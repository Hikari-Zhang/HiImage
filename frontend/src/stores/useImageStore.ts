import { create } from 'zustand'
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
  setImageDimensions: (width: number, height: number) => void
  setResultImage: (image: string | null) => void
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

export const useImageStore = create<ImageState>((set) => ({
  sourceImage: null,
  sourceFilePath: null,
  resultImage: null,
  imageWidth: 0,
  imageHeight: 0,
  rois: [],
  selectedROIs: [],

  // 遮罩相关状态初始值
  maskStrokes: [],
  brushSettings: DEFAULT_BRUSH_SETTINGS,
  maskDataURL: null,
  canvasTool: 'rectangle',

  setSourceImage: (image, filePath) =>
    set({
      sourceImage: image,
      sourceFilePath: filePath || null,
      resultImage: null,
      rois: [],
      selectedROIs: [],
      maskStrokes: [],
      maskDataURL: null,
    }),

  setImageDimensions: (width, height) => set({ imageWidth: width, imageHeight: height }),

  setResultImage: (image) => set({ resultImage: image }),

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

  reset: () =>
    set({
      sourceImage: null,
      sourceFilePath: null,
      resultImage: null,
      imageWidth: 0,
      imageHeight: 0,
      rois: [],
      selectedROIs: [],
      maskStrokes: [],
      maskDataURL: null,
      brushSettings: DEFAULT_BRUSH_SETTINGS,
      canvasTool: 'rectangle',
    }),
}))
