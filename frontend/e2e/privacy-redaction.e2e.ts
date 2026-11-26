import { readFile } from 'node:fs/promises'
import { expect, test, type Page } from '@playwright/test'

const sourceFile = (page: Page, variant: 'scene' | 'tile' | 'picture' | 'jpeg') => page.evaluate(variant => {
  const canvas = document.createElement('canvas')
  canvas.width = variant === 'tile' || variant === 'picture' ? 12 : 320
  canvas.height = variant === 'tile' || variant === 'picture' ? 12 : 180
  const context = canvas.getContext('2d')!
  if (variant === 'tile' || variant === 'picture') {
    context.fillStyle = variant === 'tile' ? '#f00000' : '#0040ff'
    context.fillRect(variant === 'tile' ? 0 : 6, 0, 6, 12)
  } else {
    context.fillStyle = '#24af38'
    context.fillRect(0, 0, 320, 180)
    context.fillStyle = '#011022'
    for (let x = 0; x < 95; x += 4) context.fillRect(x, 0, 2, 180)
  }
  return canvas.toDataURL(variant === 'jpeg' ? 'image/jpeg' : 'image/png').split(',')[1]
}, variant).then(value => Buffer.from(value, 'base64'))

async function open(page: Page) {
  const requests: string[] = []
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    if (route.request().method() !== 'GET') requests.push(route.request().url())
    await route.fulfill({ status: 503, json: { detail: 'Backend offline for this test' } })
  })
  await page.goto('/#/privacy')
  await expect(page.getByRole('heading', { name: '隐私打码' })).toBeVisible()
  return requests
}

async function drag(
  page: Page, from: [number, number], to: [number, number], width = 320, height = 180,
) {
  const layer = page.getByLabel('打码画布').locator('.konvajs-content')
  await expect(layer).toBeVisible()
  const bounds = (await layer.boundingBox())!
  const scale = 0.9 * Math.min(bounds.width / width, bounds.height / height)
  const x = (value: number) => bounds.x + (bounds.width - width * scale) / 2 + value * scale
  const y = (value: number) => bounds.y + (bounds.height - height * scale) / 2 + value * scale
  await page.mouse.move(x(from[0]), y(from[1]))
  await page.mouse.down()
  await page.mouse.move(x(to[0]), y(to[1]), { steps: 8 })
  await page.mouse.up()
}

async function download(page: Page) {
  const preview = page.getByAltText('最终导出预览')
  await expect(preview).toBeVisible()
  const [file] = await Promise.all([
    page.waitForEvent('download'), page.getByRole('button', { name: '下载图片' }).click(),
  ])
  await expect(page.getByRole('dialog')).toBeHidden()
  return { bytes: await readFile(await file.path()), name: file.suggestedFilename() }
}

function pixels(page: Page, bytes: Buffer, type: string, points: [number, number][]) {
  return page.evaluate(async ({ encoded, type, points }) => {
    const image = new Image()
    image.src = `data:${type};base64,${encoded}`
    await image.decode()
    const canvas = document.createElement('canvas')
    canvas.width = image.naturalWidth
    canvas.height = image.naturalHeight
    const context = canvas.getContext('2d')!
    context.drawImage(image, 0, 0)
    return { size: [canvas.width, canvas.height], samples: points.map(([x, y]) => Array.from(context.getImageData(x, y, 1, 1).data)) }
  }, { encoded: bytes.toString('base64'), type, points })
}

test('mosaic, tiled material and single cover export as independent opaque regions', async ({ page }) => {
  const sent = await open(page)
  await page.getByLabel('打码原图文件').setInputFiles({
    name: 'photo.png', mimeType: 'image/png', buffer: await sourceFile(page, 'scene'),
  })
  await drag(page, [12, 20], [83, 100])
  await expect(page.getByRole('button', { name: /^选区 1/ })).toBeVisible()
  await page.getByLabel('方块大小', { exact: true }).fill('32')

  await drag(page, [110, 20], [193, 100])
  await page.getByLabel('打码样式').selectOption('tile')
  await page.getByLabel('每块大小').fill('20')
  await page.getByLabel('打码素材文件').setInputFiles({
    name: 'pattern.png', mimeType: 'image/png', buffer: await sourceFile(page, 'tile'),
  })
  await expect(page.getByRole('button', { name: '使用素材 pattern.png' })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: '移动平铺起点' }).click()
  await drag(page, [140, 50], [150, 51])
  await expect(page.getByRole('button', { name: '重置位置' })).toBeVisible()
  await page.getByRole('button', { name: '重置位置' }).click()

  await page.getByRole('button', { name: '框选', exact: true }).click()
  await drag(page, [215, 20], [295, 100])
  await page.getByLabel('打码样式').selectOption('picture')
  await page.getByLabel('图片适配').selectOption('contain')
  await page.getByLabel('打码素材文件').setInputFiles({
    name: 'cover.png', mimeType: 'image/png', buffer: await sourceFile(page, 'picture'),
  })
  await expect(page.getByRole('button', { name: '使用素材 cover.png' })).toHaveAttribute('aria-pressed', 'true')
  await page.getByLabel('素材缩放').fill('80')
  await page.getByRole('button', { name: '撤销', exact: true }).click()
  await expect(page.getByLabel('素材缩放')).toHaveValue('100')
  await page.getByRole('button', { name: '重做', exact: true }).click()
  await expect(page.getByLabel('素材缩放')).toHaveValue('80')

  await page.getByRole('button', { name: /^选区 1/ }).click()
  await expect(page.getByLabel('方块大小', { exact: true })).toHaveValue('32')
  await page.getByRole('button', { name: /^选区 2/ }).click()
  await expect(page.getByLabel('每块大小')).toHaveValue('20')
  await expect(page.getByRole('button', { name: '使用素材 pattern.png' })).toHaveAttribute('aria-pressed', 'true')

  await page.getByRole('button', { name: '导出', exact: true }).click()
  const result = await download(page)
  expect(result.name).toBe('photo-redacted.png')
  const { size, samples } = await pixels(page, result.bytes, 'image/png', [
    [21, 40], [22, 40], [114, 38], [124, 38], [134, 38], [220, 55], [272, 55], [310, 55],
  ])
  expect(size).toEqual([320, 180])
  expect(samples[0]).toEqual(samples[1])
  expect(samples.slice(2)).toEqual([
    [240, 0, 0, 255], [0, 0, 0, 255], [240, 0, 0, 255], [0, 0, 0, 255], [0, 64, 255, 255], [36, 175, 56, 255],
  ])
  expect(sent).toEqual([])
})

