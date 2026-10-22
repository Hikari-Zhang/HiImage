import { afterEach, describe, expect, it, vi } from 'vitest'
import JSZip from 'jszip'
import { downloadRecoveredResult } from './recoveredResult'
import { downloadBlob } from './fileService'

vi.mock('./fileService', () => ({ downloadBlob: vi.fn(async () => {}) }))
afterEach(() => vi.clearAllMocks())
const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8ioAAAAASUVORK5CYII='

function read(blob: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as ArrayBuffer)
    reader.onerror = () => reject(reader.error)
    reader.readAsArrayBuffer(blob)
  })
}

describe('recovered image download', () => {
  it('downloads one image as PNG without restoring editor state', async () => {
    await downloadRecoveredResult({ image: png, width: 1, height: 1 })
    expect(downloadBlob).toHaveBeenCalledWith(expect.objectContaining({ type: 'image/png' }), 'recovered-result.png')
  })

  it('packages image and mask into a ZIP with fixed filenames', async () => {
    await downloadRecoveredResult({ image: png, mask: png, fileName: '../../unsafe.png' })
    const [blob, filename] = vi.mocked(downloadBlob).mock.calls[0]
    expect(filename).toBe('recovered-results.zip')
    const zip = await JSZip.loadAsync(await read(blob))
    expect(Object.keys(zip.files).sort()).toEqual(['recovered-result-mask.png', 'recovered-result.png'])
    expect(await zip.file('recovered-result.png')!.async('base64')).toBe(png)
  })

  it('recovers successful batch entries while skipping failed entries', async () => {
    await downloadRecoveredResult({ results: [
      { status: 'success', image: png }, { status: 'error', error: 'failed' }, { status: 'success', image: png },
    ] })
    const zip = await JSZip.loadAsync(await read(vi.mocked(downloadBlob).mock.calls[0][0]))
    expect(Object.keys(zip.files).sort()).toEqual(['recovered-1.png', 'recovered-3.png'])
  })

  it.each([null, {}, { image: 'not-base64!' }, { image: 'aGVsbG8=' }, { results: [{ status: 'error' }] }])('rejects invalid or empty results before downloading (%#)', async (value) => {
    await expect(downloadRecoveredResult(value)).rejects.toThrow()
    expect(downloadBlob).not.toHaveBeenCalled()
  })

  it('rejects oversized batch manifests', async () => {
    await expect(downloadRecoveredResult({ results: Array.from({ length: 101 }, () => ({ status: 'success', image: png })) })).rejects.toThrow('数量')
    expect(downloadBlob).not.toHaveBeenCalled()
  })

  it('does not download after cancellation', async () => {
    const controller = new AbortController()
    const pending = downloadRecoveredResult({ image: png, mask: png }, controller.signal)
    controller.abort()
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
    expect(downloadBlob).not.toHaveBeenCalled()
  })
})
