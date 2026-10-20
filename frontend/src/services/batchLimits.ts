export const MAX_BATCH_FILES = 100
export const MAX_FILE_BYTES = 20 * 1024 * 1024
export const MAX_TOTAL_INPUT_BYTES = 128 * 1024 * 1024
export const MAX_ESTIMATED_RESULT_BYTES = 256 * 1024 * 1024
export const MAX_RESULT_BYTES = 256 * 1024 * 1024
export const MAX_ZIP_BYTES = 64 * 1024 * 1024

export interface BatchResultItem {
  id: string
  fileName: string
  file?: File
  dataUrl?: string
  status: 'pending' | 'processing' | 'success' | 'error'
  error?: string
  resultDataUrl?: string
}

export function validateBatchFiles(files: File[]): { accepted: File[]; rejected: string[] } {
  const rejected: string[] = []
  const accepted = files.filter((file) => {
    if (file.size > MAX_FILE_BYTES) {
      rejected.push(`${file.name} 超过 20 MB`)
      return false
    }
    if (!file.type.startsWith('image/')) {
      rejected.push(`${file.name} 不是图片`)
      return false
    }
    return true
  })
  if (accepted.length > MAX_BATCH_FILES) {
    rejected.push(`最多同时处理 ${MAX_BATCH_FILES} 张图片`)
    accepted.length = MAX_BATCH_FILES
  }
  return { accepted, rejected }
}

export function estimateBatchMemory(files: File[], scale: number): number {
  return files.reduce((total, file) => total + file.size * Math.max(1, scale * scale) * 1.5, 0)
}

export function validateBatchBudget(
  files: File[],
  scale: number,
  existingInputBytes = 0,
  retainedResultBytes = 0,
): string | null {
  const inputBytes = existingInputBytes + files.reduce((total, file) => total + file.size, 0)
  if (inputBytes > MAX_TOTAL_INPUT_BYTES) {
    return '批量输入总量超过 128 MB，请分批处理'
  }
  if (retainedResultBytes + estimateBatchMemory(files, scale) > MAX_ESTIMATED_RESULT_BYTES) {
    return '按当前倍率预计结果超过 256 MB，请减少图片、降低倍率或分批处理'
  }
  return null
}

export function dataUrlByteLength(dataUrl: string): number {
  const comma = dataUrl.indexOf(',')
  const payload = comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl
  if (!dataUrl.slice(0, Math.max(0, comma)).includes(';base64')) {
    return new TextEncoder().encode(decodeURIComponent(payload)).byteLength
  }
  const padding = payload.endsWith('==') ? 2 : payload.endsWith('=') ? 1 : 0
  return Math.max(0, Math.floor(payload.length * 3 / 4) - padding)
}

export function resultDataUrlsByteLength(dataUrls: string[]): number {
  return dataUrls.reduce((total, dataUrl) => total + dataUrlByteLength(dataUrl), 0)
}

export function validateZipBudgetBytes(resultBytes: number): string | null {
  return resultBytes > MAX_ZIP_BYTES
    ? '结果总量超过 64 MB，无法打包 ZIP；请逐张下载'
    : null
}

export function applyBatchResultBudget(
  item: BatchResultItem,
  resultDataUrl: string,
  retainedResultBytes: number,
): { accepted: boolean; item: BatchResultItem } {
  if (retainedResultBytes + dataUrlByteLength(resultDataUrl) > MAX_RESULT_BYTES) {
    return {
      accepted: false,
      item: {
        ...item,
        status: 'error',
        error: '累计结果超过 256 MB，已丢弃本次结果并停止后续处理；原图已保留，可下载现有结果后重试',
        resultDataUrl: undefined,
      },
    }
  }
  const {
    file: _file,
    dataUrl: _dataUrl,
    error: _error,
    ...releasedItem
  } = item
  return {
    accepted: true,
    item: {
      ...releasedItem,
      status: 'success',
      resultDataUrl,
    },
  }
}
