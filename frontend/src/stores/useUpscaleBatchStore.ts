import { create } from 'zustand'
import type { ImageQueueItem } from '../components/BatchImageQueue'

interface UpscaleBatchState {
  mode: 'single' | 'batch'
  batchItems: ImageQueueItem[]
  batchCurrentIndex: number | null
  isBatchProcessing: boolean
  setMode: (mode: 'single' | 'batch') => void
  setBatchItems: (items: ImageQueueItem[] | ((previous: ImageQueueItem[]) => ImageQueueItem[])) => void
  setBatchCurrentIndex: (index: number | null) => void
  setIsBatchProcessing: (processing: boolean) => void
}

export const useUpscaleBatchStore = create<UpscaleBatchState>((set) => ({
  mode: 'single',
  batchItems: [],
  batchCurrentIndex: null,
  isBatchProcessing: false,
  setMode: (mode) => set({ mode }),
  setBatchItems: (items) => set((state) => ({
    batchItems: typeof items === 'function' ? items(state.batchItems) : items,
  })),
  setBatchCurrentIndex: (batchCurrentIndex) => set({ batchCurrentIndex }),
  setIsBatchProcessing: (isBatchProcessing) => set({ isBatchProcessing }),
}))
