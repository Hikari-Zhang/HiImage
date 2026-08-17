import { create } from 'zustand'
import { useWatermarkToolStore, useSynthesisToolStore } from './useImageStore'

export type WorkspaceInputSource = 'original' | 'result'

interface WorkspaceState {
  sourceImage: string | null
  sourceFilePath: string | null
  resultImage: string | null
  imageWidth: number
  imageHeight: number
  resultWidth: number
  resultHeight: number
  /** 超分 / 合成使用原图还是上一页处理结果 */
  inputSource: WorkspaceInputSource

  loadImage: (image: string | null, filePath?: string | null) => void
  setImageDimensions: (width: number, height: number) => void
  /** 写入跨页交接结果；超分/合成默认改为使用该结果 */
  setHandoffResult: (image: string | null, size?: { width: number; height: number }) => void
  setInputSource: (source: WorkspaceInputSource) => void
  /** 将交接结果提升为原图，并清空各页选区 */
  promoteResultToSource: () => void
  reset: () => void
}

function resetToolStores() {
  useWatermarkToolStore.getState().reset()
  useSynthesisToolStore.getState().reset()
}

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  sourceImage: null,
  sourceFilePath: null,
  resultImage: null,
  imageWidth: 0,
  imageHeight: 0,
  resultWidth: 0,
  resultHeight: 0,
  inputSource: 'original',

  loadImage: (image, filePath) => {
    resetToolStores()
    set({
      sourceImage: image,
      sourceFilePath: filePath || null,
      resultImage: null,
      imageWidth: 0,
      imageHeight: 0,
      resultWidth: 0,
      resultHeight: 0,
      inputSource: 'original',
    })
  },

  setImageDimensions: (width, height) => set({ imageWidth: width, imageHeight: height }),

  setHandoffResult: (image, size) =>
    set((state) => ({
      resultImage: image,
      resultWidth: image ? (size?.width ?? state.imageWidth) : 0,
      resultHeight: image ? (size?.height ?? state.imageHeight) : 0,
      inputSource: image ? 'result' : 'original',
    })),

  setInputSource: (source) => set({ inputSource: source }),

  promoteResultToSource: () => {
    const { resultImage, sourceFilePath } = get()
    if (!resultImage) return
    get().loadImage(resultImage, sourceFilePath)
  },

  reset: () => {
    resetToolStores()
    set({
      sourceImage: null,
      sourceFilePath: null,
      resultImage: null,
      imageWidth: 0,
      imageHeight: 0,
      resultWidth: 0,
      resultHeight: 0,
      inputSource: 'original',
    })
  },
}))

/** 超分 / 合成实际处理的图像 */
export function selectWorkingImage(state: WorkspaceState): string | null {
  if (state.inputSource === 'result' && state.resultImage) return state.resultImage
  return state.sourceImage
}

export function selectWorkingWidth(state: WorkspaceState): number {
  if (state.inputSource === 'result' && state.resultImage) {
    return state.resultWidth || state.imageWidth
  }
  return state.imageWidth
}

export function selectWorkingHeight(state: WorkspaceState): number {
  if (state.inputSource === 'result' && state.resultImage) {
    return state.resultHeight || state.imageHeight
  }
  return state.imageHeight
}
