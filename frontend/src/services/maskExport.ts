import type { MaskStroke, ROI } from '../types/mask'

type ExportRequest = {
  type: 'export' | 'export-combined'
  strokes: MaskStroke[]
  rois?: number[][]
  imageWidth: number
  imageHeight: number
  scale: number
}

function runExport(request: ExportRequest): Promise<string> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('../workers/maskExport.worker.js', import.meta.url))
    // A worker belongs to this export job, so route changes cannot strand its promise.
    const timeout = setTimeout(() => finish(undefined, new Error('遮罩导出超时，请重试')), 60000)
    let settled = false
    function finish(value?: string, error?: Error) {
      if (settled) return
      settled = true
      clearTimeout(timeout)
      worker.onmessage = null
      worker.onerror = null
      worker.onmessageerror = null
      worker.terminate()
      if (error) reject(error)
      else resolve(value!)
    }
    worker.onmessage = ({ data }) => {
      if (data.type === 'export-complete' && typeof data.dataURL === 'string') finish(data.dataURL)
      else if (data.type === 'export-error') finish(undefined, new Error(data.error || '遮罩导出失败'))
    }
    worker.onerror = (event) => finish(undefined, new Error(event.message || '遮罩导出失败'))
    worker.onmessageerror = () => finish(undefined, new Error('无法读取遮罩导出结果'))
    try {
      worker.postMessage(request)
    } catch (error) {
      finish(undefined, error instanceof Error ? error : new Error(String(error)))
    }
  })
}

export function exportMask(strokes: MaskStroke[], imageWidth: number, imageHeight: number, scale = 1) {
  return runExport({ type: 'export', strokes, imageWidth, imageHeight, scale })
}

export function exportCombinedMask(strokes: MaskStroke[], rois: ROI[], imageWidth: number, imageHeight: number, scale = 1) {
  return runExport({ type: 'export-combined', strokes, rois: rois.map((r) => [r.x1, r.y1, r.x2, r.y2]), imageWidth, imageHeight, scale })
}
