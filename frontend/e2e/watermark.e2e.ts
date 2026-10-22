import { expect, test } from '@playwright/test'

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
