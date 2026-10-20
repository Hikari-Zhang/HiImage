export interface ImageDimensions {
  width: number
  height: number
}

export const MAX_OUTPUT_RGBA_BYTES = 256 * 1024 * 1024

export function estimateOutputRgbaBytes(dimensions: ImageDimensions, scale: number): number {
  const normalizedScale = Math.max(1, scale)
  return dimensions.width * dimensions.height * normalizedScale * normalizedScale * 4
}

export function sumOutputRgbaBytes(dimensions: ImageDimensions[], scale: number): number {
  return dimensions.reduce(
    (total, value) => total + estimateOutputRgbaBytes(value, scale),
    0,
  )
}

export function validateOutputRgbaBudget(
  dimensions: ImageDimensions[],
  scale: number,
): string | null {
  return sumOutputRgbaBytes(dimensions, scale) > MAX_OUTPUT_RGBA_BYTES
    ? '预计输出 RGBA 超过 256 MB，请减少图片、降低倍率或分批处理'
    : null
}

export async function readImageDimensions(source: Blob | string): Promise<ImageDimensions> {
  if (typeof createImageBitmap === 'function') {
    try {
      const blob = typeof source === 'string'
        ? await (await fetch(source)).blob()
        : source
      const bitmap = await createImageBitmap(blob)
      try {
        return { width: bitmap.width, height: bitmap.height }
      } finally {
        bitmap.close()
      }
    } catch {
      // 部分浏览器或图片格式不支持 createImageBitmap，继续使用 Image 解码。
    }
  }

  return new Promise<ImageDimensions>((resolve, reject) => {
    const image = new Image()
    const isObjectUrl = typeof source !== 'string'
    const fallbackSource = typeof source === 'string' ? source : URL.createObjectURL(source)
    image.onload = () => {
      if (isObjectUrl) URL.revokeObjectURL(fallbackSource)
      resolve({ width: image.naturalWidth, height: image.naturalHeight })
    }
    image.onerror = () => {
      if (isObjectUrl) URL.revokeObjectURL(fallbackSource)
      reject(new Error('无法读取图片尺寸'))
    }
    image.src = fallbackSource
  })
}
