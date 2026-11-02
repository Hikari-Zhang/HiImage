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
import { createApiClient, readApiResponse, ensureApiSuccess } from '../services/apiClient'

// ── 模块级单例（跨组件共享，整个应用生命周期只有一份） ─────────────────────────

/** model_id → EventSource。全局唯一，不随组件卸载而销毁。 */
const _sseMap = new Map<string, EventSource>()

// 同一后端/模型的操作依次提交，避免慢 POST、DELETE 和重试乱序。
const _generations = new Map<string, number>()
const _requestTails = new Map<string, Promise<void>>()
const _serverIds = new Map<string, string>()
const _connectionKeys = new WeakMap<EventSource, string>()
const operationKey = (modelId: string, client: ReturnType<typeof createApiClient>) => `${client.baseUrl}|${modelId}`
function advance(key: string) {
  const generation = (_generations.get(key) ?? 0) + 1
  _generations.set(key, generation)
  return generation
}
function serialize<T>(key: string, work: () => Promise<T>): Promise<T> {
  const request = (_requestTails.get(key) ?? Promise.resolve()).then(work)
  const tail = request.then(() => {}, () => {})
  _requestTails.set(key, tail)
  void tail.then(() => { if (_requestTails.get(key) === tail) _requestTails.delete(key) })
  return request
}

// ── 辅助函数 ──────────────────────────────────────────────────────────────────

