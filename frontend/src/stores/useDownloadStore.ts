/**
 * 下载状态仓库 —— 独立管理所有模型下载任务状态。
 *
 * 与 useSettingsStore 完全分离，职责明确：
 *   - 单个模型下载进度（queued / downloading / done / error / cancelled）
 *   - 一键下载批次会话（本次包含的模型列表）
 *
 * 状态跨页签持久（Zustand 内存级），页面切换不会丢失进度。
 */

import { create } from 'zustand'
import { DownloadStatus, ModelStatus } from '../constants'
import type { DownloadStatusValue, ModelStatusValue } from '../constants'
import { createApiClient } from '../services/apiClient'
import { useBackendStore } from './useBackendStore'

// ── 类型定义 ──────────────────────────────────────────────────────────────────

/**
 * 模型状态：
 *   ok        - 文件已存在，可直接使用
 *   missing   - 文件缺失，需要下载
 *   queued    - 已加入下载队列，等待槽位
 *   downloading - 正在下载
 *   done      - 本次下载完成（文件已就绪）
 *   error     - 下载失败
 *   cancelled - 已取消
 *
 * 使用 constants.ts 中的 DownloadStatus / ModelStatus 替代字面量比较。
 */
export type TaskStatus = DownloadStatusValue | ModelStatusValue

/** 单个模型下载任务状态（与后端 DownloadTask.to_dict() 对应） */
export type DownloadTask = {
  modelId: string
  modelName: string
  downloadId?: string  // 本次下载实例 ID；取消重试时必须更换
  statusSyncFailed?: boolean // 仅表示状态连接失败，不代表后端下载失败
  status: TaskStatus
  position: number      // queued 时的排队序号（1-based），downloading/done 时为 0
  message: string
  speed: string         // 下载速度，如 "1.2 MB/s"
  downloaded: string    // 已下载大小，如 "32 MB"
  totalSize: string     // 总大小，如 "65 MB"
  updatedAt: number     // 最近更新时间戳（ms）
}

/** 一键下载批次会话 */
export type BulkSession = {
  active: boolean
  modelIds: string[]   // 本次一键下载包含的全部 modelId
}

interface DownloadStoreState {
  /** 所有活跃/已完成任务，key = modelId */
  tasks: Record<string, DownloadTask>

  /** 当前一键下载批次会话 */
  bulkSession: BulkSession

  // ── Actions ────────────────────────────────────────────────────────────────

  /** 更新/创建指定模型的任务状态 */
  setTask: (modelId: string, patch: Partial<DownloadTask>) => void

  /** 移除指定模型的任务记录（通常不需要，保留接口） */
  removeTask: (modelId: string) => void

  /** 清除所有 done/error/cancelled 状态的历史任务 */
  clearFinished: () => void

  /** 更新一键下载批次会话 */
  setBulkSession: (patch: Partial<BulkSession>) => void

  /** 清除一键下载批次会话 */
  clearBulkSession: () => void

  /** 获取指定模型的任务状态（如不存在返回 undefined） */
  getTask: (modelId: string) => DownloadTask | undefined
  syncFiles: (models: ReadonlyArray<{ id: string; name: string; status: ModelStatusValue; message: string }>) => void
}

// ── Store 实现 ────────────────────────────────────────────────────────────────

function updateTask(modelId: string, existing: DownloadTask | undefined, patch: Partial<DownloadTask>): DownloadTask {
  return {
    modelName: modelId, position: 0, status: DownloadStatus.QUEUED,
    message: '', speed: '', downloaded: '', totalSize: '',
    ...existing, ...patch, modelId, updatedAt: Date.now(),
  }
}

const emptyBulkSession = (): BulkSession => ({ active: false, modelIds: [] })

export const useDownloadStore = create<DownloadStoreState>((set, get) => ({
  tasks: {},
  bulkSession: emptyBulkSession(),

  setTask: (modelId, patch) =>
    set((state) => {
      return {
        tasks: {
          ...state.tasks,
          [modelId]: updateTask(modelId, state.tasks[modelId], patch),
        },
      }
    }),

  removeTask: (modelId) =>
    set((state) => {
      const next = { ...state.tasks }
      delete next[modelId]
      return { tasks: next }
    }),

  clearFinished: () =>
    set((state) => {
      const next: Record<string, DownloadTask> = {}
      for (const [id, task] of Object.entries(state.tasks)) {
        if (task.statusSyncFailed || (task.status !== DownloadStatus.DONE && task.status !== DownloadStatus.ERROR && task.status !== DownloadStatus.CANCELLED)) {
          next[id] = task
        }
      }
      return { tasks: next }
    }),

  setBulkSession: (patch) =>
    set((state) => ({
      bulkSession: { ...state.bulkSession, ...patch },
    })),

  clearBulkSession: () =>
    set({ bulkSession: { active: false, modelIds: [] } }),

  getTask: (modelId) => get().tasks[modelId],

  syncFiles: (models) => set((state) => {
    const tasks = { ...state.tasks }
    let changed = false
    for (const { id, name, status, message } of models) {
      const task = tasks[id]
      if (task?.statusSyncFailed || task?.status === DownloadStatus.QUEUED
        || task?.status === DownloadStatus.DOWNLOADING || task?.status === DownloadStatus.DONE
        || (task?.status === DownloadStatus.ERROR || task?.status === DownloadStatus.CANCELLED) && status !== ModelStatus.OK) continue
      if (task?.status === status && task.modelName === name && task.message === message) continue
      tasks[id] = updateTask(id, task, { modelName: name, status, message, speed: '', downloaded: '', totalSize: '' })
      changed = true
    }
    return changed ? { tasks } : state
  }),
}))

type Downloads = Pick<DownloadStoreState, 'tasks' | 'bulkSession'>
const downloadsByBackend = new Map<string, Downloads>()
let currentBackend = createApiClient(useBackendStore.getState().backendURL).baseUrl

useBackendStore.subscribe(({ backendURL }) => {
  const next = createApiClient(backendURL).baseUrl
  if (next === currentBackend) return
  const { tasks, bulkSession } = useDownloadStore.getState()
  downloadsByBackend.set(currentBackend, { tasks, bulkSession })
  currentBackend = next
  useDownloadStore.setState(downloadsByBackend.get(next) ?? { tasks: {}, bulkSession: emptyBulkSession() })
})

export function clearDeletedModelTasks(backend: string, modelIds: string[]) {
  const current = backend === currentBackend
  const downloads = current ? useDownloadStore.getState() : downloadsByBackend.get(backend)
  if (!downloads) return
  const tasks = { ...downloads.tasks }
  for (const id of modelIds) {
    const status = tasks[id]?.status
    if (status !== DownloadStatus.QUEUED && status !== DownloadStatus.DOWNLOADING) delete tasks[id]
  }
  if (current) useDownloadStore.setState({ tasks })
  else downloadsByBackend.set(backend, { tasks, bulkSession: downloads.bulkSession })
}
