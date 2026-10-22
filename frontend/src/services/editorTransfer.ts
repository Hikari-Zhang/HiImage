import { useImageStore, useSynthesisImageStore, useUpscaleImageStore } from '../stores/useImageStore'
import { useCutoutStore } from '../stores/useCutoutStore'
import { useUpscaleBatchStore } from '../stores/useUpscaleBatchStore'

export const editorDestinations = {
  watermark: '去水印',
  upscale: '超分辨率',
  synthesis: '智能合成',
  cutout: '抠图',
} as const

export type EditorDestination = keyof typeof editorDestinations

export function transferImageToEditor(image: string, destination: EditorDestination) {
  const stores = {
    watermark: useImageStore,
    upscale: useUpscaleImageStore,
    synthesis: useSynthesisImageStore,
    cutout: useCutoutStore,
  }
  const dataUrl = image.startsWith('data:') ? image : `data:image/png;base64,${image}`
  stores[destination].getState().setSourceImage(dataUrl)
  if (destination === 'upscale') useUpscaleBatchStore.getState().setMode('single')
}
