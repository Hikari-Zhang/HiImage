import { describe, expect, it } from 'vitest'
import {
  MAX_BATCH_FILES,
  MAX_ESTIMATED_RESULT_BYTES,
  MAX_FILE_BYTES,
  MAX_RESULT_BYTES,
  MAX_TOTAL_INPUT_BYTES,
  MAX_ZIP_BYTES,
  applyBatchResultBudget,
  dataUrlByteLength,
  estimateBatchMemory,
  resultDataUrlsByteLength,
  validateBatchBudget,
  validateBatchFiles,
  validateZipBudgetBytes,
} from './batchLimits'

describe('batchLimits', () => {
  it('rejects oversized and non-image files and caps the batch', () => {
    const files = Array.from({ length: MAX_BATCH_FILES + 1 }, (_, i) =>
      new File(['x'], `image-${i}.png`, { type: 'image/png' }),
    )
    Object.defineProperty(files[0], 'size', { value: MAX_FILE_BYTES + 1 })
    const result = validateBatchFiles(files)
    expect(result.accepted).toHaveLength(MAX_BATCH_FILES)
    expect(result.rejected.length).toBeGreaterThan(0)
  })

  it('estimates memory from source size and scale', () => {
    const file = new File(['x'], 'image.png', { type: 'image/png' })
    Object.defineProperty(file, 'size', { value: 1024 })
    expect(estimateBatchMemory([file], 2)).toBe(6144)
  })

  it('uses a 20 MB per-file limit aligned below the backend 30 MB limit', () => {
    expect(MAX_FILE_BYTES).toBe(20 * 1024 * 1024)
  })

  it('rejects imports exceeding the total input budget', () => {
    const file = new File(['x'], 'large.png', { type: 'image/png' })
    Object.defineProperty(file, 'size', { value: MAX_TOTAL_INPUT_BYTES })
    expect(validateBatchBudget([file], 1, 1)).toMatch(/输入总量/)
  })

  it('rejects processing when scale-squared estimated results exceed the budget', () => {
    const file = new File(['x'], 'large.png', { type: 'image/png' })
    Object.defineProperty(file, 'size', { value: MAX_ESTIMATED_RESULT_BYTES / 8 })
    expect(validateBatchBudget([file], 4)).toMatch(/预计结果/)
  })

  it('counts decoded base64 result bytes and enforces the cumulative result budget', () => {
    expect(dataUrlByteLength('data:image/png;base64,YWJjZA==')).toBe(4)
    expect(resultDataUrlsByteLength([
      'data:image/png;base64,YWJj',
      'data:image/png;base64,ZA==',
    ])).toBe(4)
    expect(MAX_RESULT_BYTES).toBe(256 * 1024 * 1024)
  })

  it('uses a separate 64 MB ZIP budget', () => {
    expect(MAX_ZIP_BYTES).toBe(64 * 1024 * 1024)
    expect(validateZipBudgetBytes(MAX_ZIP_BYTES)).toBeNull()
    expect(validateZipBudgetBytes(MAX_ZIP_BYTES + 1)).toMatch(/逐张下载/)
  })

  it('preserves the current source when a new result exceeds the cumulative budget', () => {
    const file = new File(['source'], 'source.png', { type: 'image/png' })
    const item = { id: '1', fileName: 'source.png', file, dataUrl: 'data:source', status: 'processing' as const }

    const decision = applyBatchResultBudget(item, 'data:image/png;base64,YWJjZA==', MAX_RESULT_BYTES)

    expect(decision.accepted).toBe(false)
    expect(decision.item.file).toBe(file)
    expect(decision.item.dataUrl).toBe('data:source')
    expect(decision.item.resultDataUrl).toBeUndefined()
    expect(decision.item.error).toMatch(/累计结果超过 256 MB/)
  })

  it('releases source references only when a result is accepted', () => {
    const item = {
      id: '1',
      fileName: 'source.png',
      file: new File(['source'], 'source.png', { type: 'image/png' }),
      dataUrl: 'data:source',
      status: 'processing' as const,
    }

    const decision = applyBatchResultBudget(item, 'data:image/png;base64,YWJjZA==', 0)

    expect(decision.accepted).toBe(true)
    expect(decision.item.file).toBeUndefined()
    expect(decision.item.dataUrl).toBeUndefined()
    expect(decision.item.status).toBe('success')
  })
})
