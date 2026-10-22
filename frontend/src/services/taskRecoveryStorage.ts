import { EDITOR_SESSION_IDS, type EditorSessionId } from '../types/editor'

export const TASK_RECOVERY_KEY = 'hiimage.task-recovery.v1'
const MAX_RECORDS = 20

export interface PendingTask {
  taskId: string
  sessionId: EditorSessionId
  backendURL: string
  createdAt: number
}

export function readPendingTasks(): PendingTask[] {
  try {
    const raw = sessionStorage.getItem(TASK_RECOVERY_KEY)
    if (!raw || raw.length > 32768) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    const records = new Map<string, PendingTask>()
    for (const item of parsed.slice(-MAX_RECORDS)) {
      if (!item || typeof item !== 'object'
        || typeof item.taskId !== 'string' || !item.taskId.trim() || item.taskId.length > 128
        || !EDITOR_SESSION_IDS.includes(item.sessionId)
        || typeof item.backendURL !== 'string' || item.backendURL.length > 2048
        || typeof item.createdAt !== 'number' || !Number.isFinite(item.createdAt) || item.createdAt < 0) continue
      const record: PendingTask = { taskId: item.taskId, sessionId: item.sessionId, backendURL: item.backendURL, createdAt: item.createdAt }
      records.set(JSON.stringify([record.backendURL, record.taskId]), record)
    }
    return [...records.values()]
  } catch { return [] }
}

function write(records: PendingTask[]) {
  try {
    if (records.length) sessionStorage.setItem(TASK_RECOVERY_KEY, JSON.stringify(records.slice(-MAX_RECORDS)))
    else sessionStorage.removeItem(TASK_RECOVERY_KEY)
  } catch { /* Unavailable storage must not prevent image processing. */ }
}

export function rememberPendingTask(task: PendingTask) {
  const records = readPendingTasks().filter((item) => item.backendURL !== task.backendURL || item.taskId !== task.taskId)
  // Serialize an explicit metadata allowlist, never request arguments or image data.
  write([...records, { taskId: task.taskId, sessionId: task.sessionId, backendURL: task.backendURL, createdAt: task.createdAt }])
}

export function forgetPendingTask(taskId: string, backendURL: string) {
  write(readPendingTasks().filter((item) => item.taskId !== taskId || item.backendURL !== backendURL))
}
