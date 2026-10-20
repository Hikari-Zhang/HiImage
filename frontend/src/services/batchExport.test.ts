import { describe, expect, it } from 'vitest'
import { uniqueOutputName } from './batchExport'

describe('uniqueOutputName', () => {
  it('keeps both duplicate source names with stable suffixes', () => {
    const used = new Set<string>()
    expect(uniqueOutputName('photo.jpg', used)).toBe('photo_upscaled.png')
    expect(uniqueOutputName('photo.jpg', used)).toBe('photo_upscaled_2.png')
  })
})
