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
  if (message.type === 'progress' && typeof message.percent === 'number') {
    updateProgress(taskId, message.percent, message.message || '')
  } else if (message.type === 'complete') {
    updateProgress(taskId, 100, message.message || '后端处理完成，正在接收结果...')
  } else if (message.type === 'error') {
    updateProgress(taskId, -1, message.message || '后端报告处理错误')
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
  const connection = createConnection(websocketUrl(taskId), {
    onMessage: (event) => {
      try {
        applyTaskProgressMessage(taskId, event.data, updateProgress)
      } catch (error) {
        console.error('Failed to parse progress message:', error)
      }
    },
    onError: () => {
      console.error('Progress WebSocket error')
    },
  })

  return {
    taskId,
    run: async <T>(request: (requestTaskId: string) => Promise<T>): Promise<T> => {
      try {
        try {
          await connection.startAndWait(options.openTimeoutMs)
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error)
          throw new Error(`进度连接失败: ${message}`)
        }
        return await request(taskId)
      } finally {
        connection.dispose()
      }
    },
  }
}
