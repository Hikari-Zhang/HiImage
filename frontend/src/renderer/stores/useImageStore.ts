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

interface ToolState {
  rois: ROI[]
  selectedROIs: string[]
  maskStrokes: MaskStroke[]
  brushSettings: BrushSettings
  maskDataURL: string | null
  canvasTool: MaskTool

  addROI: (roi: Omit<ROI, 'id'>) => void
  removeROI: (id: string) => void
  clearROIs: () => void
  selectROI: (id: string) => void
  deselectROI: (id: string) => void
  setROIs: (rois: ROI[]) => void

  setBrushSettings: (settings: Partial<BrushSettings>) => void
  addMaskStroke: (stroke: MaskStroke) => void
  undoMaskStroke: () => void
  clearMaskStrokes: () => void
  setMaskDataURL: (url: string | null) => void
  setCanvasTool: (tool: MaskTool) => void
  reset: () => void
}

let roiCounter = 0

function createToolStore() {
  return create<ToolState>((set) => ({
    rois: [],
    selectedROIs: [],
    maskStrokes: [],
    brushSettings: DEFAULT_BRUSH_SETTINGS,
    maskDataURL: null,
    canvasTool: 'rectangle',

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

    setBrushSettings: (settings) =>
      set((state) => ({
        brushSettings: { ...state.brushSettings, ...settings },
      })),

    addMaskStroke: (stroke) =>
      set((state) => {
        const newStrokes = [...state.maskStrokes, stroke]
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
        rois: [],
        selectedROIs: [],
        maskStrokes: [],
        maskDataURL: null,
        brushSettings: DEFAULT_BRUSH_SETTINGS,
        canvasTool: 'rectangle',
      }),
  }))
}

/** 去水印页选区 / 画笔，与合成页隔离 */
export const useWatermarkToolStore = createToolStore()

/** 智能合成页选区 / 画笔 */
export const useSynthesisToolStore = createToolStore()
