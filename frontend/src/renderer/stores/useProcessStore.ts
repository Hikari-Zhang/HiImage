import { create } from 'zustand'

interface ProcessState {
  isProcessing: boolean
  progress: number // 0-100, -1 for indeterminate
  statusMessage: string
  currentModel: string

  startProcess: (model: string) => void
  updateProgress: (percent: number, message?: string) => void
  finishProcess: (message?: string) => void
  setError: (message: string) => void
  reset: () => void
}

export const useProcessStore = create<ProcessState>((set) => ({
  isProcessing: false,
  progress: 0,
  statusMessage: '',
  currentModel: '',

  startProcess: (model) =>
    set({
      isProcessing: true,
      progress: 0,
      statusMessage: '正在初始化...',
      currentModel: model,
    }),

  updateProgress: (percent, message) =>
    set((state) => ({
      progress: percent,
      statusMessage: message || state.statusMessage,
    })),

  finishProcess: (message) =>
    set({
      isProcessing: false,
      progress: 100,
      statusMessage: message || '处理完成',
    }),

  setError: (message) =>
    set({
      isProcessing: false,
      progress: -1,
      statusMessage: `错误: ${message}`,
    }),

  reset: () =>
    set({
      isProcessing: false,
      progress: 0,
      statusMessage: '',
      currentModel: '',
    }),
}))
