import { expect, test, type Page, type Route } from '@playwright/test'

type EditRequest = { task_id: string; seed: number; image: string; prompt: string; model: string }

async function setup(page: Page, respond: (route: Route, index: number, image: string) => Promise<void>, outputCount = 3) {
  const requests: EditRequest[] = []
  let images: string[] = []
  await page.routeWebSocket(url => new URL(url).pathname === '/api/ws/progress', () => {})
  await page.route(url => url.pathname.startsWith('/api/'), route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/prompt-edit') {
      requests.push(route.request().postDataJSON())
      const index = requests.length - 1
      return respond(route, index, images[index])
    }
    const model = { id: 'flux2-klein-4b', name: 'FLUX.2 Klein 4B', available: true, reason: null }
    const replies: Record<string, object> = {
      '/api/settings': { device: 'cpu' },
      '/api/models/groups': { inpaint: { groups: [] }, upscale: { groups: [] }, cutout: { groups: [] } },
      '/api/prompt-edit/status': { available: true, model: model.name, default_model: model.id, models: [model] },
      '/api/health': { status: 'ok' },
    }
    return route.fulfill({ json: replies[path] ?? {} })
  })
  await page.goto('/#/prompt-edit')
  const open = page.getByRole('button', { name: '历史记录', exact: true })
  await expect(open).toBeVisible()
  const [source, ...outputs] = await page.evaluate(count => {
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 64
    const ctx = canvas.getContext('2d')!
    const colors = ['#777', ...Array.from({ length: count }, (_, index) => `rgb(${index}, 120, 180)`)]
    return colors.map(color => {
      ctx.fillStyle = color
      ctx.fillRect(0, 0, 64, 64)
      return canvas.toDataURL('image/png').split(',')[1]
    })
  }, outputCount)
  images = outputs
  await page.getByLabel('编辑原图文件').setInputFiles({
    name: 'portrait.png', mimeType: 'image/png', buffer: Buffer.from(source, 'base64'),
  })
  await page.getByLabel('修改指令', { exact: true }).fill('换成摄影棚背景')
  return { requests, images, source, open, dialog: page.getByRole('dialog', { name: '历史记录', exact: true }) }
}

async function startBatch(page: Page) {
  await page.getByRole('spinbutton', { name: '生成张数', exact: true }).fill('3')
  await page.getByRole('button', { name: '生成 3 张图片', exact: true }).click()
}

test('the batch count accepts keyboard entry and requires an integer from 1 to 64', async ({ page }) => {
  const { requests } = await setup(page, route => route.fulfill({ status: 500 }))
  const count = page.getByRole('spinbutton', { name: '生成张数', exact: true })
  const single = page.getByRole('button', { name: '生成编辑结果', exact: true })
  await expect(count).toHaveValue('1')
  await expect(single).toBeEnabled()
  for (const value of ['', '0', '65', '1.5']) {
    await count.fill(value)
    await expect(count).toHaveAttribute('aria-invalid', 'true')
    await expect(single).toBeDisabled()
    await count.press('Enter')
  }
  await count.fill('')
  await count.pressSequentially('64')
  await expect(count).toHaveValue('64')
  await expect(count).toHaveAttribute('aria-invalid', 'false')
  await expect(page.getByRole('button', { name: '生成 64 张图片', exact: true })).toBeEnabled()
  expect(requests).toHaveLength(0)
})

test('all 64 variations are available for preview and saved to history', async ({ page }) => {
  const { requests, images, open, dialog } = await setup(page, (route, _index, image) =>
    route.fulfill({ json: { image, width: 64, height: 64 } }), 64)

  const count = page.getByRole('spinbutton', { name: '生成张数', exact: true })
  await count.fill('64')
  await page.getByRole('button', { name: '生成 64 张图片', exact: true }).click()
  const gallery = page.getByRole('group', { name: '批量生成结果' })
  await expect(gallery.getByRole('button')).toHaveCount(64, { timeout: 20_000 })
  await expect(count).toBeEnabled()
  expect(requests).toHaveLength(64)
  expect(new Set(requests.map(request => request.seed)).size).toBe(64)
  await gallery.getByRole('button', { name: '第 64 张', exact: true }).click()
  await expect(page.getByRole('img', { name: '编辑结果', exact: true }))
    .toHaveAttribute('src', 'data:image/png;base64,' + images[63])

  await page.reload()
  await open.click()
  const more = dialog.getByRole('button', { name: '显示更多', exact: true })
  await more.click()
  await more.click()
  const items = dialog.getByRole('listitem')
  await expect(items).toHaveCount(64)
  const expectedNames = Array.from({ length: 64 }, (_, index) => `portrait-${String(index + 1).padStart(2, '0')}.png`)
  expect((await items.locator('[title]').allTextContents()).sort()).toEqual(expectedNames)
})

