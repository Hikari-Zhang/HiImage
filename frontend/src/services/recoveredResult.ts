import { downloadBlob } from './fileService'

/** Read output fields only. Never restore a previous request into a live editor. */
export async function downloadRecoveredResult(value: unknown, signal?: AbortSignal) {
  const checkActive = () => { if (signal?.aborted) throw new DOMException('cancelled', 'AbortError') }
  checkActive()
  if (!value || typeof value !== 'object') throw new Error('结果格式无效')
  const result = value as Record<string, unknown>
  const images: Array<{ value: string; name: string }> = []
  const add = (item: Record<string, unknown>, prefix: string) => {
    if (typeof item.image === 'string' && item.image) images.push({ value: item.image, name: `${prefix}.png` })
    if (typeof item.mask === 'string' && item.mask) images.push({ value: item.mask, name: `${prefix}-mask.png` })
  }
  add(result, 'recovered-result')
  if (Array.isArray(result.results)) {
    if (result.results.length > 100) throw new Error('结果数量超出限制')
    result.results.forEach((item, index) => {
      if (item && typeof item === 'object' && item.status === 'success') add(item, `recovered-${index + 1}`)
    })
  }
  if (!images.length) throw new Error('没有可下载的图片结果')
  // Validate before starting a download and keep decoded output under a fixed budget.
  const decoded: Array<{ bytes: Uint8Array<ArrayBuffer>; name: string }> = []
  let total = 0
  for (const item of images) {
    const encoded = item.value.startsWith('data:image/png;base64,') ? item.value.slice(22) : item.value
    if (encoded.length > 90 * 1024 * 1024) throw new Error('结果图片超出下载预算')
    const binary = atob(encoded)
    total += binary.length
    if (total > 64 * 1024 * 1024) throw new Error('结果图片超出下载预算')
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0))
    if ([137, 80, 78, 71, 13, 10, 26, 10].some((byte, index) => bytes[index] !== byte)) {
      throw new Error('结果图片格式无效')
    }
    decoded.push({ bytes, name: item.name })
  }
  if (decoded.length === 1) {
    checkActive()
    await downloadBlob(new Blob([decoded[0].bytes], { type: 'image/png' }), decoded[0].name)
  } else {
    const { default: JSZip } = await import('jszip')
    const zip = new JSZip()
    for (const item of decoded) zip.file(item.name, item.bytes)
    const blob = await zip.generateAsync({ type: 'blob' })
    checkActive()
    await downloadBlob(blob, 'recovered-results.zip')
  }
}
