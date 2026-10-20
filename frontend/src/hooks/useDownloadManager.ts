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

import { useEffect, useCallback, useRef } from 'react'
import { useBackendStore } from '../stores/useBackendStore'
import { useDownloadStore } from '../stores/useDownloadStore'
import { showToast } from '../components/ui'
import { DownloadStatus, ModelStatus, ApiPath } from '../constants'
import type { DownloadTask } from '../stores/useDownloadStore'
import { createApiClient } from '../services/apiClient'

// ── 模块级单例（跨组件共享，整个应用生命周期只有一份） ─────────────────────────

/** model_id → EventSource。全局唯一，不随组件卸载而销毁。 */
const _sseMap = new Map<string, EventSource>()

/** 已取消的 modelId 集合，用于防止 startDownload 的 POST 回调覆盖 cancelled 状态 */
const _cancelledSet = new Set<string>()

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

/** SSE 重试计数器：model_id → 已重试次数 */
const _sseRetryCount = new Map<string, number>()
/** SSE 最大重试次数（对 queued 状态的任务宽容一些） */
const SSE_MAX_RETRIES = 3
/** SSE 重试间隔 (ms) */
const SSE_RETRY_DELAY = 2000

/** 建立或复用 SSE 连接（全局单例，不重复建立）。 */
function ensureSubscribed(modelId: string, client: ReturnType<typeof createApiClient>) {
  const existing = _sseMap.get(modelId)
  if (existing && existing.readyState !== EventSource.CLOSED) {
    console.log(`[SSE] 已有连接复用: ${modelId} readyState=${existing.readyState}`)
    return
  }

  const esUrl = client.sseUrl(`${ApiPath.MODELS_SUBSCRIBE}/${encodeURIComponent(modelId)}`)
  console.log(`[SSE] 建立连接: ${esUrl}`)
  const es = new EventSource(esUrl)
  _sseMap.set(modelId, es)

  es.onopen = () => {
    console.log(`[SSE] 连接已打开: ${modelId}`)
    // 连接成功，重置重试计数
    _sseRetryCount.delete(modelId)
  }

  es.addEventListener('status', (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data) as Record<string, unknown>
      const patch = normalizeTask(data)
      console.log(`[SSE] 收到 status 事件: ${modelId} status=${patch.status}`, data)

      // 收到有效事件，重置重试计数
      _sseRetryCount.delete(modelId)
      useDownloadStore.getState().setTask(modelId, patch)

      if (patch.status === DownloadStatus.DONE) {
        console.log(`[SSE] 下载完成: ${modelId}`)
        showToast('success', `${patch.modelName || modelId} 下载完成`)
        es.close()
        _sseMap.delete(modelId)
        _syncOne(modelId)
      }

      if (patch.status === DownloadStatus.ERROR) {
        console.log(`[SSE] 下载失败: ${modelId} msg=${patch.message}`)
        showToast('error', `${patch.modelName || modelId} 下载失败: ${patch.message}`)
        es.close()
        _sseMap.delete(modelId)
      }

      if (patch.status === DownloadStatus.CANCELLED) {
        es.close()
        _sseMap.delete(modelId)
      }
    } catch (err) {
      console.error('[SSE] 解析事件失败:', err)
    }
  })

  es.onerror = (e) => {
    console.error(`[SSE] 连接错误: ${modelId}`, e)
    es.close()
    _sseMap.delete(modelId)

    const current = useDownloadStore.getState().getTask(modelId)
    // 对于 queued/downloading 状态的任务，尝试重连而不是立即标记为 error
    if (current?.status === DownloadStatus.DOWNLOADING || current?.status === DownloadStatus.QUEUED) {
      const retries = _sseRetryCount.get(modelId) ?? 0
      if (retries < SSE_MAX_RETRIES) {
        _sseRetryCount.set(modelId, retries + 1)
        console.log(`[SSE] 将在 ${SSE_RETRY_DELAY}ms 后重试 (${retries + 1}/${SSE_MAX_RETRIES}): ${modelId}`)
        setTimeout(() => {
          // 重连前再次检查状态，如果已经不再是 queued/downloading 就不重连
          const latest = useDownloadStore.getState().getTask(modelId)
          if (latest?.status === DownloadStatus.QUEUED || latest?.status === DownloadStatus.DOWNLOADING) {
            ensureSubscribed(modelId, client)
          }
        }, SSE_RETRY_DELAY)
      } else {
        // 超过重试次数，标记为 error
        console.log(`[SSE] 重试次数耗尽，标记为 error: ${modelId}`)
        _sseRetryCount.delete(modelId)
        useDownloadStore.getState().setTask(modelId, { status: DownloadStatus.ERROR, message: '连接中断，请重试' })
      }
    }
  }
}

