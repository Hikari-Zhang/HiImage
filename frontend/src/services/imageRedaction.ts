import type { MaskStroke } from '../types/mask'

export type RedactionMode = 'mosaic' | 'tile' | 'picture' | 'solid'
export type RedactionFormat = 'image/png' | 'image/jpeg'
export type RedactionBox = { x1: number; y1: number; x2: number; y2: number }

export interface RedactionStyle {
  mode: RedactionMode
  block: number
  color: string
  asset: string | null
  fit: 'cover' | 'contain'
  zoom: number
  offsetX: number
  offsetY: number
}

export interface RedactionRegion {
  id: string
  label: string
  box?: RedactionBox
  strokes: MaskStroke[]
  style: RedactionStyle
}

export const defaultRedactionStyle: RedactionStyle = {
  mode: 'mosaic', block: 24, color: '#000000', asset: null, fit: 'cover', zoom: 100, offsetX: 0, offsetY: 0,
}

export const redactionImageTypes = 'image/png,image/jpeg,image/webp'

export async function loadRedactionImage(file: File): Promise<{ image: HTMLImageElement; url: string }> {
  const header = new Uint8Array(await file.slice(0, 12).arrayBuffer())
  const ascii = (start: number, end: number) => String.fromCharCode(...header.slice(start, end))
  const png = [137, 80, 78, 71, 13, 10, 26, 10].every((value, index) => header[index] === value)
  const jpeg = header[0] === 255 && header[1] === 216 && header[2] === 255
  const webp = ascii(0, 4) === 'RIFF' && ascii(8, 12) === 'WEBP'
  if (!png && !jpeg && !webp) throw new Error('请选择 PNG、JPEG 或 WebP 图片。')

  const url = URL.createObjectURL(file)
  try {
    const image = new Image()
    image.src = url
    await image.decode()
    const { naturalWidth: width, naturalHeight: height } = image
    if (!width || !height || width * height > 32_000_000 || Math.max(width, height) > 8192) {
      throw new Error('图片最长边需在 8192 像素以内，总像素不超过 3200 万。')
    }
    return { image, url }
  } catch (error) {
    URL.revokeObjectURL(url)
    throw error
  }
}

export function regionBounds(region: RedactionRegion, width: number, height: number): RedactionBox | null {
  let { x1, y1, x2, y2 } = region.box ?? { x1: Infinity, y1: Infinity, x2: -Infinity, y2: -Infinity }
  for (const stroke of region.strokes) {
    if (stroke.tool === 'eraser') continue
    const radius = stroke.size / 2
    for (let index = 0; index + 1 < stroke.points.length; index += 2) {
      const x = stroke.points[index], y = stroke.points[index + 1]
      x1 = Math.min(x1, x - radius)
      y1 = Math.min(y1, y - radius)
      x2 = Math.max(x2, x + radius)
      y2 = Math.max(y2, y + radius)
    }
  }
  const bounds = { x1: Math.max(0, x1), y1: Math.max(0, y1), x2: Math.min(width, x2), y2: Math.min(height, y2) }
  return bounds.x2 > bounds.x1 && bounds.y2 > bounds.y1 ? bounds : null
}

export function resizeRedactionRegion(region: RedactionRegion, from: RedactionBox, to: RedactionBox): RedactionRegion {
  const scaleX = (to.x2 - to.x1) / (from.x2 - from.x1)
  const scaleY = (to.y2 - to.y1) / (from.y2 - from.y1)
  const x = (value: number) => to.x1 + (value - from.x1) * scaleX
  const y = (value: number) => to.y1 + (value - from.y1) * scaleY
  const box = region.box && {
    x1: x(region.box.x1), x2: x(region.box.x2), y1: y(region.box.y1), y2: y(region.box.y2),
  }
  return {
    ...region, box,
    strokes: region.strokes.map(stroke => ({
      ...stroke,
      points: stroke.points.map((value, index) => index % 2 ? y(value) : x(value)),
      size: stroke.size * Math.min(scaleX, scaleY),
    })),
  }
}

function context(canvas: HTMLCanvasElement, width: number, height: number) {
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('浏览器无法处理这张图片。')
  return ctx
}

