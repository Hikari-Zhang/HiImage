import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  MAX_OUTPUT_RGBA_BYTES,
  estimateOutputRgbaBytes,
  readImageDimensions,
  sumOutputRgbaBytes,
  validateOutputRgbaBudget,
} from './imageDimensions'

describe('imageDimensions', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('uses decoded pixels instead of compressed file size for the hard output budget', async () => {
    const close = vi.fn()
    vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ width: 10000, height: 10000, close })))
    const tinyCompressedFile = new File(['x'], 'compressed.png', { type: 'image/png' })

    const dimensions = await readImageDimensions(tinyCompressedFile)

    expect(estimateOutputRgbaBytes(dimensions, 2)).toBe(1_600_000_000)
    expect(estimateOutputRgbaBytes(dimensions, 2)).toBeGreaterThan(MAX_OUTPUT_RGBA_BYTES)
    expect(validateOutputRgbaBudget([dimensions], 2)).toMatch(/256 MB/)
    expect(close).toHaveBeenCalled()
  })

  it('aggregates output RGBA bytes across a batch', () => {
    expect(sumOutputRgbaBytes([
      { width: 100, height: 200 },
      { width: 300, height: 400 },
    ], 4)).toBe((100 * 200 + 300 * 400) * 16 * 4)
  })

  it('falls back to Image when createImageBitmap is unavailable', async () => {
    vi.stubGlobal('createImageBitmap', undefined)
    class FakeImage {
      naturalWidth = 640
      naturalHeight = 480
      onload: null | (() => void) = null
      onerror: null | (() => void) = null
      set src(_value: string) {
        queueMicrotask(() => this.onload?.())
      }
    }
    vi.stubGlobal('Image', FakeImage)

    await expect(readImageDimensions('data:image/png;base64,eA==')).resolves.toEqual({
      width: 640,
      height: 480,
    })
  })
})
