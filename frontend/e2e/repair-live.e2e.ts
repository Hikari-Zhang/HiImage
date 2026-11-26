import { expect, test, type Page } from '@playwright/test'
import { readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const width = 320
const height = 180

async function fixture(page: Page) {
  return page.evaluate(({ width, height }) => {
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const drawing = canvas.getContext('2d')!
    drawing.fillStyle = '#729ba4'
    drawing.fillRect(0, 0, width, height)
    drawing.clearRect(0, 0, 20, 20)
    drawing.fillStyle = 'rgba(220, 120, 30, 0.5)'
    drawing.fillRect(0, 0, 12, 12)
    drawing.fillStyle = '#d51734'
    drawing.fillRect(80, 76, 12, 12)
    drawing.fillRect(224, 84, 14, 14)
    return canvas.toDataURL()
  }, { width, height })
}

async function mark(page: Page, start: [number, number], end = start) {
  const layer = page.getByLabel('修复画布').locator('.konvajs-content')
  await expect(layer).toBeVisible()
  const bounds = (await layer.boundingBox())!
  const scale = 0.9 * Math.min(bounds.width / width, bounds.height / height)
  const x = (value: number) => bounds.x + (bounds.width - width * scale) / 2 + value * scale
  const y = (value: number) => bounds.y + (bounds.height - height * scale) / 2 + value * scale
  await page.mouse.move(x(start[0]), y(start[1]))
  await page.mouse.down()
  if (end !== start) await page.mouse.move(x(end[0]), y(end[1]), { steps: 8 })
  await page.mouse.up()
}

async function repair(page: Page, timeout = 30_000) {
  const start = Date.now()
  const [response] = await Promise.all([
    page.waitForResponse(value => value.url().endsWith('/api/inpaint/with-mask') && value.request().method() === 'POST', { timeout }),
    page.getByRole('button', { name: '修复选区' }).click(),
  ])
  expect(response.ok(), await response.text()).toBeTruthy()
  const request = response.request().postDataJSON()
  const { image } = await response.json()
  await expect(page.getByRole('button', { name: '修复后' })).toHaveAttribute('aria-pressed', 'true')
  return { request, url: `data:image/png;base64,${image}`, elapsedMs: Date.now() - start }
}

function pixels(page: Page, url: string) {
  return page.evaluate(async src => {
    const image = new Image()
    image.src = src
    await image.decode()
    const canvas = document.createElement('canvas')
    canvas.width = image.naturalWidth
    canvas.height = image.naturalHeight
    const drawing = canvas.getContext('2d')!
    drawing.drawImage(image, 0, 0)
    const points = [[6, 6], [86, 82], [230, 90], [145, 145]]
    return {
      size: [canvas.width, canvas.height],
      samples: points.map(([x, y]) => Array.from(drawing.getImageData(x, y, 1, 1).data)),
    }
  }, url)
}

test('LaMa repairs a selection, keeps it when adjusting, and uses the applied image in the next round', async ({ page }) => {
  test.skip(process.env.HIIMAGE_LIVE !== '1', '需要已启动的后端和 LaMa 权重')
  test.setTimeout(150_000)
  await page.goto('/#/repair')
  await expect(page.getByRole('heading', { name: '瑕疵修复' })).toBeVisible()
  const source = await fixture(page)
  await page.getByLabel('修复原图文件').setInputFiles({
    name: 'blemishes.png', mimeType: 'image/png', buffer: Buffer.from(source.split(',')[1], 'base64'),
  })
  const submit = page.getByRole('button', { name: '修复选区' })
  await expect(submit).toBeDisabled()
  await expect(page.getByRole('button', { name: '画笔', exact: true })).toBeVisible()
  await page.getByLabel('选区外扩', { exact: true }).fill('0')

  await mark(page, [86, 82])
  const first = await repair(page)
  expect(first.request).toMatchObject({ model: 'wm_lama', dilation: 0, preserve_alpha: true })
  const original = await pixels(page, source)
  const repaired = await pixels(page, first.url)
  expect(repaired.size).toEqual([width, height])
  expect(repaired.samples[0]).toEqual(original.samples[0])
  expect(repaired.samples[1]).not.toEqual(original.samples[1])
  expect(repaired.samples[2]).toEqual(original.samples[2])
  expect(repaired.samples[3]).toEqual(original.samples[3])
  await page.getByRole('button', { name: '修复前' }).click()
  await expect(page.getByRole('button', { name: '修复前' })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: '返回调整' }).click()
  await expect(submit).toBeEnabled()
  await page.getByLabel('选区外扩', { exact: true }).fill('4')
  const retry = await repair(page)
  expect(retry.request).toMatchObject({ image: first.request.image, mask: first.request.mask, dilation: 4 })

  await page.getByRole('button', { name: '应用并继续' }).click()
  await expect(submit).toBeDisabled()
  await expect(page.getByRole('button', { name: '撤回上次修复' })).toBeEnabled()
  const originalButton = page.getByRole('button', { name: '查看最初原图' })
  await originalButton.click()
  await expect(originalButton).toHaveAttribute('aria-pressed', 'true')
  await originalButton.click()
  await page.getByRole('button', { name: '矩形', exact: true }).click()
  await mark(page, [217, 77], [245, 104])
  const second = await repair(page)
  expect(second.request.image).toBe(retry.url)
  expect(second.request.mask).not.toBe(first.request.mask)
  const final = await pixels(page, second.url)
  const applied = await pixels(page, retry.url)
  expect(final.samples[0]).toEqual(original.samples[0])
  expect(final.samples[1]).toEqual(applied.samples[1])
  expect(final.samples[2]).not.toEqual(original.samples[2])
  expect(final.samples[3]).toEqual(original.samples[3])
  const [download] = await Promise.all([
    page.waitForEvent('download'), page.getByRole('button', { name: '下载结果' }).click(),
  ])
  expect(download.suggestedFilename()).toBe('blemishes-repaired.png')
  expect(await download.failure()).toBeNull()
})