function normalizeTask(data: Record<string, unknown>): Partial<DownloadTask> {
  return {
    modelId:    (data.modelId   ?? data.model_id)   as string,
    modelName:  (data.modelName ?? data.model_name) as string,
    downloadId: (data.downloadId ?? data.download_id) as string | undefined,
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

// 仅对提示去重，重放的终态仍更新 store；缓存限定当前应用最近 500 次下载。
const _terminalNotices = new Set<string>()
const isTerminal = (status: DownloadTask['status'] | undefined) =>
  status === DownloadStatus.DONE || status === DownloadStatus.ERROR || status === DownloadStatus.CANCELLED
const isDownloadStatus = (status: unknown) => typeof status === 'string'
  && [DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING, DownloadStatus.DONE,
    DownloadStatus.ERROR, DownloadStatus.CANCELLED].some((value) => value === status)

function applySnapshot(modelId: string, patch: Partial<DownloadTask>, client: ReturnType<typeof createApiClient>, isCurrent: () => boolean) {
  if (!isCurrent() || !isDownloadStatus(patch.status)) return false
  useDownloadStore.getState().setTask(modelId, { ...patch, statusSyncFailed: false })
  const key = operationKey(modelId, client)
  _sseRetryCount.delete(key)
  if (!isTerminal(patch.status)) return false
  const noticeKey = `${key}|${patch.downloadId ?? `legacy-${_generations.get(key) ?? 0}`}`
  if (!_terminalNotices.has(noticeKey)) {
    _terminalNotices.add(noticeKey)
    if (_terminalNotices.size > 500) _terminalNotices.delete(_terminalNotices.values().next().value!)
    if (patch.status === DownloadStatus.DONE) showToast('success', `${patch.modelName || modelId} 下载完成`)
    if (patch.status === DownloadStatus.ERROR) showToast('error', `${patch.modelName || modelId} 下载失败: ${patch.message}`)
  }
  if (patch.status === DownloadStatus.DONE) void _syncOne(modelId, client, isCurrent)
  return true
}

/** 建立或复用 SSE 连接（全局单例，不重复建立）。 */
function ensureSubscribed(modelId: string, client: ReturnType<typeof createApiClient>) {
  const key = operationKey(modelId, client)
  const generation = _generations.get(key) ?? 0
  let downloadId = useDownloadStore.getState().getTask(modelId)?.downloadId
  const connectionKey = `${key}|${generation}|${downloadId ?? ''}`
  const sameAttempt = () => (_generations.get(key) ?? 0) === generation
    && createApiClient(useBackendStore.getState().backendURL).baseUrl === client.baseUrl
    && useDownloadStore.getState().getTask(modelId)?.downloadId === downloadId
  const existing = _sseMap.get(modelId)
  if (existing && existing.readyState !== EventSource.CLOSED && _connectionKeys.get(existing) === connectionKey) {
    console.log(`[SSE] 已有连接复用: ${modelId} readyState=${existing.readyState}`)
    return
  }

  existing?.close()
  const esUrl = client.sseUrl(`${ApiPath.MODELS_SUBSCRIBE}/${encodeURIComponent(modelId)}`)
  console.log(`[SSE] 建立连接: ${esUrl}`)
  const es = new EventSource(esUrl)
  _sseMap.set(modelId, es)
  _connectionKeys.set(es, connectionKey)
  const isCurrent = () => _sseMap.get(modelId) === es && sameAttempt()

  es.onopen = () => {
    if (!isCurrent()) return
    console.log(`[SSE] 连接已打开: ${modelId}`)
    // 只有有效状态快照才重置重试，TCP 打开不代表状态已同步。
  }

  es.addEventListener('status', (e: MessageEvent) => {
    if (!isCurrent()) return
    try {
      const data = JSON.parse(e.data) as Record<string, unknown>
      const patch = normalizeTask(data)
      if (downloadId && patch.downloadId !== downloadId) return
      console.log(`[SSE] 收到 status 事件: ${modelId} status=${patch.status}`, data)

      if (!isDownloadStatus(patch.status) || (patch.modelId && patch.modelId !== modelId)) return
      const terminal = applySnapshot(modelId, patch, client, sameAttempt)
      if (!downloadId && patch.downloadId) {
        downloadId = patch.downloadId
        _serverIds.set(key, downloadId)
        _connectionKeys.set(es, `${key}|${generation}|${downloadId}`)
      }
      if (terminal) {
        es.close()
        _sseMap.delete(modelId)
      }
    } catch (err) {
      console.error('[SSE] 解析事件失败:', err)
    }
  })

  es.onerror = (e) => {
    if (!isCurrent()) return
    console.error(`[SSE] 连接错误: ${modelId}`, e)
    es.close()
    _sseMap.delete(modelId)

    const current = useDownloadStore.getState().getTask(modelId)
    // 对于 queued/downloading 状态的任务，尝试重连而不是立即标记为 error
    if (current?.status === DownloadStatus.DOWNLOADING || current?.status === DownloadStatus.QUEUED) {
      const retries = _sseRetryCount.get(key) ?? 0
      if (retries < SSE_MAX_RETRIES) {
        _sseRetryCount.set(key, retries + 1)
        console.log(`[SSE] 将在 ${SSE_RETRY_DELAY}ms 后重试 (${retries + 1}/${SSE_MAX_RETRIES}): ${modelId}`)
        setTimeout(() => {
          // 重连前再次检查状态，如果已经不再是 queued/downloading 就不重连
          if (!sameAttempt()) return
          const latest = useDownloadStore.getState().getTask(modelId)
          if (latest?.status === DownloadStatus.QUEUED || latest?.status === DownloadStatus.DOWNLOADING) {
            ensureSubscribed(modelId, client)
          }
        }, SSE_RETRY_DELAY)
      } else {
        // 表示状态同步失败，不推定后端下载失败，也不弹下载失败通知。
        console.log(`[SSE] 重试次数耗尽，无法确认状态: ${modelId}`)
        _sseRetryCount.delete(key)
        useDownloadStore.getState().setTask(modelId, { status: DownloadStatus.ERROR, statusSyncFailed: true,
          message: '无法确认下载状态，请重新连接；不代表下载失败' })
      }
    }
  }
}

/** 下载完成后立即重查单个模型状态，把 store 里的 done → ok。 */
async function _syncOne(modelId: string, client: ReturnType<typeof createApiClient>, isCurrent: () => boolean) {
  try {
    const res = await client.fetch(`${ApiPath.MODELS_HEALTH}/${encodeURIComponent(modelId)}`)
    const data = await readApiResponse<{ status: string; name?: string }>(res, '读取模型状态失败')
    if (!isCurrent()) return
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
      const data = await readApiResponse<{
        models?: Array<{ id: string; name: string; status: string }>
      }>(res, '读取模型状态失败')
      // 用 getState() 避免订阅 store 导致 re-render
      const { getTask, setTask } = useDownloadStore.getState()
      for (const m of data.models ?? []) {
        const current = getTask(m.id)
        if (
          current?.statusSyncFailed ||
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
    const client = getClient()
    const key = operationKey(modelId, client)
    const current = getTask(modelId)
    if (current?.status === DownloadStatus.ERROR && current.statusSyncFailed && current.downloadId) {
      _sseRetryCount.delete(key)
      setTask(modelId, { status: DownloadStatus.DOWNLOADING, statusSyncFailed: false, message: '正在重新连接下载状态...' })
      ensureSubscribed(modelId, client)
      return
    }
    if (current?.status === DownloadStatus.QUEUED || current?.status === DownloadStatus.DOWNLOADING) {
      // POST 尚未返回时不能订阅同模型的旧实例。
      if (current.downloadId || !_requestTails.has(key)) ensureSubscribed(modelId, client)
      return
    }
    const generation = advance(key)
    _sseRetryCount.delete(key)
    const isCurrent = () => _generations.get(key) === generation
      && createApiClient(useBackendStore.getState().backendURL).baseUrl === client.baseUrl
    _sseMap.get(modelId)?.close()
    _sseMap.delete(modelId)
    const queuedCount = Object.values(useDownloadStore.getState().tasks).filter(
      (task) => task.status === DownloadStatus.QUEUED || task.status === DownloadStatus.DOWNLOADING
    ).length
    setTask(modelId, { downloadId: undefined, statusSyncFailed: false, status: DownloadStatus.QUEUED, position: queuedCount + 1,
      message: '提交中...', speed: '', downloaded: '', totalSize: '' })
    try {
      await serialize(key, async () => {
        const res = await client.fetch(`${ApiPath.MODELS_DOWNLOAD}/${encodeURIComponent(modelId)}`, { method: 'POST' })
        const patch = normalizeTask(await readApiResponse<Record<string, unknown>>(res, '提交下载失败'))
        // 即使 UI 已取消也记住实例 ID，使后续 DELETE 只针对本次下载。
        if (patch.downloadId) _serverIds.set(key, patch.downloadId)
        if (!isCurrent()) return
        if (!applySnapshot(modelId, patch, client, isCurrent)) ensureSubscribed(modelId, client)
      })
    } catch (error: unknown) {
      if (!isCurrent()) return
      const message = error instanceof Error ? error.message : String(error)
      setTask(modelId, { status: DownloadStatus.ERROR, message })
      showToast('error', `下载失败: ${message}`)
    }
  }, [getClient])

  const cancelDownload = useCallback(async (modelId: string) => {
    const client = getClient()
    const key = operationKey(modelId, client)
    advance(key)
    const currentId = useDownloadStore.getState().getTask(modelId)?.downloadId
    _sseMap.get(modelId)?.close()
    _sseMap.delete(modelId)
    _sseRetryCount.delete(key)
    useDownloadStore.getState().setTask(modelId, { status: DownloadStatus.CANCELLED, statusSyncFailed: false,
      message: '正在取消，等待下载线程退出', speed: '', downloaded: '' })
    try {
      await serialize(key, async () => {
        const downloadId = _serverIds.get(key) ?? currentId
        const query = downloadId ? `?download_id=${encodeURIComponent(downloadId)}` : ''
        await ensureApiSuccess(await client.fetch(
          `${ApiPath.MODELS_DOWNLOAD}/${encodeURIComponent(modelId)}${query}`, { method: 'DELETE' }
        ), '取消下载失败')
      })
    } catch {
      // 网络失败时保留用户取消意图；后续重试由后端任务去重和资源占用保护。
    }
  }, [getClient])

  // ── 一键下载 ────────────────────────────────────────────────────────────────

  const startBulkDownload = useCallback(async () => {
    const client = getClient()
    const generations = new Map(_generations)
    try {
      const res = await client.fetch(`${ApiPath.MODELS_DOWNLOAD}/bulk`, { method: 'POST' })
      const data = await readApiResponse<{ submitted: number; tasks: Record<string, unknown>[] }>(res, '一键下载提交失败')
      if (createApiClient(useBackendStore.getState().backendURL).baseUrl !== client.baseUrl) return

      if (data.submitted === 0) {
        showToast('success', '所有模型均已下载，无需重复下载')
        return
      }

      const { setBulkSession } = useDownloadStore.getState()
      const modelIds: string[] = []
      for (const taskData of data.tasks) {
        const task = normalizeTask(taskData)
        if (task.modelId) {
          const key = operationKey(task.modelId, client)
          if ((_generations.get(key) ?? 0) !== (generations.get(key) ?? 0)) continue
          if (task.downloadId) _serverIds.set(key, task.downloadId)
          if (useDownloadStore.getState().getTask(task.modelId)?.downloadId !== task.downloadId) advance(key)
          const generation = _generations.get(key) ?? 0
          const isCurrent = () => (_generations.get(key) ?? 0) === generation
            && createApiClient(useBackendStore.getState().backendURL).baseUrl === client.baseUrl
          modelIds.push(task.modelId)
          if (!applySnapshot(task.modelId, task, client, isCurrent)) ensureSubscribed(task.modelId, client)
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
      if (task?.statusSyncFailed || task?.status === DownloadStatus.QUEUED || task?.status === DownloadStatus.DOWNLOADING) {
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
      if (task.statusSyncFailed) {
        await startDownload(modelId)
        continue
      }
      if (task.status === DownloadStatus.QUEUED || task.status === DownloadStatus.DOWNLOADING) {
        ensureSubscribed(modelId, client)
      }
    }
  }, [getClient, startDownload])

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
