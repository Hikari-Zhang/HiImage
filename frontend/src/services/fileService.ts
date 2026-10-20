export function fileToDataUrl(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error ?? new Error('读取文件失败'))
    reader.readAsDataURL(file)
  })
}

function normalizeBase64(value: string): { mime: string; encoded: string } {
  const match = value.match(/^data:([^;,]+)?(?:;base64)?,(.*)$/)
  return match
    ? { mime: match[1] || 'application/octet-stream', encoded: match[2] }
    : { mime: 'application/octet-stream', encoded: value }
}

export async function dataUrlToBlob(value: string): Promise<Blob> {
  const { mime, encoded } = normalizeBase64(value)
  const binary = atob(encoded)
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0))
  return new Blob([bytes], { type: mime })
}

export async function base64ToBlob(value: string, mime = 'application/octet-stream'): Promise<Blob> {
  const { encoded } = normalizeBase64(value)
  const binary = atob(encoded)
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0))
  return new Blob([bytes], { type: mime })
}

export async function downloadBlob(blob: Blob, filename: string): Promise<void> {
  const objectUrl = URL.createObjectURL(blob)
  try {
    const anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = filename
    anchor.click()
  } finally {
    setTimeout(() => URL.revokeObjectURL(objectUrl), 0)
  }
}

export async function downloadDataUrl(value: string, filename: string): Promise<void> {
  return downloadBlob(await dataUrlToBlob(value), filename)
}
