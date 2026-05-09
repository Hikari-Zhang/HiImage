/**
 * useDownloadManager —— 下载管理器 Hook。
 *
 * 架构说明：
 *   SSE 连接和 Toast 通知放在模块级单例（非 Hook 内部），
 *   确保同一个模型无论被多少个组件订阅，都只建立一条 SSE 连接、只弹一次 Toast。
 *
 *   Hook 本身只负责暴露操作函数（startDownload / cancelDownload / ...），
 *   以及在挂载时触发 syncModelStatus 同步后端文件状态。
 */

import { useEffect, useCallback } from 'react'
import { useBackendStore } from '../stores/useBackendStore'
import { useDownloadStore } from '../stores/useDownloadStore'
import { showToast } from '../components/ui'
import type { DownloadTask } from '../stores/useDownloadStore'

// ── 模块级单例（跨组件共享，整个应用生命周期只有一份） ─────────────────────────

/** model_id → EventSource。全局唯一，不随组件卸载而销毁。 */
const _sseMap = new Map<string, EventSource>()

/** 确保 syncModelStatus 只在应用启动时执行一次 */
let _syncDone = false
let _onDownloadDone: (() => void) | null = null

/** 注册下载完成回调（ModelManager 用于刷新模型列表） */
export function registerDownloadDoneCallback(fn: () => void) {
  _onDownloadDone = fn
}

// ── 辅助函数 ──────────────────────────────────────────────────────────────────

function normalizeTask(data: Record<string, unknown>): Partial<DownloadTask> {
  return {
    modelId:    (data.modelId   ?? data.model_id)   as string,
    modelName:  (data.modelName ?? data.model_name) as string,
    status:     data.status    as DownloadTask['status'],
    position:   (data.position ?? 0)               as number,
    message:    (data.message  ?? '')              as string,
    speed:      (data.speed    ?? '')              as string,
    downloaded: (data.downloaded ?? '')            as string,
    totalSize:  (data.totalSize ?? data.total_size ?? '') as string,
  }
}

/** 建立或复用 SSE 连接（全局单例，不重复建立）。 */
function ensureSubscribed(modelId: string, url: string) {
  const existing = _sseMap.get(modelId)
  if (existing && existing.readyState !== EventSource.CLOSED) {
    console.log(`[SSE] 已有连接复用: ${modelId} readyState=${existing.readyState}`)
    return
  }

  const esUrl = `${url}/api/models/subscribe/${encodeURIComponent(modelId)}`
  console.log(`[SSE] 建立连接: ${esUrl}`)
  const es = new EventSource(esUrl)
  _sseMap.set(modelId, es)

  es.onopen = () => {
    console.log(`[SSE] 连接已打开: ${modelId}`)
  }

  es.addEventListener('status', (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data) as Record<string, unknown>
      const patch = normalizeTask(data)
      console.log(`[SSE] 收到 status 事件: ${modelId} status=${patch.status}`, data)

      useDownloadStore.getState().setTask(modelId, patch)

      if (patch.status === 'done') {
        console.log(`[SSE] 下载完成: ${modelId}`)
        showToast('success', `${patch.modelName || modelId} 下载完成`)
        es.close()
        _sseMap.delete(modelId)
        _syncOne(modelId)
        setTimeout(() => _onDownloadDone?.(), 500)
      }

      if (patch.status === 'error') {
        console.log(`[SSE] 下载失败: ${modelId} msg=${patch.message}`)
        showToast('error', `${patch.modelName || modelId} 下载失败: ${patch.message}`)
        es.close()
        _sseMap.delete(modelId)
      }

      if (patch.status === 'cancelled') {
        es.close()
        _sseMap.delete(modelId)
      }
    } catch (err) {
      console.error('[SSE] 解析事件失败:', err)
    }
  })

  es.onerror = (e) => {
    console.error(`[SSE] 连接错误: ${modelId}`, e)
    const current = useDownloadStore.getState().getTask(modelId)
    if (current?.status === 'downloading' || current?.status === 'queued') {
      useDownloadStore.getState().setTask(modelId, { status: 'error', message: '连接中断，请重试' })
    }
    es.close()
    _sseMap.delete(modelId)
  }
}

