import { describe, expect, it, vi } from 'vitest'
import { base64ToBlob, dataUrlToBlob, downloadBlob, fileToDataUrl } from './fileService'

describe('fileService', () => {
  it('converts a File to a data URL', async () => {
    const dataUrl = await fileToDataUrl(new File(['hello'], 'hello.txt', { type: 'text/plain' }))
    expect(dataUrl).toBe('data:text/plain;base64,aGVsbG8=')
  })

  it('converts data URLs and raw base64 to Blobs', async () => {
    expect(await dataUrlToBlob('data:text/plain;base64,aGVsbG8=')).toMatchObject({
      type: 'text/plain',
      size: 5,
    })
    expect(await base64ToBlob('aGVsbG8=', 'text/plain')).toMatchObject({
      type: 'text/plain',
      size: 5,
    })
  })

  it('clicks an object URL and revokes it on the next task', async () => {
    vi.useFakeTimers()
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: () => 'blob:test' })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: () => {} })
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:test')
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    await downloadBlob(new Blob(['hello'], { type: 'text/plain' }), 'hello.txt')
    expect(click).toHaveBeenCalled()
    expect(createObjectURL).toHaveBeenCalled()
    expect(revokeObjectURL).not.toHaveBeenCalled()

    vi.runAllTimers()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:test')
    vi.useRealTimers()
    createObjectURL.mockRestore()
    revokeObjectURL.mockRestore()
    click.mockRestore()
  })
})
