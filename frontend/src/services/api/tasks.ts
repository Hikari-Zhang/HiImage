import type { ApiClient } from '../apiClient'

export interface TaskSnapshot {
  task_id: string
  operation: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'interrupted'
  progress: number
  message: string
  // Unix timestamps in seconds. Records and cached results do not survive backend restarts.
  created_at: number
  updated_at: number
  finished_at: number | null
  http_status: number | null
  result_available?: boolean
}

export function createTaskAPI(client: ApiClient) {
  return {
    getTask: (taskId: string, signal?: AbortSignal) => client.json<TaskSnapshot>(
      `/api/tasks/${encodeURIComponent(taskId)}`, { cache: 'no-store', signal }, '读取任务状态失败',
    ),
    getTaskResult: (taskId: string, signal?: AbortSignal) => client.json<unknown>(
      `/api/tasks/${encodeURIComponent(taskId)}/result`, { cache: 'no-store', signal }, '取回任务结果失败',
    ),
  }
}
