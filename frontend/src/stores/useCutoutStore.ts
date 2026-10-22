import { create } from 'zustand'
import { useProcessStore } from './useProcessStore'
import type { MaskStroke } from '../types/mask'

/** 画笔模式：添加前景 / 擦除前景 */
export type CutoutBrushMode = 'add_fg' | 'remove_fg'

interface CutoutState {
  // ── 图像 ──
  sourceImage: string | null       // Base64 原图
  sourceFilePath: string | null
  resultImage: string | null       // RGBA 结果 (base64)
  resultMask: string | null        // Alpha mask (base64)
  sourceRevision: number
  compositeResult: string | null
  bgColor: string
  setCompositeResult: (value: string | null) => void
  setBgColor: (value: string) => void
  imageWidth: number
  imageHeight: number

  // ── 画笔编辑 ──
  brushStrokes: MaskStroke[]       // 画笔笔划记录
  brushMode: CutoutBrushMode       // 添加前景 / 擦除前景
  brushSize: number                // 画笔大小 5-100

  // ── 精修参数 ──
  feather: number                  // 羽化 0-30
  dilate: number                   // 膨胀/腐蚀 -20~20

  // ── 视图 ──
  showMask: boolean                // 叠加显示 mask 边缘
  showCheckerboard: boolean        // 棋盘格透明背景

  // ── 操作 ──
  setSourceImage: (image: string | null, filePath?: string | null) => void
  setImageDimensions: (width: number, height: number, revision?: number) => void
  setResult: (image: string | null, mask: string | null) => void
  setResultImage: (image: string | null) => void
  setResultMask: (mask: string | null) => void
  addBrushStroke: (stroke: MaskStroke) => void
  undoBrushStroke: () => void
  clearBrushStrokes: () => void
  setBrushMode: (mode: CutoutBrushMode) => void
  setBrushSize: (size: number) => void
  setFeather: (value: number) => void
  setDilate: (value: number) => void
  toggleMask: () => void
  toggleCheckerboard: () => void
  setShowMask: (show: boolean) => void
  reset: () => void
}

const MAX_UNDO_STEPS = 20

export const useCutoutStore = create<CutoutState>((set) => ({
  // ── 图像初始值 ──
  sourceRevision: 0,
  compositeResult: null,
  bgColor: '#FFFFFF',
  setCompositeResult: (compositeResult) => set({ compositeResult }),
  setBgColor: (bgColor) => set({ bgColor }),
  sourceImage: null,
  sourceFilePath: null,
  resultImage: null,
  resultMask: null,
  imageWidth: 0,
  imageHeight: 0,

  // ── 画笔初始值 ──
  brushStrokes: [],
  brushMode: 'add_fg' as CutoutBrushMode,
  brushSize: 30,

  // ── 精修参数初始值 ──
  feather: 0,
  dilate: 0,

  // ── 视图初始值 ──
  showMask: false,
  showCheckerboard: true,

  // ── 操作实现 ──
  setSourceImage: (image, filePath) => {
    useProcessStore.getState().reset('cutout')
    set((state) => ({
      sourceRevision: state.sourceRevision + 1,
      imageWidth: 0,
      imageHeight: 0,
      compositeResult: null,
      sourceImage: image,
      sourceFilePath: filePath || null,
      resultImage: null,
      resultMask: null,
      brushStrokes: [],
    }))
  },

  setImageDimensions: (width, height, revision) => set((state) =>
    revision !== undefined && state.sourceRevision !== revision
      ? state : { imageWidth: width, imageHeight: height }),

  setResult: (image, mask) => set({ resultImage: image, resultMask: mask, compositeResult: null }),

  setResultImage: (image) => set({ resultImage: image }),

  setResultMask: (mask) => set({ resultMask: mask }),

  addBrushStroke: (stroke) =>
    set((state) => {
      const newStrokes = [...state.brushStrokes, stroke]
      if (newStrokes.length > MAX_UNDO_STEPS) {
        newStrokes.shift()
      }
      return { brushStrokes: newStrokes }
    }),

  undoBrushStroke: () =>
    set((state) => ({
      brushStrokes: state.brushStrokes.slice(0, -1),
    })),

  clearBrushStrokes: () => set({ brushStrokes: [] }),

  setBrushMode: (mode) => set({ brushMode: mode }),

  setBrushSize: (size) => set({ brushSize: size }),

  setFeather: (value) => set({ feather: value }),

  setDilate: (value) => set({ dilate: value }),

  toggleMask: () => set((state) => ({ showMask: !state.showMask })),

  toggleCheckerboard: () => set((state) => ({ showCheckerboard: !state.showCheckerboard })),

  setShowMask: (show) => set({ showMask: show }),

  reset: () => {
    useProcessStore.getState().reset('cutout')
    set((state) => ({
      sourceRevision: state.sourceRevision + 1,
      compositeResult: null,
      bgColor: '#FFFFFF',
      sourceImage: null,
      sourceFilePath: null,
      resultImage: null,
      resultMask: null,
      imageWidth: 0,
      imageHeight: 0,
      brushStrokes: [],
      brushMode: 'add_fg' as CutoutBrushMode,
      brushSize: 30,
      feather: 0,
      dilate: 0,
      showMask: false,
      showCheckerboard: true,
    }))
  },
}))
