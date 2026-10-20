import { create } from 'zustand'

interface ProcessState {
  isProcessing: boolean
  progress: number // 0-100, -1 for indeterminate
  statusMessage: string
  currentModel: string
  currentTaskId: string | null

  startProcess: (model: string, taskId: string) => void
  updateProgress: (taskId: string, percent: number, message?: string) => void
  finishProcess: (taskId: string, message?: string) => void
  setError: (taskId: string, message: string) => void
  reset: () => void
}

export const useProcessStore = create<ProcessState>((set) => ({
  isProcessing: false,
  progress: 0,
  statusMessage: '',
  currentModel: '',
  currentTaskId: null,

  startProcess: (model, taskId) =>
    set({
      isProcessing: true,
      progress: 0,
      statusMessage: '正在初始化...',
      currentModel: model,
      currentTaskId: taskId,
    }),

  updateProgress: (taskId, percent, message) =>
    set((state) => state.currentTaskId === taskId ? {
      progress: percent,
      statusMessage: message || state.statusMessage,
    } : state),

  finishProcess: (taskId, message) =>
    set((state) => state.currentTaskId === taskId ? {
      isProcessing: false,
      progress: 100,
      statusMessage: message || '处理完成',
      currentTaskId: null,
    } : state),

  setError: (taskId, message) =>
    set((state) => state.currentTaskId === taskId ? {
      isProcessing: false,
      progress: -1,
      statusMessage: `错误: ${message}`,
      currentTaskId: null,
    } : state),

  reset: () =>
    set({
      isProcessing: false,
      progress: 0,
      statusMessage: '',
      currentModel: '',
      currentTaskId: null,
    }),
}))

export function isCurrentTask(taskId: string): boolean {
  return useProcessStore.getState().currentTaskId === taskId
}
