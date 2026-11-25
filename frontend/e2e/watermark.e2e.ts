import { expect, test } from '@playwright/test'

test('pan tools move the image without drawing and keep the drawing tools usable', async ({ page }) => {
  const groups = { groups: [] }
  const replies: Record<string, object> = {
    '/api/settings': { device: 'cpu' },
    '/api/models/groups': { inpaint: groups, upscale: groups, cutout: groups },
    '/api/models/list': { models: [] },
    '/api/devices': { devices: [{ id: 'cpu', label: 'CPU', desc: 'CPU', available: true }] },
    '/api/health': { status: 'ok' },
  }
  await page.route(url => url.pathname.startsWith('/api/'), route =>
    route.fulfill({ json: replies[new URL(route.request().url()).pathname] ?? {} }),
  )
  await page.goto('/#/watermark')
  await expect(page.getByText('拖拽图片到此处，或点击打开文件')).toBeVisible()
  const png = await page.evaluate(() => {
    const image = document.createElement('canvas')
    image.width = 640
    image.height = 400
    const ctx = image.getContext('2d')!
    for (let x = 0; x < image.width; x += 32) {
      ctx.fillStyle = `hsl(${x / 2} 90% 45%)`
      ctx.fillRect(x, 0, 32, image.height)
    }
    return image.toDataURL().split(',')[1]
  })
  await page.locator('input[type=file]').first().setInputFiles({
    name: 'pan-test.png', mimeType: 'image/png', buffer: Buffer.from(png, 'base64'),
  })
  await expect(page.getByText('640 x 400')).toBeVisible()

  const imageLayer = page.locator('.konvajs-content canvas').first()
  await expect(imageLayer).toBeVisible()
  const box = (await imageLayer.boundingBox())!
  const x = box.x + box.width / 2
  const y = box.y + box.height / 2
  const imagePixels = () => imageLayer.evaluate(canvas => (canvas as HTMLCanvasElement).toDataURL())
  const drag = async (dx: number, dy: number) => {
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x + dx, y + dy, { steps: 8 })
    await page.mouse.up()
  }

  await page.mouse.move(x, y)
  for (let step = 0; step < 3; step++) {
    const beforeZoom = await imagePixels()
    await page.mouse.wheel(0, -120)
    await expect.poll(imagePixels).not.toBe(beforeZoom)
  }

  const pan = page.getByTitle('平移 (Space)')
  await pan.click()
  await expect(pan).toHaveAttribute('aria-pressed', 'true')
  const beforePan = await imagePixels()
  await drag(40, 24)
  await expect.poll(imagePixels).not.toBe(beforePan)
  const emptyRegions = page.getByText('在画布上拖拽绘制区域')
  await expect(emptyRegions).toBeVisible()

  await page.keyboard.press('r')
  await expect(pan).toHaveAttribute('aria-pressed', 'false')
  const beforeSpace = await imagePixels()
  await page.keyboard.down('Space')
  await drag(-40, -24)
  await page.keyboard.up('Space')
  await expect.poll(imagePixels).not.toBe(beforeSpace)
  await expect(emptyRegions).toBeVisible()

  await drag(42, 34)
  const region = page.getByText(/^区域 1 \(/)
  await expect(region).toBeVisible()
  await page.getByTitle('画笔模式 (B)').click()
  await expect(page.getByTitle(/魔术棒/)).toHaveCount(0)
  await page.keyboard.press('w')
  await drag(48, -12)
  await expect(page.getByText('1 笔', { exact: true })).toBeVisible()

  await page.getByTitle('橡皮擦 (E)').click()
  await drag(28, -9)
  await expect(page.getByText(/已绘制 1 笔画笔，\s*1 笔橡皮擦/)).toBeVisible()
  await page.keyboard.press('Control+z')
  await expect(page.getByText('1 笔', { exact: true })).toBeVisible()
  await page.getByTitle('撤销 (Ctrl+Z)').click()
  await expect(page.getByText('画笔遮罩', { exact: true })).toHaveCount(0)
  await expect(region).toBeVisible()
})

test('real worker exports editable rectangles, single clicks and an opaque black background', async ({ page }) => {
  await page.goto('/#/watermark')
  const pixels = await page.evaluate(async () => {
    // Vite 提供真实应用模块；同时执行真实浏览器 OffscreenCanvas worker。
    const { exportCombinedMask } = await import(/* @vite-ignore */ '/src/services/maskExport.ts')
    const makeStroke = (tool: string, points: number[], shape = 'circle') => ({
      id: tool, points, size: 6, hardness: 100, shape, tool, timestamp: 1,
    })
    const url = await exportCombinedMask([
      makeStroke('eraser', [10, 10]),
      makeStroke('brush', [25, 25], 'square'),
    ], [{ x1: 4, y1: 4, x2: 18, y2: 18 }], 32, 32)
    const image = new Image()
    image.src = url
    await image.decode()
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 32
    const ctx = canvas.getContext('2d')!
    ctx.drawImage(image, 0, 0)
    return [[0, 0], [5, 5], [10, 10], [25, 25], [23, 23]].map(([x, y]) =>
      Array.from(ctx.getImageData(x, y, 1, 1).data))
  })
  expect(pixels).toEqual([
    [0, 0, 0, 255], [255, 255, 255, 255], [0, 0, 0, 255],
    [255, 255, 255, 255], [255, 255, 255, 255],
  ])
})

test('polygon candidates do not fill the rest of their bounding rectangle', async ({ page }) => {
  await page.goto('/#/watermark')
  const pixels = await page.evaluate(async () => {
    const { exportCombinedMask } = await import(/* @vite-ignore */ '/src/services/maskExport.ts')
    const url = await exportCombinedMask([], [{
      x1: 2, y1: 2, x2: 30, y2: 30,
      polygon: [[16, 2], [30, 16], [16, 30], [2, 16]],
    }], 32, 32)
    const image = new Image()
    image.src = url
    await image.decode()
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 32
    const ctx = canvas.getContext('2d')!
    ctx.drawImage(image, 0, 0)
    return [[3, 3], [16, 16]].map(([x, y]) => Array.from(ctx.getImageData(x, y, 1, 1).data))
  })
  expect(pixels).toEqual([[0, 0, 0, 255], [255, 255, 255, 255]])
})