test('batch generates distinct variations, survives editor navigation and persists every image', async ({ page }) => {
  let releaseSecond!: () => void
  const second = new Promise<void>(resolve => { releaseSecond = resolve })
  const { requests, images, source, open, dialog } = await setup(page, async (route, index, image) => {
    if (index === 1) await second
    await route.fulfill({ json: { image, width: 64, height: 64 } })
  })
  try {
    await startBatch(page)
    await expect.poll(() => requests.length).toBe(2)
    await expect(page.getByLabel('生成张数', { exact: true })).toBeDisabled()
    const nav = page.getByRole('navigation', { name: '主导航' })
    await nav.getByRole('button', { name: '超分辨率', exact: true }).click()
    await open.click()
    await expect(dialog.getByText('portrait-01.png', { exact: true })).toBeVisible()
    await dialog.getByRole('button', { name: '关闭', exact: true }).click()
    await nav.getByRole('button', { name: '提示词编辑', exact: true }).click()
  } finally {
    releaseSecond()
  }
  const gallery = page.getByRole('group', { name: '批量生成结果' })
  await expect(gallery.getByRole('button')).toHaveCount(3)
  await expect(page.getByLabel('生成张数', { exact: true })).toBeEnabled()
  expect(new Set(requests.map(request => request.task_id)).size).toBe(3)
  expect(new Set(requests.map(request => request.seed)).size).toBe(3)
  for (const request of requests) {
    expect(request.image).toBe('data:image/png;base64,' + source)
    expect(request.prompt).toBe('换成摄影棚背景')
    expect(request.model).toBe('flux2-klein-4b')
    expect(request.seed).toBeGreaterThanOrEqual(0)
    expect(request.seed).toBeLessThan(2 ** 31)
  }
  await gallery.getByRole('button', { name: '第 2 张', exact: true }).click()
  await expect(page.getByRole('img', { name: '编辑结果', exact: true }))
    .toHaveAttribute('src', 'data:image/png;base64,' + images[1])

  await page.reload()
  await open.click()
  for (const name of ['portrait-01.png', 'portrait-02.png', 'portrait-03.png']) {
    await expect(dialog.getByText(name, { exact: true })).toBeVisible()
  }
})

test('a failed variation keeps the earlier image and its history record', async ({ page }) => {
  const { requests, open, dialog } = await setup(page, (route, index, image) => index === 1
    ? route.fulfill({ status: 502, json: { detail: '测试模型暂不可用' } })
    : route.fulfill({ json: { image, width: 64, height: 64 } }))
  await startBatch(page)

  await expect(page.getByRole('alert').filter({ hasText: '第 2 / 3 张生成失败，已生成 1 张' })).toBeVisible()
  await expect(page.getByRole('img', { name: '编辑结果', exact: true })).toBeVisible()
  expect(requests).toHaveLength(2)
  await open.click()
  await expect(dialog.getByRole('listitem')).toHaveCount(1)
  await expect(dialog.getByText('portrait-01.png', { exact: true })).toBeVisible()
})

test('stopping a batch saves the in-flight image and skips later requests', async ({ page }) => {
  let releaseFirst!: () => void
  const first = new Promise<void>(resolve => { releaseFirst = resolve })
  const { requests, open, dialog } = await setup(page, async (route, _index, image) => {
    await first
    await route.fulfill({ json: { image, width: 64, height: 64 } })
  })
  try {
    await startBatch(page)
    await expect.poll(() => requests.length).toBe(1)
    await page.getByRole('button', { name: '停止后续生成', exact: true }).click()
    await expect(page.getByRole('button', { name: '当前图片完成后停止', exact: true })).toBeDisabled()
  } finally {
    releaseFirst()
  }
  await expect(page.getByLabel('生成张数', { exact: true })).toBeEnabled()
  expect(requests).toHaveLength(1)
  await open.click()
  await expect(dialog.getByRole('listitem')).toHaveCount(1)
  await expect(dialog.getByText('portrait-01.png', { exact: true })).toBeVisible()
})
