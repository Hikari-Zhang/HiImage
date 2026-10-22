import { create } from 'zustand'
import type { EditorSessionId } from '../types/editor'
import { useProcessStore } from './useProcessStore'
import type { MaskStroke, BrushSettings, MaskTool } from '../types/mask'
import { DEFAULT_BRUSH_SETTINGS, MAX_UNDO_STEPS } from '../types/mask'

export interface ROI {
  id: string
  x1: number
  y1: number
  x2: number
  y2: number
}

interface ImageState {
  sourceImage: string | null // Base64 or data URL
  sourceFilePath: string | null
  resultImage: string | null
  showResult: boolean
  setShowResult: (show: boolean) => void
  sourceRevision: number
  resultWidth: number
  resultHeight: number
  imageWidth: number
  imageHeight: number
  rois: ROI[]
  selectedROIs: string[]

  // 遮罩绘制相关状态
  maskStrokes: MaskStroke[]
  brushSettings: BrushSettings
  maskDataURL: string | null
  canvasTool: MaskTool

  setSourceImage: (image: string | null, filePath?: string | null) => void
  setImageDimensions: (width: number, height: number, revision?: number) => void
  setResultImage: (image: string | null, width?: number, height?: number) => void
  addROI: (roi: Omit<ROI, 'id'>) => void
  removeROI: (id: string) => void
  clearROIs: () => void
  selectROI: (id: string) => void
  deselectROI: (id: string) => void
  setROIs: (rois: ROI[]) => void
  reset: () => void

  // 遮罩绘制相关方法
  setBrushSettings: (settings: Partial<BrushSettings>) => void
  addMaskStroke: (stroke: MaskStroke) => void
  undoMaskStroke: () => void
  clearMaskStrokes: () => void
  setMaskDataURL: (url: string | null) => void
  setCanvasTool: (tool: MaskTool) => void
}

let roiCounter = 0

function createImageSessionStore(sessionId: EditorSessionId) {
  return create<ImageState>((set) => ({
    sourceRevision: 0,
    resultWidth: 0,
    resultHeight: 0,
    sourceImage: null,
    sourceFilePath: null,
    resultImage: null,
    showResult: false,
    setShowResult: (showResult) => set({ showResult }),
    imageWidth: 0,
    imageHeight: 0,
    rois: [],
    selectedROIs: [],

    // 遮罩相关状态初始值
    maskStrokes: [],
    brushSettings: DEFAULT_BRUSH_SETTINGS,
    maskDataURL: null,
    canvasTool: 'rectangle',

    setSourceImage: (image, filePath) => {
      useProcessStore.getState().reset(sessionId)
      set((state) => ({
        sourceRevision: state.sourceRevision + 1,
        imageWidth: 0,
        imageHeight: 0,
        resultWidth: 0,
        resultHeight: 0,
        sourceImage: image,
        sourceFilePath: filePath || null,
        resultImage: null,
        showResult: false,
        rois: [],
        selectedROIs: [],
        maskStrokes: [],
        maskDataURL: null,
      }))
    },

    setImageDimensions: (width, height, revision) => set((state) =>
      revision !== undefined && state.sourceRevision !== revision
        ? state : { imageWidth: width, imageHeight: height }),

    setResultImage: (image, width = 0, height = 0) => set({
      resultImage: image, resultWidth: width, resultHeight: height, showResult: !!image,
    }),

    addROI: (roi) => {
      const id = `roi_${++roiCounter}`
      set((state) => ({
        rois: [...state.rois, { ...roi, id }],
      }))
    },

    removeROI: (id) =>
      set((state) => ({
        rois: state.rois.filter((r) => r.id !== id),
        selectedROIs: state.selectedROIs.filter((sid) => sid !== id),
      })),

    clearROIs: () => set({ rois: [], selectedROIs: [] }),

    selectROI: (id) =>
      set((state) => ({
        selectedROIs: [...state.selectedROIs, id],
      })),

    deselectROI: (id) =>
      set((state) => ({
        selectedROIs: state.selectedROIs.filter((sid) => sid !== id),
      })),

    setROIs: (rois) => set({ rois }),

    // 遮罩相关方法实现
    setBrushSettings: (settings) =>
      set((state) => ({
        brushSettings: { ...state.brushSettings, ...settings },
      })),

    addMaskStroke: (stroke) =>
      set((state) => {
        const newStrokes = [...state.maskStrokes, stroke]
        // 限制撤销栈大小
        if (newStrokes.length > MAX_UNDO_STEPS) {
          newStrokes.shift()
        }
        return { maskStrokes: newStrokes }
      }),

    undoMaskStroke: () =>
      set((state) => ({
        maskStrokes: state.maskStrokes.slice(0, -1),
      })),

    clearMaskStrokes: () => set({ maskStrokes: [], maskDataURL: null }),

    setMaskDataURL: (url) => set({ maskDataURL: url }),

    setCanvasTool: (tool) => set({ canvasTool: tool }),

    reset: () => {
      useProcessStore.getState().reset(sessionId)
      set((state) => ({
        sourceRevision: state.sourceRevision + 1,
        resultWidth: 0,
        resultHeight: 0,
        sourceImage: null,
        sourceFilePath: null,
        resultImage: null,
        showResult: false,
        imageWidth: 0,
        imageHeight: 0,
        rois: [],
        selectedROIs: [],
        maskStrokes: [],
        maskDataURL: null,
        brushSettings: DEFAULT_BRUSH_SETTINGS,
        canvasTool: 'rectangle',
      }))
    },
  }))
}

export const useImageStore = createImageSessionStore('watermark')
export const useUpscaleImageStore = createImageSessionStore('upscale')
export const useSynthesisImageStore = createImageSessionStore('synthesis')
