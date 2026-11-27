import { expect, test, type Page } from '@playwright/test'

async function photo(page: Page) {
  const png = await page.evaluate(() => {
    const canvas = document.createElement('canvas')
    canvas.width = 64
    canvas.height = 32
    return canvas.toDataURL('image/png').split(',')[1]
  })
  return { name: 'photo.png', mimeType: 'image/png', buffer: Buffer.from(png, 'base64') }
}

for (const editor of [
  { page: 'watermark', open: '打开图片', replaceTitle: '打开图片' },
  { page: 'upscale', open: '打开图片', replaceButton: '更换图片' },
]) {
  test(`${editor.page} can choose a local file again after loading the first one`, async ({ page }) => {
    await page.goto(`/#/${editor.page}`)
    await page.getByRole('button', { name: editor.open, exact: true }).click()
    const [first] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.getByRole('button', { name: '从本机选择', exact: true }).click(),
    ])
    const image = await photo(page)
    await first.setFiles(image)

    const replace = editor.replaceTitle
      ? page.getByTitle(editor.replaceTitle, { exact: true })
      : page.getByRole('button', { name: editor.replaceButton, exact: true })
    await replace.click()
    const [second] = await Promise.all([
      page.waitForEvent('filechooser', { timeout: 5000 }),
      page.getByRole('button', { name: '从本机选择', exact: true }).click(),
    ])
    await second.setFiles({ ...image, name: 'replacement.png' })
  })
}

for (const editor of ['watermark', 'cutout', 'upscale']) {
  test(`${editor} keeps the last selection when the earlier file finishes late`, async ({ page }) => {
    await page.addInitScript(() => {
      const read = FileReader.prototype.readAsDataURL
      FileReader.prototype.readAsDataURL = function (blob) {
        if (!(blob instanceof File) || blob.name !== 'first.png') return read.call(this, blob)
        window.addEventListener('release-first-read', () => {
          this.addEventListener('loadend', () => window.dispatchEvent(new Event('first-read-done')), { once: true })
          read.call(this, blob)
        }, { once: true })
      }
    })
    await page.goto('/#/' + editor)
    const file = await photo(page)
    const input = page.locator('input[type="file"]').first()
    const sourceRevision = () => page.evaluate(async editor => {
      if (editor === 'cutout') {
        const { useCutoutStore } = await import('/src/stores/useCutoutStore.ts')
        return useCutoutStore.getState().sourceRevision
      }
      const { useImageStore, useUpscaleImageStore } = await import('/src/stores/useImageStore.ts')
      return (editor === 'upscale' ? useUpscaleImageStore : useImageStore).getState().sourceRevision
    }, editor)
    const initialRevision = await sourceRevision()
    await input.setInputFiles({ ...file, name: 'first.png' })
    await input.setInputFiles({ ...file, name: 'last.png' })
    await expect.poll(sourceRevision).toBe(initialRevision + 1)
    await page.evaluate(() => new Promise<void>(resolve => {
      window.addEventListener('first-read-done', () => resolve(), { once: true })
      window.dispatchEvent(new Event('release-first-read'))
    }))
    expect(await sourceRevision()).toBe(initialRevision + 1)
  })
}

test('Immich save uses the capture time from the original response', async ({ page }) => {
  const assetId = '5117607e-8edb-467e-913a-9c308cb62562'
  const displayTime = '2024-01-02T01:00:00.000Z'
  const captureTime = '2024-01-01T17:00:00+00:00'
  await page.addInitScript(() => {
    sessionStorage.setItem('hiimage:immich-access', JSON.stringify({ access: 'test-token', accessBackend: '' }))
  })
  await page.goto('/#/watermark')
  const image = await photo(page)
  await page.route('**/api/immich/connection', route => route.fulfill({
    json: { configured: true, mode: 'upload', url: 'http://immich.test', generation: 'test' },
  }))
  await page.route('**/api/immich/albums', route => route.fulfill({ json: [] }))
  await page.route('**/api/immich/assets?*', route => route.fulfill({
    json: { next: null, items: [{ id: assetId, name: image.name, taken: displayTime }] },
  }))
  await page.route(`**/api/immich/assets/${assetId}/thumbnail?*`, route => route.fulfill({
    body: image.buffer, contentType: image.mimeType,
  }))
  await page.route(`**/api/immich/assets/${assetId}/original`, route => route.fulfill({
    body: image.buffer,
    contentType: image.mimeType,
    headers: { 'X-Immich-Source': 'test-source', 'X-Immich-Taken-At': captureTime },
  }))
  await page.route('**/api/immich/save', route => route.fulfill({
    json: { state: 'created', url: 'http://immich.test/photos/result' },
  }))

  await page.getByRole('button', { name: '从 Immich 选择', exact: true }).click()
  await page.getByTitle(image.name, { exact: true }).click()
  await page.getByRole('button', { name: '导入 1 张', exact: true }).click()
  await expect(page.getByText('Immich · photo.png', { exact: true })).toBeVisible()
  await page.evaluate(async () => {
    const { useImageStore } = await import(/* @vite-ignore */ '/src/stores/useImageStore.ts')
    const editor = useImageStore.getState()
    editor.setResultImage(editor.sourceImage)
  })

  await page.getByRole('button', { name: '保存到 Immich', exact: true }).click()
  const [request] = await Promise.all([
    page.waitForRequest('**/api/immich/save'),
    page.getByRole('button', { name: '保存为新图片', exact: true }).click(),
  ])
  expect(request.postData()).toContain(captureTime)
  expect(request.postData()).not.toContain(displayTime)
  await expect(page.getByRole('status').filter({ hasText: '图片已保存' })).toBeVisible()
})
