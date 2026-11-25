import { expect, test } from '@playwright/test'

test('image results are shared across workbenches and can be restored or removed', async ({ page }) => {
  const model = (id: string) => ({ id, name: id, description: '', scale: 2, outscale: 2 })
  const group = (id: string) => ({ default_model: id, groups: [{ label: '测试模型', models: [model(id)] }] })
  const cutout = group('birefnet')
  const replies: Record<string, object> = {
    '/api/settings': { device: 'cpu' },
    '/api/models/groups': { inpaint: group('wm_lama'), upscale: group('RealESRGAN_x4plus'), cutout },
    '/api/models/list': { models: [] },
    '/api/cutout/models': cutout,
    '/api/devices': { devices: [{ id: 'cpu', label: 'CPU', desc: 'CPU', available: true }] },
    '/api/health': { status: 'ok' },
    '/api/prompt-edit/status': { available: true, reason: null, model: 'FLUX.2 Klein 4B' },
  }
  const outputEndpoints = new Set([
    '/api/prompt-edit', '/api/inpaint/with-mask', '/api/pipeline/run', '/api/upscale',
    '/api/cutout', '/api/cutout/refine', '/api/cutout/composite',
  ])
  let png = ''
  const sentImages: Record<string, string> = {}
  await page.routeWebSocket(url => new URL(url).pathname === '/api/ws/progress', () => {})
  await page.route(url => url.pathname.startsWith('/api/'), route => {
    const path = new URL(route.request().url()).pathname
    if (outputEndpoints.has(path)) {
      sentImages[path] = route.request().postDataJSON().image ?? ''
      return route.fulfill({ json: { image: png, mask: png, width: 128, height: 96 } })
    }
    return route.fulfill({ json: replies[path] ?? {} })
  })
  await page.goto('/#/prompt-edit')
  png = await page.evaluate(() => {
    const canvas = document.createElement('canvas')
    canvas.width = 128
    canvas.height = 96
    const context = canvas.getContext('2d')!
    context.fillStyle = '#3670a2'
    context.fillRect(10, 10, 80, 55)
    return canvas.toDataURL().split(',')[1]
  })
  const dataUrl = 'data:image/png;base64,' + png
  const open = page.getByRole('button', { name: '历史记录', exact: true })
  const dialog = page.getByRole('dialog', { name: '历史记录', exact: true })
  const rows = dialog.getByRole('listitem')
  const row = (label: string) => rows.filter({ hasText: label + ' ·' })
  const restore = async (label: string, batch = false) => {
    await open.click()
    await row(label).getByRole('button', { name: batch ? '加入批量队列' : '设为原图', exact: true }).click()
    await expect(dialog).toBeHidden()
  }

  await open.click()
  await expect(dialog.getByText('暂无历史图片。处理成功的图片会自动记录。')).toBeVisible()
  await page.keyboard.press('Escape')
  await page.getByLabel('编辑原图文件').setInputFiles({ name: 'history-test.png', mimeType: 'image/png', buffer: Buffer.from(png, 'base64') })
  await page.getByLabel('修改指令', { exact: true }).fill('修改背景')
  await page.getByRole('button', { name: '生成编辑结果', exact: true }).click()
  await expect(page.getByRole('img', { name: '编辑结果' })).toBeVisible()

  await page.goto('/#/watermark')
  await restore('提示词编辑')
  await expect(page.getByText('128 x 96', { exact: true })).toBeVisible()
  const canvas = page.locator('.konvajs-content canvas').first()
  await expect(canvas).toBeVisible()
  const bounds = (await canvas.boundingBox())!
  const centerX = bounds.x + bounds.width / 2
  const centerY = bounds.y + bounds.height / 2
  await page.mouse.move(centerX - 50, centerY - 40)
  await page.mouse.down()
  await page.mouse.move(centerX + 20, centerY + 15, { steps: 5 })
  await page.mouse.up()
  await expect(page.getByRole('button', { name: '去除水印', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '去除水印', exact: true }).click()
  await expect.poll(() => sentImages['/api/inpaint/with-mask'] ?? sentImages['/api/pipeline/run']).toBe(dataUrl)
  await open.click()
  await expect(row('去水印')).toBeVisible()
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()

  await page.goto('/#/cutout')
  await restore('去水印')
  await page.getByRole('button', { name: '开始抠图', exact: true }).click()
  await page.getByRole('button', { name: '应用精修', exact: true }).click()
  await expect(page.getByRole('button', { name: '应用精修', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '合成', exact: true }).click()
  await open.click()
  await expect(rows).toHaveCount(5)
  for (const label of ['抠图', '抠图精修', '背景合成']) await expect(row(label)).toBeVisible()
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()

  await page.goto('/#/upscale')
  await restore('背景合成')
  await page.getByRole('button', { name: '开始超分', exact: true }).click()
  await page.getByRole('button', { name: '批量', exact: true }).click()
  await restore('单图超分', true)
  await page.getByRole('button', { name: '开始批量处理 (1 张)', exact: true }).click()
  await open.click()
  await expect(rows).toHaveCount(7)
  await expect(row('批量超分')).toBeVisible()

  await page.reload()
  await open.click()
  await expect(rows).toHaveCount(7)
  await row('提示词编辑').scrollIntoViewIfNeeded()
  const thumbnail = row('提示词编辑').getByRole('img', { name: 'history-test.png', exact: true })
  await expect(thumbnail).toBeVisible()
  await thumbnail.evaluate(image => (image as HTMLImageElement).decode())
  await row('提示词编辑').getByRole('button', { name: '删除', exact: true }).click()
  await expect(rows).toHaveCount(6)
  await page.goto('/#/prompt-edit')
  await restore('批量超分')
  const original = page.getByRole('img', { name: '编辑原图', exact: true })
  await expect(original).toBeVisible()
  expect(await original.evaluate(async node => {
    const response = await fetch((node as HTMLImageElement).src)
    const blob = await response.blob()
    return await new Promise<string>(resolve => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.readAsDataURL(blob)
    })
  })).toBe(dataUrl)
  await open.click()
  await expect(rows).toHaveCount(6)
  await dialog.getByRole('button', { name: '清空历史记录', exact: true }).click()
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  await expect(rows).toHaveCount(6)
  await dialog.getByRole('button', { name: '清空历史记录', exact: true }).click()
  await dialog.getByRole('button', { name: '确认清空', exact: true }).click()
  await expect(rows).toHaveCount(0)
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()
  await expect(original).toBeVisible()
  await page.reload()
  await open.click()
  await expect(dialog.getByText('暂无历史图片。处理成功的图片会自动记录。')).toBeVisible()
})