test('brush and eraser only alter their own region, and rectangles can move and resize', async ({ page }) => {
  await open(page)
  await page.getByLabel('打码原图文件').setInputFiles({
    name: 'brush.png', mimeType: 'image/png', buffer: await sourceFile(page, 'scene'),
  })
  await drag(page, [110, 25], [150, 60])
  await page.getByLabel('打码样式').selectOption('solid')
  await page.getByRole('button', { name: '调整选区', exact: true }).click()
  await drag(page, [130, 44], [190, 44])
  await drag(page, [210, 60], [224, 73])
  await page.getByRole('button', { name: '新建区域' }).click()
  await page.getByLabel('打码样式').selectOption('solid')
  await page.getByRole('button', { name: '画笔', exact: true }).click()
  await drag(page, [182, 42], [264, 42])
  await page.getByRole('button', { name: '橡皮擦', exact: true }).click()
  await drag(page, [195, 40], [238, 40])
  await page.getByRole('button', { name: '导出', exact: true }).click()
  const result = await download(page)
  const { samples } = await pixels(page, result.bytes, 'image/png', [[129, 43], [192, 43], [218, 68], [237, 43], [256, 43]])
  expect(samples).toEqual([[36, 175, 56, 255], [0, 0, 0, 255], [0, 0, 0, 255], [36, 175, 56, 255], [0, 0, 0, 255]])
})

test('rotated JPEG keeps the displayed orientation and both exports discard original metadata', async ({ page }) => {
  await open(page)
  const jpeg = await sourceFile(page, 'jpeg')
  function segment(marker: number, body: Buffer) {
    const header = Buffer.from([255, marker, 0, 0])
    header.writeUInt16BE(body.length + 2, 2)
    return Buffer.concat([header, body])
  }
  const exifOrientation = Buffer.from([
    0x45, 0x78, 0x69, 0x66, 0, 0, 0x49, 0x49, 42, 0, 8, 0, 0, 0,
    1, 0, 0x12, 1, 3, 0, 1, 0, 0, 0, 6, 0, 0, 0, 0, 0, 0, 0,
  ])
  const original = Buffer.concat([
    jpeg.subarray(0, 2),
    segment(225, exifOrientation),
    segment(225, Buffer.from('http://ns.adobe.com/xap/1.0/\0PRIVATE_LOCATION_MARKER')),
    segment(237, Buffer.from('Photoshop 3.0\0PRIVATE_IPTC_MARKER')),
    segment(254, Buffer.from('PRIVATE_COMMENT_MARKER')),
    jpeg.subarray(2),
  ])
  await page.getByLabel('打码原图文件').setInputFiles({ name: 'location.jpg', mimeType: 'image/jpeg', buffer: original })
  await expect(page.getByLabel('打码画布').locator('canvas').first()).toBeVisible()
  await page.getByRole('button', { name: '导出', exact: true }).click()
  await expect(page.getByRole('button', { name: '下载图片' })).toBeDisabled()
  await page.getByLabel('仅清除元数据').check()
  const clean = await download(page)
  expect(clean.name).toBe('location-clean.png')
  expect((await pixels(page, clean.bytes, 'image/png', [])).size).toEqual([180, 320])

  await drag(page, [30, 200], [70, 250], 180, 320)
  await page.getByLabel('打码样式').selectOption('solid')
  await page.getByRole('button', { name: '导出', exact: true }).click()
  await page.getByLabel('导出格式').selectOption('image/jpeg')
  const redacted = await download(page)
  expect(redacted.name).toBe('location-redacted.jpg')
  const result = await pixels(page, redacted.bytes, 'image/jpeg', [[50, 225], [90, 225]])
  expect(result.size).toEqual([180, 320])
  expect(Math.max(...result.samples[0].slice(0, 3))).toBeLessThan(10)
  expect(result.samples[1][1]).toBeGreaterThan(125)
  for (const { bytes } of [clean, redacted]) {
    for (const marker of ['Exif\0\0', 'eXIf', 'PRIVATE_LOCATION_MARKER', 'PRIVATE_IPTC_MARKER', 'PRIVATE_COMMENT_MARKER']) {
      expect(bytes.includes(Buffer.from(marker))).toBe(false)
    }
  }
})
