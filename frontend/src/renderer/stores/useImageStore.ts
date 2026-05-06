import { create } from 'zustand'

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

  setSourceImage: (image, filePath) =>
    set({
      sourceImage: image,
      sourceFilePath: filePath || null,
      resultImage: null,
      rois: [],
      selectedROIs: [],
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

  reset: () =>
    set({
      sourceImage: null,
      sourceFilePath: null,
      resultImage: null,
      imageWidth: 0,
      imageHeight: 0,
      rois: [],
      selectedROIs: [],
    }),
}))
