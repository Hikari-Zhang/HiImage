import { describe, expect, it } from 'vitest'
import { prepareBatchFiles } from './batchImport'

describe('prepareBatchFiles', () => {
  it('deduplicates bubbled drop events and never exceeds the remaining limit', () => {
    const file = new File(['a'], 'photo.png', { type: 'image/png' })
    Object.defineProperty(file, 'lastModified', { value: 1 })
    const result = prepareBatchFiles([file, file], new Set(), 1)
    expect(result.accepted).toHaveLength(1)
    expect(result.keys).toHaveLength(1)
  })
})