/** 下载完成后立即重查单个模型状态，把 store 里的 done → ok。 */
async function _syncOne(modelId: string) {
  try {
    const backendURL = useBackendStore.getState().backendURL
    const url = window.electronAPI?.getBackendURL
      ? await window.electronAPI.getBackendURL()
      : backendURL
    const res = await fetch(`${url}/api/models/health/${encodeURIComponent(modelId)}`)
    if (!res.ok) return
    const data = await res.json() as { status: string; name?: string }
    useDownloadStore.getState().setTask(modelId, {
      status: data.status === 'ok' ? 'ok' : 'missing',
      speed: '', downloaded: '', totalSize: '', message: '',
    })
  } catch {
    // 忽略网络错误
  }
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useDownloadManager() {
  const { backendURL } = useBackendStore()

  const getURL = useCallback(async (): Promise<string> => {
    if (window.electronAPI?.getBackendURL) {
      return window.electronAPI.getBackendURL()
    }
    return backendURL
  }, [backendURL])

  // ── 同步所有模型文件状态（从后端拉取 ok/missing，写入 store） ──────────────

  const syncModelStatus = useCallback(async () => {
    const url = await getURL()
    try {
      const res = await fetch(`${url}/api/models/list`)
      if (!res.ok) return
      const data = await res.json() as {
        models?: Array<{ id: string; name: string; status: string }>
      }
      // 用 getState() 避免订阅 store 导致 re-render
      const { getTask, setTask } = useDownloadStore.getState()
      for (const m of data.models ?? []) {
        const current = getTask(m.id)
        if (
          current?.status === 'queued' ||
          current?.status === 'downloading' ||
          current?.status === 'done'
        ) continue
        setTask(m.id, {
          modelId:    m.id,
          modelName:  m.name,
          status:     m.status === 'ok' ? 'ok' : 'missing',
          message:    '',
          speed:      '',
          downloaded: '',
          totalSize:  '',
        })
      }
    } catch {
      // 网络不通时忽略
    }
  }, [getURL])

  // ── 单模型下载 ──────────────────────────────────────────────────────────────

  const startDownload = useCallback(async (modelId: string) => {
    const { getTask, setTask } = useDownloadStore.getState()
    const current = getTask(modelId)
    console.log(`[startDownload] modelId=${modelId} current=`, current)

    if (current?.status === 'queued' || current?.status === 'downloading') {
      console.log(`[startDownload] 已在队列，只恢复 SSE: ${modelId}`)
      const url = await getURL()
      ensureSubscribed(modelId, url)
      return
    }

    const url = await getURL()
    console.log(`[startDownload] POST ${url}/api/models/download/${modelId}`)
    try {
      const res = await fetch(`${url}/api/models/download/${encodeURIComponent(modelId)}`, {
        method: 'POST',
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({})) as Record<string, unknown>
        throw new Error((body.detail as string) || `提交下载失败 (${res.status})`)
      }
      const task = await res.json() as Record<string, unknown>
      console.log(`[startDownload] POST 响应:`, task)
      setTask(modelId, normalizeTask(task))
      ensureSubscribed(modelId, url)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      console.error(`[startDownload] 失败:`, msg)
      setTask(modelId, { status: 'error', message: msg })
      showToast('error', `下载失败: ${msg}`)
    }
  }, [getURL])

  // ── 取消单模型下载 ──────────────────────────────────────────────────────────

  const cancelDownload = useCallback(async (modelId: string) => {
    const es = _sseMap.get(modelId)
    if (es) {
      es.close()
      _sseMap.delete(modelId)
    }

    const url = await getURL()
    try {
      await fetch(`${url}/api/models/download/${encodeURIComponent(modelId)}`, {
        method: 'DELETE',
      })
    } catch {
      // 忽略
    }

    useDownloadStore.getState().setTask(modelId, { status: 'missing', message: '', speed: '', downloaded: '' })
  }, [getURL])

  // ── 一键下载 ────────────────────────────────────────────────────────────────

  const startBulkDownload = useCallback(async () => {
    const url = await getURL()
    try {
      const res = await fetch(`${url}/api/models/download/bulk`, { method: 'POST' })
      if (!res.ok) {
        const body = await res.json().catch(() => ({})) as Record<string, unknown>
        throw new Error((body.detail as string) || `一键下载提交失败 (${res.status})`)
      }
      const data = await res.json() as { submitted: number; tasks: Record<string, unknown>[] }

      if (data.submitted === 0) {
        showToast('success', '所有模型均已下载，无需重复下载')
        return
      }

      const { setTask, setBulkSession } = useDownloadStore.getState()
      const modelIds: string[] = []
      for (const taskData of data.tasks) {
        const task = normalizeTask(taskData)
        if (task.modelId) {
          setTask(task.modelId, task)
          modelIds.push(task.modelId)
          if (task.status === 'queued' || task.status === 'downloading') {
            ensureSubscribed(task.modelId, url)
          }
        }
      }

      setBulkSession({ active: true, modelIds })
      showToast('info', `已添加 ${data.submitted} 个模型到下载队列`)

    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      showToast('error', `一键下载失败: ${msg}`)
    }
  }, [getURL])

  // ── 取消一键下载 ────────────────────────────────────────────────────────────

  const cancelBulkDownload = useCallback(async () => {
    const { bulkSession } = useDownloadStore.getState()
    for (const modelId of bulkSession.modelIds) {
      const task = useDownloadStore.getState().getTask(modelId)
      if (task?.status === 'queued' || task?.status === 'downloading') {
        await cancelDownload(modelId)
      }
    }
    useDownloadStore.getState().clearBulkSession()
  }, [cancelDownload])

  // ── 恢复进行中的 SSE 连接 ──────────────────────────────────────────────────

  const resume = useCallback(async () => {
    const url = await getURL()
    const { tasks } = useDownloadStore.getState()
    for (const [modelId, task] of Object.entries(tasks)) {
      if (task.status === 'queued' || task.status === 'downloading') {
        ensureSubscribed(modelId, url)
      }
    }
  }, [getURL])

  // ── 挂载时：同步状态 + 恢复连接 ────────────────────────────────────────────

  useEffect(() => {
    if (!_syncDone) {
      _syncDone = true
      syncModelStatus()
    }
    resume()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return {
    startDownload,
    cancelDownload,
    startBulkDownload,
    cancelBulkDownload,
    resume,
    syncModelStatus,
  }
}
