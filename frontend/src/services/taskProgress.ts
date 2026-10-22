import { apiClient } from './apiClient'
import { ManagedWebSocket } from './managedWebSocket'
import { useProcessStore } from '../stores/useProcessStore'

type UpdateProgress = (taskId: string, percent: number, message?: string) => void

export function applyTaskProgressMessage(
  taskId: string,
  rawMessage: string,
  updateProgress: UpdateProgress,
): void {
  const message = JSON.parse(rawMessage)
  if (!message || typeof message !== 'object' || Array.isArray(message)) return
  if (message.task_id !== undefined && message.task_id !== taskId) return
  const text = typeof message.message === 'string' ? message.message : ''
  if (message.type === 'progress' && typeof message.percent === 'number'
    && Number.isFinite(message.percent) && message.percent >= 0 && message.percent <= 100) {
    updateProgress(taskId, message.percent, text)
  } else if (message.type === 'complete') {
    updateProgress(taskId, 100, text || '后端处理完成，正在接收结果...')
  } else if (message.type === 'error') {
    updateProgress(taskId, -1, text || '后端报告处理错误')
  }
}

export type ProgressConnection = {
  startAndWait: (timeoutMs?: number) => Promise<void>
  dispose: () => void
}

type ProgressConnectionHandlers = {
  onMessage: (event: MessageEvent) => void
  onError: () => void
}

export type ProgressConnectionFactory = (
  url: string,
  handlers: ProgressConnectionHandlers,
) => ProgressConnection

type TaskProgressSessionOptions = {
  isCurrent?: () => boolean
  createConnection?: ProgressConnectionFactory
  websocketUrl?: (taskId: string) => string
  updateProgress?: UpdateProgress
  openTimeoutMs?: number
}

export type TaskProgressSession = {
  taskId: string
  run: <T>(request: (taskId: string) => Promise<T>) => Promise<T>
}

export function createTaskProgressSession(
  taskId: string,
  options: TaskProgressSessionOptions = {},
): TaskProgressSession {
  const updateProgress = options.updateProgress ?? useProcessStore.getState().updateProgress
  const websocketUrl = options.websocketUrl
    ?? ((id: string) => apiClient.websocketUrl(`/api/ws/progress?task_id=${encodeURIComponent(id)}`))
  const createConnection = options.createConnection
    ?? ((url: string, handlers: ProgressConnectionHandlers) => new ManagedWebSocket(url, handlers))
  let started = false
  let active = false
  const handlers: ProgressConnectionHandlers = {
    onMessage: (event) => {
      if (!active || (options.isCurrent && !options.isCurrent())) return
      try {
        applyTaskProgressMessage(taskId, event.data, updateProgress)
      } catch (error) {
        console.error('Failed to parse progress message:', error)
      }
    },
    onError: () => {
      if (active) console.error('Progress WebSocket error')
    },
  }

  return {
    taskId,
    run: async <T>(request: (requestTaskId: string) => Promise<T>): Promise<T> => {
      if (started) throw new Error('任务会话只能执行一次，请创建新任务重试')
      started = true
      let connection: ProgressConnection | undefined
      try {
        if (options.isCurrent && !options.isCurrent()) throw new Error('任务已被新任务替代')
        active = true
        try {
          connection = createConnection(websocketUrl(taskId), handlers)
          await connection.startAndWait(options.openTimeoutMs)
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error)
          throw new Error(`进度连接失败: ${message}`)
        }
        if (options.isCurrent && !options.isCurrent()) throw new Error('任务已被新任务替代')
        return await request(taskId)
      } finally {
        active = false
        connection?.dispose()
      }
    },
  }
}
