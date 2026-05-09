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
 */
export type TaskStatus = 'ok' | 'missing' | 'queued' | 'downloading' | 'done' | 'error' | 'cancelled'

/** 单个模型下载任务状态（与后端 DownloadTask.to_dict() 对应） */
export type DownloadTask = {
  modelId: string
  modelName: string
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
}

// ── Store 实现 ────────────────────────────────────────────────────────────────

export const useDownloadStore = create<DownloadStoreState>((set, get) => ({
  tasks: {},
  bulkSession: { active: false, modelIds: [] },

  setTask: (modelId, patch) =>
    set((state) => {
      const existing = state.tasks[modelId]
      const base: DownloadTask = existing ?? {
        modelId,
        modelName: modelId,
        status: 'queued' as TaskStatus,
        position: 0,
        message: '',
        speed: '',
        downloaded: '',
        totalSize: '',
        updatedAt: Date.now(),
      }
      return {
        tasks: {
          ...state.tasks,
          [modelId]: {
            ...base,
            ...patch,
            updatedAt: Date.now(),
          },
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
        if (task.status !== 'done' && task.status !== 'error' && task.status !== 'cancelled') {
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
}))
