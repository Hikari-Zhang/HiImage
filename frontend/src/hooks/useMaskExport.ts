import { useRef, useCallback, useEffect } from 'react'
import type { MaskStroke } from '../types/mask'
import type { ROI } from '../stores/useImageStore'

/**
 * 遮罩导出 Hook
 * 使用 WebWorker 在后台线程中将遮罩笔划导出为 base64 PNG
 * 支持合并画笔遮罩和 ROI 矩形区域
 */
export function useMaskExport() {
  const workerRef = useRef<Worker | null>(null)
  const callbackRef = useRef<((dataURL: string) => void) | null>(null)
  const errorCallbackRef = useRef<((error: string) => void) | null>(null)

  // 初始化 WebWorker
  useEffect(() => {
    // 使用 Vite 的 WebWorker 导入方式
    const worker = new Worker(
      new URL('../workers/maskExport.worker.js', import.meta.url)
    )

    worker.onmessage = (e: MessageEvent) => {
      const { type, dataURL, error } = e.data

      if (type === 'export-complete' && callbackRef.current) {
        callbackRef.current(dataURL)
      } else if (type === 'export-error' && errorCallbackRef.current) {
        errorCallbackRef.current(error)
      }
    }

    worker.onerror = (error) => {
      if (errorCallbackRef.current) {
        errorCallbackRef.current(error.message)
      }
    }

    workerRef.current = worker

    return () => {
      worker.terminate()
      workerRef.current = null
    }
  }, [])

  /**
   * 导出遮罩为 base64 PNG
   * @param strokes 遮罩笔划数组
   * @param imageWidth 图像宽度
   * @param imageHeight 图像高度
   * @param scale 导出缩放比例（默认 1）
   * @returns Promise<string> base64 data URL
   */
  const exportMask = useCallback(
    (
      strokes: MaskStroke[],
      imageWidth: number,
      imageHeight: number,
      scale: number = 1
    ): Promise<string> => {
      return new Promise((resolve, reject) => {
        const worker = workerRef.current
        if (!worker) {
          reject(new Error('WebWorker 未初始化'))
          return
        }

        // 设置回调
        callbackRef.current = (dataURL: string) => {
          resolve(dataURL)
        }
        errorCallbackRef.current = (error: string) => {
          reject(new Error(error))
        }

        // 发送消息给 Worker
        worker.postMessage({
          type: 'export',
          strokes,
          imageWidth,
          imageHeight,
          scale,
        })
      })
    },
    []
  )

  /**
   * 导出合并的遮罩（画笔遮罩 + ROI 矩形）
   * @param strokes 遮罩笔划数组
   * @param rois ROI 矩形数组
   * @param imageWidth 图像宽度
   * @param imageHeight 图像高度
   * @param scale 导出缩放比例（默认 1）
   * @returns Promise<string> base64 data URL
   */
  const exportCombinedMask = useCallback(
    (
      strokes: MaskStroke[],
      rois: ROI[],
      imageWidth: number,
      imageHeight: number,
      scale: number = 1
    ): Promise<string> => {
      return new Promise((resolve, reject) => {
        const worker = workerRef.current
        if (!worker) {
          reject(new Error('WebWorker 未初始化'))
          return
        }

        // 设置回调
        callbackRef.current = (dataURL: string) => {
          resolve(dataURL)
        }
        errorCallbackRef.current = (error: string) => {
          reject(new Error(error))
        }

        // 发送消息给 Worker，包含 ROI 信息
        worker.postMessage({
          type: 'export-combined',
          strokes,
          rois: rois.map(r => [r.x1, r.y1, r.x2, r.y2]),
          imageWidth,
          imageHeight,
          scale,
        })
      })
    },
    []
  )

  return {
    exportMask,
    exportCombinedMask,
    isExporting: false, // 可以通过 state 管理，这里简化为 false
  }
}