/** 下载完成后立即重查单个模型状态，把 store 里的 done → ok。 */
async function _syncOne(modelId: string) {
  try {
    const client = createApiClient(useBackendStore.getState().backendURL)
    const res = await client.fetch(`${ApiPath.MODELS_HEALTH}/${encodeURIComponent(modelId)}`)
    if (!res.ok) return
    const data = await res.json() as { status: string; name?: string }
    useDownloadStore.getState().setTask(modelId, {
      status: data.status === ModelStatus.OK ? ModelStatus.OK : ModelStatus.MISSING,
      speed: '', downloaded: '', totalSize: '', message: '',
    })
  } catch {
    // 忽略网络错误
  }
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useDownloadManager() {
  const { backendURL } = useBackendStore()

  const getClient = useCallback(() => createApiClient(backendURL), [backendURL])

  // ── 同步所有模型文件状态（从后端拉取 ok/missing，写入 store） ──────────────

  const syncModelStatus = useCallback(async () => {
    const client = getClient()
    try {
      const res = await client.fetch(ApiPath.MODELS_LIST)
      if (!res.ok) return
      const data = await res.json() as {
        models?: Array<{ id: string; name: string; status: string }>
      }
      // 用 getState() 避免订阅 store 导致 re-render
      const { getTask, setTask } = useDownloadStore.getState()
      for (const m of data.models ?? []) {
        const current = getTask(m.id)
        if (
          current?.status === DownloadStatus.QUEUED ||
          current?.status === DownloadStatus.DOWNLOADING ||
          current?.status === DownloadStatus.DONE
        ) continue
        setTask(m.id, {
          modelId:    m.id,
          modelName:  m.name,
          status:     m.status === ModelStatus.OK ? ModelStatus.OK : ModelStatus.MISSING,
          message:    '',
          speed:      '',
          downloaded: '',
          totalSize:  '',
        })
      }
    } catch {
      // 网络不通时忽略
    }
  }, [getClient])

  // ── 单模型下载 ──────────────────────────────────────────────────────────────

  const startDownload = useCallback(async (modelId: string) => {
    const { getTask, setTask } = useDownloadStore.getState()
    const current = getTask(modelId)
    console.log(`[startDownload] modelId=${modelId} current=`, current)

    if (current?.status === DownloadStatus.QUEUED || current?.status === DownloadStatus.DOWNLOADING) {
      console.log(`[startDownload] 已在队列，只恢复 SSE: ${modelId}`)
      const client = getClient()
      ensureSubscribed(modelId, client)
      return
    }

    // 清除取消标记（用户重新发起下载）
    _cancelledSet.delete(modelId)

    // 先重置为 queued 状态，给用户即时反馈并避免 SSE onerror 竞态
    // 计算临时排队位置：当前队列中 queued/downloading 的任务数 + 1
    const allTasks = useDownloadStore.getState().tasks
    const queuedCount = Object.values(allTasks).filter(
      (t) => t.status === DownloadStatus.QUEUED || t.status === DownloadStatus.DOWNLOADING
    ).length
    setTask(modelId, { status: DownloadStatus.QUEUED, position: queuedCount + 1, message: '提交中...', speed: '', downloaded: '', totalSize: '' })

    const client = getClient()
    console.log(`[startDownload] POST ${client.url(`${ApiPath.MODELS_DOWNLOAD}/${modelId}`)}`)
    try {
      const res = await client.fetch(`${ApiPath.MODELS_DOWNLOAD}/${encodeURIComponent(modelId)}`, {
        method: 'POST',
      })
      // POST 返回后检查是否已被取消
      if (_cancelledSet.has(modelId)) {
        console.log(`[startDownload] POST 返回但已被取消，忽略: ${modelId}`)
        _cancelledSet.delete(modelId)
        return
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({})) as Record<string, unknown>
        throw new Error((body.detail as string) || `提交下载失败 (${res.status})`)
      }
      const task = await res.json() as Record<string, unknown>
      console.log(`[startDownload] POST 响应:`, task)
      // 再次检查（json 解析也是异步的）
      if (_cancelledSet.has(modelId)) {
        console.log(`[startDownload] 解析响应后已被取消，忽略: ${modelId}`)
        _cancelledSet.delete(modelId)
        return
      }
      setTask(modelId, normalizeTask(task))
      ensureSubscribed(modelId, client)
    } catch (err: unknown) {
      // 如果已被取消，不设置 error 状态
      if (_cancelledSet.has(modelId)) {
        _cancelledSet.delete(modelId)
        return
      }
      const msg = err instanceof Error ? err.message : String(err)
      console.error(`[startDownload] 失败:`, msg)
      setTask(modelId, { status: DownloadStatus.ERROR, message: msg })
      showToast('error', `下载失败: ${msg}`)
    }
  }, [getClient])

  // ── 取消单模型下载 ──────────────────────────────────────────────────────────

  const cancelDownload = useCallback(async (modelId: string) => {
    // 标记为已取消，防止 startDownload 的 POST 回调覆盖状态
    _cancelledSet.add(modelId)

    const es = _sseMap.get(modelId)
    if (es) {
      es.close()
      _sseMap.delete(modelId)
    }
    _sseRetryCount.delete(modelId)

    // 先立即更新前端状态
    useDownloadStore.getState().setTask(modelId, { status: DownloadStatus.CANCELLED, message: '', speed: '', downloaded: '' })

    // 再通知后端取消
    const client = getClient()
    try {
      await client.fetch(`${ApiPath.MODELS_DOWNLOAD}/${encodeURIComponent(modelId)}`, {
        method: 'DELETE',
      })
    } catch {
      // 忽略
    }
  }, [getClient])

  // ── 一键下载 ────────────────────────────────────────────────────────────────

  const startBulkDownload = useCallback(async () => {
    const client = getClient()
    try {
      const res = await client.fetch(`${ApiPath.MODELS_DOWNLOAD}/bulk`, { method: 'POST' })
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
          if (task.status === DownloadStatus.QUEUED || task.status === DownloadStatus.DOWNLOADING) {
            ensureSubscribed(task.modelId, client)
          }
        }
      }

      setBulkSession({ active: true, modelIds })
      showToast('info', `已添加 ${data.submitted} 个模型到下载队列`)

    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      showToast('error', `一键下载失败: ${msg}`)
    }
  }, [getClient])

  // ── 取消一键下载 ────────────────────────────────────────────────────────────

  const cancelBulkDownload = useCallback(async () => {
    const { bulkSession } = useDownloadStore.getState()
    for (const modelId of bulkSession.modelIds) {
      const task = useDownloadStore.getState().getTask(modelId)
      if (task?.status === DownloadStatus.QUEUED || task?.status === DownloadStatus.DOWNLOADING) {
        await cancelDownload(modelId)
      }
    }
    useDownloadStore.getState().clearBulkSession()
  }, [cancelDownload])

  // ── 恢复进行中的 SSE 连接 ──────────────────────────────────────────────────

  const resume = useCallback(async () => {
    const client = getClient()
    const { tasks } = useDownloadStore.getState()
    for (const [modelId, task] of Object.entries(tasks)) {
      if (task.status === DownloadStatus.QUEUED || task.status === DownloadStatus.DOWNLOADING) {
        ensureSubscribed(modelId, client)
      }
    }
  }, [getClient])

  // ── 挂载时：同步状态 + 恢复连接 ────────────────────────────────────────────

  const syncDoneRef = useRef(false)

  useEffect(() => {
    if (!syncDoneRef.current) {
      syncDoneRef.current = true
      syncModelStatus()
    }
    resume()

    // 监听后端重启事件，重新同步状态（后端重启后 _syncDone 重置，避免状态停留在旧快照）
    return () => {}
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
