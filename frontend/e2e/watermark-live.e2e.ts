import { expect, test } from '@playwright/test'

test('live OCR → confirm → LaMa → comparison → download', async ({ page }) => {
  test.skip(process.env.HIIMAGE_LIVE !== '1', '需要已启动的后端和已下载的 LaMa 模型')
  test.setTimeout(120_000)
  page.on('pageerror', error => console.log('pageerror:', error.message))
  page.on('requestfailed', request => console.log('request failed:', request.url(), request.failure()))
  await page.goto('/#/watermark')
  const fixture = await page.evaluate(() => {
    const canvas = document.createElement('canvas')
    canvas.width = 640
    canvas.height = 320
    const ctx = canvas.getContext('2d')!
    ctx.fillStyle = 'rgb(80,130,170)'
    ctx.fillRect(0, 0, 640, 320)
    ctx.fillStyle = 'white'
    ctx.font = '28px Arial'
    ctx.fillText('WATERMARK', 400, 270)
    return canvas.toDataURL().split(',')[1]
  })
  await page.locator('input[type=file]').first().setInputFiles({
    name: 'watermark.png', mimeType: 'image/png', buffer: Buffer.from(fixture, 'base64'),
  })
  await expect(page.getByText('640 x 320')).toBeVisible()
  const detection = page.waitForResponse(response => response.url().endsWith('/api/detect'), { timeout: 60_000 })
  await page.getByRole('button', { name: '自动检测' }).click()
  const detected = await detection
  expect(detected.ok()).toBeTruthy()
  expect((await detected.json()).engine).toBe('paddleocr-onnx')
  const candidate = page.getByRole('checkbox', { name: /候选.*WATERMARK/i }).first()
  await expect(candidate).not.toBeChecked()
  await expect(page.getByRole('button', { name: '去除水印', exact: true })).toBeDisabled()
  await candidate.check()
  const processing = page.waitForResponse(response => response.url().endsWith('/api/inpaint/with-mask'))
  await page.getByRole('button', { name: '去除水印', exact: true }).click()
  const processed = await processing
  expect(processed.ok(), await processed.text()).toBeTruthy()
  await expect(page.getByRole('button', { name: '返回编辑', exact: true })).toBeVisible()
  await page.screenshot({ path: 'test-results/watermark-live-comparison.png', fullPage: true })
  const downloaded = page.waitForEvent('download')
  await page.getByRole('button', { name: '保存结果', exact: true }).click()
  const download = await downloaded
  expect(download.suggestedFilename()).toBe('result_processed.png')
  expect(await download.failure()).toBeNull()
})