function paintMask(ctx: CanvasRenderingContext2D, region: RedactionRegion, scale: number, x: number, y: number) {
  if (region.box && !region.strokes.length) {
    // The layer already spans the rounded-out rectangle.
    ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height)
    return true
  }
  ctx.setTransform(scale, 0, 0, scale, -x, -y)
  ctx.fillStyle = '#fff'
  ctx.strokeStyle = '#fff'
  if (region.box) {
    const { x1, y1, x2, y2 } = region.box
    ctx.fillRect(x1, y1, x2 - x1, y2 - y1)
  }
  for (const { points, size, tool } of region.strokes) {
    if (points.length < 2) continue
    ctx.globalCompositeOperation = tool === 'eraser' ? 'destination-out' : 'source-over'
    ctx.beginPath()
    if (points.length === 2) {
      ctx.arc(points[0], points[1], size / 2, 0, Math.PI * 2)
      ctx.fill()
    } else {
      ctx.lineWidth = size
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      ctx.moveTo(points[0], points[1])
      for (let index = 2; index < points.length; index += 2) ctx.lineTo(points[index], points[index + 1])
      ctx.stroke()
    }
  }
  ctx.resetTransform()
  ctx.globalCompositeOperation = 'source-over'
  const pixels = ctx.getImageData(0, 0, ctx.canvas.width, ctx.canvas.height)
  let visible = false
  for (let index = 3; index < pixels.data.length; index += 4) {
    if (pixels.data[index]) {
      pixels.data[index] = 255
      visible = true
    }
  }
  ctx.putImageData(pixels, 0, 0)
  return visible
}

function drawPicture(
  ctx: CanvasRenderingContext2D, image: HTMLImageElement, width: number, height: number,
  fit: RedactionStyle['fit'], zoom = 1, offsetX = 0, offsetY = 0,
) {
  const ratio = (fit === 'cover' ? Math.max : Math.min)(width / image.naturalWidth, height / image.naturalHeight) * zoom
  const w = image.naturalWidth * ratio
  const h = image.naturalHeight * ratio
  ctx.drawImage(image, (width - w) / 2 + offsetX, (height - h) / 2 + offsetY, w, h)
}

export function renderRedactions(
  image: HTMLImageElement, regions: RedactionRegion[], assets: ReadonlyMap<string, HTMLImageElement>,
  maxEdge = Infinity, background?: string,
): { canvas: HTMLCanvasElement; hasRedactions: boolean } {
  const { naturalWidth: width, naturalHeight: height } = image
  const scale = Math.min(1, maxEdge / Math.max(width, height))
  const canvas = document.createElement('canvas')
  const output = context(canvas, Math.ceil(width * scale), Math.ceil(height * scale))
  if (background) {
    output.fillStyle = background
    output.fillRect(0, 0, canvas.width, canvas.height)
  }
  output.drawImage(image, 0, 0, canvas.width, canvas.height)
  const mask = document.createElement('canvas')
  const layer = document.createElement('canvas')
  const tile = document.createElement('canvas')
  let hasRedactions = false

  // Draw opaque covers after mosaics so an overlap never replaces a cover with source pixels.
  const ordered = [...regions.filter(region => region.style.mode === 'mosaic'),
    ...regions.filter(region => region.style.mode !== 'mosaic')]
  for (const region of ordered) {
    const bounds = regionBounds(region, width, height)
    if (!bounds) continue
    const { x1, y1, x2, y2 } = bounds
    const x = Math.floor(x1 * scale), y = Math.floor(y1 * scale)
    const w = Math.ceil(x2 * scale) - x, h = Math.ceil(y2 * scale) - y
    if (!paintMask(context(mask, w, h), region, scale, x, y)) continue
    hasRedactions = true

    const ctx = context(layer, w, h)
    const style = region.style
    ctx.fillStyle = style.color
    ctx.fillRect(0, 0, w, h)
    const asset = style.asset ? assets.get(style.asset) : undefined
    const block = Math.max(1, Math.round(style.block * scale))
    if (style.mode === 'mosaic') {
      const low = context(tile, Math.ceil(w / block), Math.ceil(h / block))
      low.drawImage(image, x1, y1, x2 - x1, y2 - y1, 0, 0, tile.width, tile.height)
      ctx.imageSmoothingEnabled = false
      ctx.drawImage(tile, 0, 0, tile.width * block, tile.height * block)
    } else if (style.mode === 'tile' && asset) {
      drawPicture(context(tile, block, block), asset, block, block, 'cover')
      const pattern = ctx.createPattern(tile, 'repeat')
      if (!pattern) throw new Error('素材平铺失败，请重新选择素材。')
      pattern.setTransform(new DOMMatrix().translate(style.offsetX * scale, style.offsetY * scale))
      ctx.fillStyle = pattern
      ctx.fillRect(0, 0, w, h)
    } else if (style.mode === 'picture' && asset) {
      drawPicture(ctx, asset, w, h, style.fit, style.zoom / 100, style.offsetX * scale, style.offsetY * scale)
    }
    ctx.globalCompositeOperation = 'destination-in'
    ctx.drawImage(mask, 0, 0)
    output.drawImage(layer, x, y)
  }
  return { canvas, hasRedactions }
}

export async function encodeRedactions(canvas: HTMLCanvasElement, format: RedactionFormat): Promise<Blob> {
  // Encode the rendered pixels as a new file. No source metadata or thumbnails are copied.
  return new Promise((resolve, reject) => {
    canvas.toBlob(blob => {
      if (blob?.type === format) resolve(blob)
      else reject(new Error('图片导出失败，请尝试缩小图片。'))
    }, format, 0.95)
  })
}
