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
  const { type, strokes, rois, polygons, imageWidth, imageHeight, scale = 1 } = e.data

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

    // 选区是底层遮罩，之后的橡皮擦可以修正矩形选区。
    if (type === 'export-combined' && rois) {
      ctx.fillStyle = 'white'
      rois.forEach(([x1, y1, x2, y2]) => ctx.fillRect(
        x1 * scale, y1 * scale, (x2 - x1) * scale, (y2 - y1) * scale,
      ))
    }
    if (type === 'export-combined' && polygons) {
      ctx.fillStyle = 'white'
      polygons.forEach(polygon => {
        ctx.beginPath()
        polygon.forEach(([x, y], index) => {
          if (index === 0) ctx.moveTo(x * scale, y * scale)
          else ctx.lineTo(x * scale, y * scale)
        })
        ctx.closePath()
        ctx.fill()
      })
    }

    // 绘制所有笔划
    strokes.forEach((stroke) => {
      const isEraser = stroke.tool === 'eraser'

      ctx.lineCap = stroke.shape === 'square' ? 'square' : 'round'
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
      if (points.length === 2) {
        ctx.fillStyle = 'white'
        if (stroke.shape === 'square') {
          ctx.fillRect((points[0] - stroke.size / 2) * scale, (points[1] - stroke.size / 2) * scale,
            stroke.size * scale, stroke.size * scale)
        } else {
          ctx.arc(points[0] * scale, points[1] * scale, stroke.size * scale / 2, 0, Math.PI * 2)
          ctx.fill()
        }
      }
      for (let i = 0; i < points.length; i += 2) {
        const x = points[i] * scale
        const y = points[i + 1] * scale

        if (i === 0) {
          ctx.moveTo(x, y)
        } else {
          ctx.lineTo(x, y)
        }
      }
      if (points.length >= 4) ctx.stroke()
      ctx.shadowBlur = 0 // 重置
    })

    // PNG 中明确存储黑底，避免透明白色被灰度解码成白色遮罩。
    ctx.globalCompositeOperation = 'destination-over'
    ctx.fillStyle = 'black'
    ctx.fillRect(0, 0, canvas.width, canvas.height)

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
