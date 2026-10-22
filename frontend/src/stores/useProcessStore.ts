import { create } from 'zustand'
import type { EditorSessionId } from '../types/editor'
import { createApiClient } from '../services/apiClient'
import { rememberPendingTask } from '../services/taskRecoveryStorage'
import { useBackendStore } from './useBackendStore'

export interface ProcessSnapshot {
  isProcessing: boolean
  progress: number
  statusMessage: string
  currentModel: string
  currentTaskId: string | null
}

interface ProcessTask extends ProcessSnapshot {
  backendURL: string
  sessionId: EditorSessionId
  status: 'running' | 'succeeded' | 'failed' | 'superseded'
}

const EMPTY_PROCESS: ProcessSnapshot = {
  isProcessing: false,
  progress: 0,
  statusMessage: '',
  currentModel: '',
  currentTaskId: null,
}

interface ProcessState {
  tasks: Record<string, ProcessTask>
  sessions: Partial<Record<EditorSessionId, ProcessSnapshot>>
  startProcess: (sessionId: EditorSessionId, model: string, taskId: string) => void
  updateProgress: (taskId: string, percent: number, message?: string) => void
  finishProcess: (taskId: string, message?: string) => void
  setError: (taskId: string, message: string) => void
  reset: (sessionId: EditorSessionId) => void
}

function isActive(state: ProcessState, taskId: string): boolean {
  const task = state.tasks[taskId]
  return !!task && task.status === 'running'
    && state.sessions[task.sessionId]?.currentTaskId === taskId
}

export const useProcessStore = create<ProcessState>((set) => {
  const updateTask = (taskId: string, changes: Partial<ProcessTask>) => set((state) => {
    if (!isActive(state, taskId)) return state
    const task = { ...state.tasks[taskId], ...changes }
    return {
      tasks: { ...state.tasks, [taskId]: task },
      sessions: { ...state.sessions, [task.sessionId]: task },
    }
  })

  return {
    tasks: {},
    sessions: {},
    startProcess: (sessionId, model, taskId) => set((state) => {
      const backendURL = createApiClient(useBackendStore.getState().backendURL).baseUrl
      rememberPendingTask({ taskId, sessionId, backendURL, createdAt: Date.now() })
      const tasks = { ...state.tasks }
      const previous = state.sessions[sessionId]?.currentTaskId
      if (previous && tasks[previous]) {
        tasks[previous] = { ...tasks[previous], isProcessing: false, status: 'superseded' }
      }
      // Keep bounded terminal history without dropping another session's running task.
      const terminalIds = Object.keys(tasks).filter((id) => tasks[id].status !== 'running')
      terminalIds.slice(0, Math.max(0, terminalIds.length - 100)).forEach((id) => delete tasks[id])
      const task: ProcessTask = {
        ...EMPTY_PROCESS,
        sessionId,
        backendURL,
        currentModel: model,
        currentTaskId: taskId,
        isProcessing: true,
        statusMessage: '正在初始化...',
        status: 'running',
      }
      return { tasks: { ...tasks, [taskId]: task }, sessions: { ...state.sessions, [sessionId]: task } }
    }),
    updateProgress: (taskId, progress, message) => set((state) => {
      if (!isActive(state, taskId)) return state
      const task = { ...state.tasks[taskId], progress, statusMessage: message || state.tasks[taskId].statusMessage }
      return { tasks: { ...state.tasks, [taskId]: task }, sessions: { ...state.sessions, [task.sessionId]: task } }
    }),
    finishProcess: (taskId, message) => {
      // Keep the recent task ID so a refresh after completion can still retrieve its cache.
      updateTask(taskId, {
        isProcessing: false, progress: 100, currentTaskId: null,
        statusMessage: message || '处理完成', status: 'succeeded',
      })
    },
    setError: (taskId, message) => updateTask(taskId, {
      isProcessing: false, progress: -1, currentTaskId: null,
      statusMessage: `错误: ${message}`, status: 'failed',
    }),
    reset: (sessionId) => set((state) => {
      const taskId = state.sessions[sessionId]?.currentTaskId
      return {
        sessions: { ...state.sessions, [sessionId]: EMPTY_PROCESS },
        tasks: taskId && state.tasks[taskId]
          ? { ...state.tasks, [taskId]: { ...state.tasks[taskId], isProcessing: false, status: 'superseded' } }
          : state.tasks,
      }
    }),
  }
})

export function isCurrentTask(taskId: string): boolean {
  return isActive(useProcessStore.getState(), taskId)
}

const actions = (sessionId: EditorSessionId) => ({
  startProcess: (model: string, taskId: string) => useProcessStore.getState().startProcess(sessionId, model, taskId),
  finishProcess: (taskId: string, message?: string) => useProcessStore.getState().finishProcess(taskId, message),
  setError: (taskId: string, message: string) => useProcessStore.getState().setError(taskId, message),
  reset: () => useProcessStore.getState().reset(sessionId),
})

const sessionActions = {
  watermark: actions('watermark'),
  upscale: actions('upscale'),
  'upscale-batch': actions('upscale-batch'),
  synthesis: actions('synthesis'),
  cutout: actions('cutout'),
}

export function useSessionProcess(sessionId: EditorSessionId) {
  const snapshot = useProcessStore((state) => state.sessions[sessionId] ?? EMPTY_PROCESS)
  return { ...snapshot, ...sessionActions[sessionId] }
}
