/**
 * 遮罩导出 WebWorker
 * 在后台线程中将遮罩笔划导出为 base64 PNG，避免阻塞主线程
 *
 * 主线程发送消息格式：
 * {
 *   type: 'export',
 *   strokes: MaskStroke[],
 *   imageWidth: number,
 *   imageHeight: number,
 *   scale: number  // 可选，导出缩放比例，默认 1
 * }
 *
 * 合并遮罩消息格式：
 * {
 *   type: 'export-combined',
 *   strokes: MaskStroke[],
 *   rois: number[][],  // [[x1, y1, x2, y2], ...]
 *   imageWidth: number,
 *   imageHeight: number,
 *   scale: number
 * }
 *
 * Worker 返回消息格式：
 * {
 *   type: 'export-complete',
 *   dataURL: string  // base64 PNG data URL
 * }
 */

self.onmessage = async function (e) {
  const { type, strokes, rois, imageWidth, imageHeight, scale = 1 } = e.data

  if (type !== 'export' && type !== 'export-combined') {
    return
  }

  try {
    // 创建 OffscreenCanvas（使用完整分辨率，不做人为截断）
    const canvas = new OffscreenCanvas(
      imageWidth * scale,
      imageHeight * scale
    )
    const ctx = canvas.getContext('2d')

    if (!ctx) {
      throw new Error('无法获取 Canvas 2D 上下文')
    }

    // 清空画布（透明）
    ctx.clearRect(0, 0, canvas.width, canvas.height)

    // 绘制所有笔划
    strokes.forEach((stroke) => {
      const isEraser = stroke.tool === 'eraser'

      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      ctx.lineWidth = stroke.size * scale
      ctx.globalCompositeOperation = isEraser ? 'destination-out' : 'source-over'

      if (!isEraser) {
        ctx.strokeStyle = 'rgba(255, 255, 255, 1)' // 白色表示遮罩区域
      }

      // 应用硬度/羽化
      const hardness = stroke.hardness / 100
      if (hardness < 1) {
        // 羽化处理：使用 shadowBlur 模拟
        ctx.shadowBlur = stroke.size * scale * (1 - hardness) * 0.5
        if (!isEraser) {
          ctx.shadowColor = 'rgba(255, 255, 255, 0.5)'
        }
      }

      // 绘制路径
      ctx.beginPath()
      const points = stroke.points
      for (let i = 0; i < points.length; i += 2) {
        const x = points[i] * scale
        const y = points[i + 1] * scale

        if (i === 0) {
          ctx.moveTo(x, y)
        } else {
          ctx.lineTo(x, y)
        }
      }
      ctx.stroke()
      ctx.shadowBlur = 0 // 重置
    })

    // 绘制 ROI 矩形区域（合并模式）
    if (type === 'export-combined' && rois && rois.length > 0) {
      ctx.globalCompositeOperation = 'source-over'
      ctx.fillStyle = 'rgba(255, 255, 255, 1)' // 白色填充

      rois.forEach((roi) => {
        const [x1, y1, x2, y2] = roi
        const width = x2 - x1
        const height = y2 - y1

        ctx.fillRect(
          x1 * scale,
          y1 * scale,
          width * scale,
          height * scale
        )
      })
    }

    // 导出为 PNG
    const blob = await canvas.convertToBlob({ type: 'image/png' })
    const dataURL = await new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result)
      reader.onerror = () => reject(new Error('无法读取遮罩图片'))
      reader.readAsDataURL(blob)
    })
    self.postMessage({ type: 'export-complete', dataURL })
  } catch (error) {
    self.postMessage({
      type: 'export-error',
      error: error.message,
    })
  }
}