test('LaMa and FLUX Fill repair the same photo and selection', async ({ page }, testInfo) => {
  test.skip(process.env.HIIMAGE_LIVE_FLUX !== '1', '需要已启动的 CUDA 后端和两款模型的权重')
  test.setTimeout(350_000)
  await page.goto('/#/repair')
  const photo = await readFile(resolve(__dirname, '../../scripts/fixtures/watermark_quality/coffee.png'), 'base64')
  const { clean, damaged } = await page.evaluate(async ({ photo, width, height }) => {
    const source = new Image()
    source.src = `data:image/png;base64,${photo}`
    await source.decode()
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const drawing = canvas.getContext('2d')!
    drawing.drawImage(source, 0, 16, source.width, source.width * height / width, 0, 0, width, height)
    const clean = canvas.toDataURL()
    drawing.strokeStyle = '#f2e3d4'
    drawing.lineWidth = 4
    drawing.lineCap = 'round'
    drawing.beginPath()
    drawing.moveTo(274, 60)
    drawing.lineTo(291, 83)
    drawing.stroke()
    return { clean, damaged: canvas.toDataURL() }
  }, { photo, width, height })

  const select = page.getByLabel('修复模型')
  const promptField = page.getByRole('textbox', { name: '重绘提示词' })
  await expect(select).toHaveValue('wm_lama')
  await expect(promptField).toHaveCount(0)
  await page.getByLabel('修复原图文件').setInputFiles({
    name: 'coffee-scratch.png', mimeType: 'image/png', buffer: Buffer.from(damaged.split(',')[1], 'base64'),
  })
  await expect(page.getByRole('button', { name: '画笔', exact: true })).toBeVisible()
  await page.getByLabel('画笔大小', { exact: true }).fill('18')
  await mark(page, [274, 60], [291, 83])
  const lama = await repair(page)
  expect(lama.request).toMatchObject({ model: 'wm_lama', device: 'cpu', dilation: 2, preserve_alpha: true })
  expect(lama.request).not.toHaveProperty('redraw')
  for (const [name, dataUrl] of Object.entries({ clean, damaged, mask: lama.request.mask as string, lama: lama.url })) {
    await writeFile(testInfo.outputPath(`${name}.png`), Buffer.from(dataUrl.split(',')[1], 'base64'))
  }

  await page.getByRole('button', { name: '返回调整' }).click()
  await select.selectOption('flux_fill')
  const prompt = 'Continue the surrounding brown wooden tabletop across the selection. Match the adjacent diagonal fine wood grain, color and lighting.'
  await promptField.fill(prompt)
  await select.selectOption('wm_lama')
  await expect(promptField).toHaveCount(0)
  await select.selectOption('flux_fill')
  await expect(promptField).toHaveValue(prompt)
  const flux = await repair(page, 300_000)
  expect(flux.request).toMatchObject({
    model: 'flux_fill', device: 'cuda', image: lama.request.image, mask: lama.request.mask,
    dilation: lama.request.dilation, preserve_alpha: true, redraw: { prompt },
  })
  expect(flux.url).not.toBe(lama.url)
  await writeFile(testInfo.outputPath('flux.png'), Buffer.from(flux.url.split(',')[1], 'base64'))
  await writeFile(testInfo.outputPath('timings.json'), JSON.stringify({ lamaMs: lama.elapsedMs, fluxMs: flux.elapsedMs, prompt }, null, 2))
})
