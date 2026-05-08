import { useState, useEffect, useCallback, useRef, type MutableRefObject } from 'react'
import { CheckCircle2, XCircle, AlertCircle, Loader2, Trash2, RefreshCw, Package, Download, ChevronDown, ChevronUp } from 'lucide-react'
import { clsx } from 'clsx'
import { showToast } from '../components/ui'
import { useBackendStore } from '../stores/useBackendStore'
import { useSettingsStore } from '../stores/useSettingsStore'
import type { RowDownloadState } from '../stores/useSettingsStore'

// ── 类型 ──────────────────────────────────────────────────────────────────────

interface ModelEntry {
  id: string
  name: string
  provider: string
  tags: string[]
  description?: string
  badge?: string
  size_mb?: number
  hf_model_id?: string
  download_url?: string
  display_group?: string
  iopaint_mode?: string
  // 文件状态（来自 /api/models/list）
  status: 'ok' | 'missing' | 'partial' | 'corrupted' | 'unknown'
  message: string
}

interface ModeGroup {
  id: string
  name: string
}

// ── 辅助组件 ──────────────────────────────────────────────────────────────────

const STATUS_ICON = {
  ok:        <CheckCircle2 size={13} className="text-status-success flex-shrink-0" />,
  missing:   <XCircle size={13} className="text-status-error flex-shrink-0" />,
  partial:   <AlertCircle size={13} className="text-yellow-400 flex-shrink-0" />,
  corrupted: <AlertCircle size={13} className="text-orange-400 flex-shrink-0" />,
  unknown:   <AlertCircle size={13} className="text-fg-secondary flex-shrink-0" />,
}

const STATUS_LABEL: Record<string, string> = {
  ok:        '已下载',
  missing:   '未下载',
  partial:   '不完整',
  corrupted: '已损坏',
  unknown:   '未知',
}

const BADGE_COLOR: Record<string, string> = {
  推荐:   'bg-blue-500/20 text-blue-400',
  快速:   'bg-green-500/20 text-green-400',
  高质量: 'bg-purple-500/20 text-purple-400',
  动漫:   'bg-pink-500/20 text-pink-400',
}

// ── 模块级 SSE 单例（生命周期独立于组件，切换页签不中断）────────────────────
// 一键下载连接
let globalEsRef: EventSource | null = null
// 单模型下载连接（key = model id）
const globalRowEsRef: Record<string, EventSource> = {}

// 存储具名事件处理函数引用，确保 removeEventListener 能精准移除旧监听
type AllHandlers = { start?: (e: Event) => void; model?: (e: Event) => void; finish?: (e: Event) => void }
let globalEsHandlers: AllHandlers = {}

type RowHandlers = { model?: (e: Event) => void; finish?: (e: Event) => void }
const globalRowEsHandlers: Record<string, RowHandlers> = {}

/** 为一键下载 EventSource 绑定事件监听（可重复调用以接管已有连接） */
function bindDownloadAllListeners(
  es: EventSource,
  loadModelsRef: MutableRefObject<() => void>,
) {
  // 移除旧的具名监听（防止重复注册堆积）
  if (globalEsHandlers.start)  es.removeEventListener('start',  globalEsHandlers.start)
  if (globalEsHandlers.model)  es.removeEventListener('model',  globalEsHandlers.model)
  if (globalEsHandlers.finish) es.removeEventListener('finish', globalEsHandlers.finish)
  es.onerror = null

  globalEsHandlers.start = (e) => {
    const data = JSON.parse((e as MessageEvent).data)
    useSettingsStore.getState().setDownloadTotal(data.total)
    useSettingsStore.getState().setDownloadSummary(data.message)
  }
  globalEsHandlers.model = (e) => {
    const item = JSON.parse((e as MessageEvent).data)
    useSettingsStore.getState().setDownloadModels((prev) => {
      const idx = prev.findIndex((m) => m.id === item.id)
      if (idx >= 0) { const next = [...prev]; next[idx] = item; return next }
      return [...prev, item]
    })
  }
  globalEsHandlers.finish = (e) => {
    const data = JSON.parse((e as MessageEvent).data)
    useSettingsStore.getState().setDownloadSummary(data.message)
    useSettingsStore.getState().setDownloadStatus(data.failed > 0 ? 'error' : 'done')
    es.close(); globalEsRef = null; globalEsHandlers = {}
    loadModelsRef.current()
  }

  es.addEventListener('start',  globalEsHandlers.start)
  es.addEventListener('model',  globalEsHandlers.model)
  es.addEventListener('finish', globalEsHandlers.finish)
  es.onerror = () => {
    useSettingsStore.getState().setDownloadModels((prev) =>
      prev.map((item) =>
        item.status === 'downloading'
          ? { ...item, status: 'error', message: '连接中断', speed: '', downloaded: '', total_size: '' }
          : item
      )
    )
    useSettingsStore.getState().setDownloadSummary('连接中断，请检查后端是否在运行')
    useSettingsStore.getState().setDownloadStatus('error')
    es.close(); globalEsRef = null; globalEsHandlers = {}
  }
}

/** 为单模型 EventSource 绑定事件监听（可重复调用以接管已有连接） */
function bindRowListeners(
  es: EventSource,
  mid: string,
  loadModelsRef: MutableRefObject<() => void>,
) {
  // 移除旧的具名监听（防止重复注册堆积）
  const prev = globalRowEsHandlers[mid]
  if (prev?.model)  es.removeEventListener('model',  prev.model)
  if (prev?.finish) es.removeEventListener('finish', prev.finish)
  es.onerror = null

  const handlers: RowHandlers = {}
  globalRowEsHandlers[mid] = handlers

  handlers.model = (e) => {
    const item = JSON.parse((e as MessageEvent).data)
    useSettingsStore.getState().setRowDownload(mid, {
      status: item.status === 'error' ? 'error' : item.status === 'done' || item.status === 'skipped' ? 'done' : 'downloading',
      message: item.message ?? '',
      speed: item.speed ?? '',
      downloaded: item.downloaded ?? '',
      total_size: item.total_size ?? '',
    })
  }
  handlers.finish = (e) => {
    const data = JSON.parse((e as MessageEvent).data)
    const finalStatus = data.failed > 0 ? 'error' : 'done'
    useSettingsStore.getState().setRowDownload(mid, {
      status: finalStatus, message: data.message, speed: '', downloaded: '', total_size: '',
    })
    es.close(); delete globalRowEsRef[mid]; delete globalRowEsHandlers[mid]
    if (finalStatus === 'done') {
      showToast('success', data.message)
      setTimeout(() => loadModelsRef.current(), 500)
    } else {
      showToast('error', data.message)
    }
  }

  es.addEventListener('model',  handlers.model)
  es.addEventListener('finish', handlers.finish)
  es.onerror = () => {
    useSettingsStore.getState().setRowDownload(mid, {
      status: 'error', message: '连接中断', speed: '', downloaded: '', total_size: '',
    })
    es.close(); delete globalRowEsRef[mid]; delete globalRowEsHandlers[mid]
  }
}

// ── 主组件 ────────────────────────────────────────────────────────────────────

export default function ModelManager() {
  const backendURL = useBackendStore((s) => s.backendURL)
  const store = useSettingsStore()

  const [models, setModels] = useState<ModelEntry[]>([])
  const [modeGroups, setModeGroups] = useState<ModeGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedGroup, setSelectedGroup] = useState<string>('__all__')
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)

  // 下载相关（从全局 store 读取，跨页签保持）
  const downloadStatus = store.downloadStatus
  const downloadModels = store.downloadModels
  const downloadSummary = store.downloadSummary
  const downloadTotal = store.downloadTotal
  const downloadPanelOpen = store.downloadPanelOpen
  // 单模型行内下载状态（从全局 store 读取，跨页签保持）
  const rowDownloads = store.rowDownloads
  const downloadListRef = useRef<HTMLDivElement>(null)
  // loadModels 引用转发（让模块级 SSE 回调始终调用最新的 loadModels）
  const loadModelsRef = useRef<() => void>(() => {})

  // ── 加载模型列表 ──────────────────────────────────────────────────────────

  const loadModels = useCallback(async () => {
    setLoading(true)
    try {
      const url = window.electronAPI?.getBackendURL
        ? await window.electronAPI.getBackendURL()
        : backendURL
      const res = await fetch(`${url}/api/models/list`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setModels(data.models ?? [])
      setModeGroups(data.mode_groups ?? [])
    } catch (e: any) {
      showToast('error', `加载模型列表失败: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }, [backendURL])

  // 始终同步最新的 loadModels 到 ref，供模块级 SSE 回调使用
  useEffect(() => { loadModelsRef.current = loadModels }, [loadModels])

  useEffect(() => { loadModels() }, [loadModels])

  // ── 组件挂载时：接管后台正在运行的 SSE 连接 & 恢复孤立的行内下载状态 ───────

  useEffect(() => {
    const resume = async () => {
      const url = window.electronAPI?.getBackendURL
        ? await window.electronAPI.getBackendURL()
        : backendURL

      // 1. 一键下载：连接仍 OPEN，重新绑定事件监听
      if (globalEsRef && globalEsRef.readyState !== EventSource.CLOSED) {
        bindDownloadAllListeners(globalEsRef, loadModelsRef)
      }

      // 2. 单模型：重新绑定已有 OPEN 连接的监听器
      for (const [mid, es] of Object.entries(globalRowEsRef)) {
        if (es.readyState !== EventSource.CLOSED) {
          bindRowListeners(es, mid, loadModelsRef)
        } else {
          delete globalRowEsRef[mid]
        }
      }

      // 3. 行内 downloading 状态但已无对应连接 → 查询实际状态
      const orphaned = Object.entries(useSettingsStore.getState().rowDownloads)
        .filter(([mid, v]) => v.status === 'downloading' && !globalRowEsRef[mid])
      for (const [mid] of orphaned) {
        try {
          const res = await fetch(`${url}/api/models/health/${encodeURIComponent(mid)}`)
          if (!res.ok) throw new Error()
          const data = await res.json()
          if (data.status === 'ok') {
            useSettingsStore.getState().setRowDownload(mid, {
              status: 'done', message: '下载完成', speed: '', downloaded: '', total_size: '',
            })
          } else {
            useSettingsStore.getState().setRowDownload(mid, {
              status: 'error', message: '下载未完成，请重试', speed: '', downloaded: '', total_size: '',
            })
          }
        } catch {
          useSettingsStore.getState().setRowDownload(mid, {
            status: 'error', message: '状态检查失败，请重试', speed: '', downloaded: '', total_size: '',
          })
        }
      }
    }
    resume()
  }, [backendURL])

  // ── 单模型下载逻辑 ────────────────────────────────────────────────────────

  const getBackendUrl = async () =>
    window.electronAPI?.getBackendURL
      ? await window.electronAPI.getBackendURL()
      : backendURL

  /** 建立单模型 SSE 连接（新下载 or 切换页签后重连） */
  const connectRowSSE = useCallback(async (mid: string) => {
    // 已有 OPEN 连接则不重复建立
    if (globalRowEsRef[mid] && globalRowEsRef[mid].readyState !== EventSource.CLOSED) return

    const url = await getBackendUrl()
    const es = new EventSource(`${url}/api/models/download/${encodeURIComponent(mid)}`)
    globalRowEsRef[mid] = es
    bindRowListeners(es, mid, loadModelsRef)
  }, [backendURL])

  const handleDownloadSingle = async (model: ModelEntry) => {
    const mid = model.id
    // 已在下载中则忽略
    if (rowDownloads[mid]?.status === 'downloading') return
    // 关闭旧连接
    if (globalRowEsRef[mid]) { globalRowEsRef[mid].close(); delete globalRowEsRef[mid] }

    store.setRowDownload(mid, { status: 'downloading', message: '准备下载...', speed: '', downloaded: '', total_size: '' })
    connectRowSSE(mid)
  }

  // ── 下载逻辑 ─────────────────────────────────────────────────────────────

  const handleDownloadAll = async () => {
    if (downloadStatus === 'running') return
    if (globalEsRef) { globalEsRef.close(); globalEsRef = null }

    store.setDownloadStatus('running')
    store.setDownloadModels([])
    store.setDownloadSummary('')
    store.setDownloadPanelOpen(true)

    const url = await getBackendUrl()
    const es = new EventSource(`${url}/api/models/download`)
    globalEsRef = es
    bindDownloadAllListeners(es, loadModelsRef)
  }

  // ── 取消一键下载 ────────────────────────────────────────────────────────────

  const handleCancelDownloadAll = useCallback(() => {
    if (globalEsRef) {
      globalEsRef.close()
      globalEsRef = null
    }
    useSettingsStore.getState().setDownloadModels((prev) =>
      prev.map((item) =>
        item.status === 'downloading'
          ? { ...item, status: 'error', message: '已取消', speed: '', downloaded: '', total_size: '' }
          : item
      )
    )
    store.setDownloadStatus('error')
    store.setDownloadSummary('下载已取消')
  }, [store])

  // ── 取消单模型下载 ────────────────────────────────────────────────────────

  const handleCancelSingle = useCallback((mid: string) => {
    if (globalRowEsRef[mid]) {
      globalRowEsRef[mid].close()
      delete globalRowEsRef[mid]
    }
    useSettingsStore.getState().setRowDownload(mid, {
      status: 'error', message: '已取消', speed: '', downloaded: '', total_size: '',
    })
  }, [])

  // ── 分组逻辑 ─────────────────────────────────────────────────────────────

  // 所有 mode_group id 的集合
  const allGroupIds = new Set(modeGroups.map((g) => g.id))

  // 按分组过滤模型
  const filteredModels = (() => {
    if (selectedGroup === '__all__') return models
    if (selectedGroup === '__ungrouped__') {
      return models.filter((m) => !m.tags?.some((t) => allGroupIds.has(t)))
    }
    return models.filter((m) => m.tags?.includes(selectedGroup))
  })()

  // 未分组模型数量
  const ungroupedCount = models.filter(
    (m) => !m.tags?.some((t) => allGroupIds.has(t))
  ).length

  // 每个分组的统计（ok 数 / 总数）
  const groupStats = (groupId: string) => {
    const list = groupId === '__all__'
      ? models
      : groupId === '__ungrouped__'
        ? models.filter((m) => !m.tags?.some((t) => allGroupIds.has(t)))
        : models.filter((m) => m.tags?.includes(groupId))
    const ok = list.filter((m) => m.status === 'ok').length
    return { total: list.length, ok }
  }

  // ── 删除文件 ─────────────────────────────────────────────────────────────

  const handleDelete = async (model: ModelEntry) => {
    if (confirmDeleteId !== model.id) {
      // 第一次点击：显示确认
      setConfirmDeleteId(model.id)
      return
    }
    // 第二次点击：执行删除
    setConfirmDeleteId(null)
    setDeletingId(model.id)
    try {
      const url = window.electronAPI?.getBackendURL
        ? await window.electronAPI.getBackendURL()
        : backendURL
      const res = await fetch(`${url}/api/models/${model.id}/files`, { method: 'DELETE' })
      const data = await res.json()
      if (data.ok) {
        showToast('success', data.message)
        // 刷新状态
        loadModels()
      } else {
        showToast('error', data.message || '删除失败')
      }
    } catch (e: any) {
      showToast('error', `删除失败: ${e.message}`)
    } finally {
      setDeletingId(null)
    }
  }

  // 点击其他地方取消确认
  const cancelConfirm = () => setConfirmDeleteId(null)

  // ── 渲染 ─────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-1 min-h-0 overflow-hidden" onClick={cancelConfirm}>
      {/* ── 左侧分组导航 ─────────────────────────────────────────────────── */}
      <div className="w-36 flex-shrink-0 border-r border-border-subtle overflow-y-auto py-2">
        {/* 全部 */}
        <GroupItem
          label="全部"
          stats={groupStats('__all__')}
          active={selectedGroup === '__all__'}
          onClick={() => setSelectedGroup('__all__')}
        />

        {/* 各功能分组 */}
        {modeGroups.map((g) => {
          const stats = groupStats(g.id)
          if (stats.total === 0) return null
          return (
            <GroupItem
              key={g.id}
              label={g.name}
              stats={stats}
              active={selectedGroup === g.id}
              onClick={() => setSelectedGroup(g.id)}
            />
          )
        })}

        {/* 未分组 */}
        {ungroupedCount > 0 && (
          <GroupItem
            label="未分组"
            stats={groupStats('__ungrouped__')}
            active={selectedGroup === '__ungrouped__'}
            onClick={() => setSelectedGroup('__ungrouped__')}
          />
        )}
      </div>

      {/* ── 右侧模型列表 ──────────────────────────────────────────────────── */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {/* 列表头部工具栏 */}
        <div className="flex items-center justify-between px-4 py-2 border-b border-border-subtle flex-shrink-0 gap-2">
          <span className="text-xs text-fg-secondary flex-shrink-0">
            {filteredModels.length} 个模型
            {filteredModels.length > 0 && (
              <span className="ml-2">
                · 已下载 {filteredModels.filter((m) => m.status === 'ok').length}
                · 缺失 {filteredModels.filter((m) => m.status === 'missing').length}
              </span>
            )}
          </span>

          {/* 右侧按钮组 */}
          <div className="flex items-center gap-2 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
            {/* 下载进度状态指示 */}
            {downloadStatus === 'done' && (
              <span className="text-[11px] text-status-success flex items-center gap-1">
                <CheckCircle2 size={11} /> 下载完成
              </span>
            )}
            {downloadStatus === 'error' && (
              <span className="text-[11px] text-status-error flex items-center gap-1">
                <XCircle size={11} /> 部分失败
              </span>
            )}

            {/* 展开/收起下载面板 */}
            {downloadStatus !== 'idle' && (
              <button
                onClick={() => store.setDownloadPanelOpen(!downloadPanelOpen)}
                className="flex items-center gap-1 text-[11px] text-fg-secondary hover:text-fg-primary transition-colors"
              >
                {downloadPanelOpen ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                {downloadStatus === 'running' ? '下载中' : '进度'}
              </button>
            )}

            {/* 一键下载 / 取消 按钮 */}
            {downloadStatus === 'running' ? (
              <button
                onClick={handleCancelDownloadAll}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded transition-colors bg-status-error/20 text-status-error hover:bg-status-error/30"
              >
                <XCircle size={12} />
                取消下载
              </button>
            ) : (
              <button
                onClick={handleDownloadAll}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded transition-colors bg-border-focus text-white hover:bg-blue-600"
              >
                <Download size={12} />
                一键下载全部
              </button>
            )}

            <button
              onClick={loadModels}
              disabled={loading}
              className="flex items-center gap-1 text-xs text-fg-secondary hover:text-fg-primary transition-colors disabled:opacity-50"
            >
              <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
              刷新
            </button>
          </div>
        </div>

        {/* 下载进度面板（可折叠） */}
        {downloadStatus !== 'idle' && downloadPanelOpen && (
          <div className="border-b border-border-subtle bg-bg-tertiary flex-shrink-0" onClick={(e) => e.stopPropagation()}>
            {/* 汇总 */}
            {downloadSummary && (
              <div className="px-4 py-1.5 border-b border-border-subtle">
                <p className="text-[11px] text-fg-secondary">{downloadSummary}</p>
              </div>
            )}
            {/* 模型条目列表 */}
            <div ref={downloadListRef} className="max-h-40 overflow-y-auto divide-y divide-border-subtle">
              {downloadModels.length === 0 && (
                <div className="px-4 py-2 text-[11px] text-fg-secondary">检测中...</div>
              )}
              {downloadModels.map((item) => (
                <div key={item.id} className="px-4 py-1.5">
                  <div className="flex items-center gap-2">
                    <span className="flex-shrink-0">
                      {item.status === 'downloading' && <Loader2 size={11} className="animate-spin text-fg-accent" />}
                      {item.status === 'done'        && <CheckCircle2 size={11} className="text-status-success" />}
                      {item.status === 'skipped'     && <CheckCircle2 size={11} className="text-fg-secondary" />}
                      {item.status === 'error'       && <XCircle size={11} className="text-status-error" />}
                      {item.status === 'checking'    && <Loader2 size={11} className="animate-spin text-fg-secondary" />}
                    </span>
                    <span className={clsx('flex-1 text-[11px] truncate', item.status === 'error' ? 'text-status-error' : 'text-fg-primary')}>
                      {item.name}
                    </span>
                    <div className="flex items-center gap-2 flex-shrink-0">
                      {item.status === 'downloading' && item.speed && (
                        <span className="text-[10px] text-fg-accent font-mono min-w-[70px] text-right">{item.speed}</span>
                      )}
                      {item.status === 'downloading' && item.downloaded && (
                        <span className="text-[10px] text-fg-secondary font-mono">
                          {item.downloaded}{item.total_size ? ` / ${item.total_size}` : ''}
                        </span>
                      )}
                      {item.status !== 'downloading' && (
                        <span className="text-[10px] text-fg-secondary max-w-[160px] truncate">{item.message}</span>
                      )}
                      {item.status === 'downloading' && !item.speed && (
                        <span className="text-[10px] text-fg-secondary max-w-[140px] truncate">{item.message}</span>
                      )}
                    </div>
                  </div>
                  {item.status === 'downloading' && item.downloaded && item.total_size && item.total_size !== '?' && (() => {
                    const p = (s: string) => { const n = parseFloat(s); return s.includes('GB') ? n*1024 : s.includes('MB') ? n : s.includes('KB') ? n/1024 : n/(1024*1024) }
                    const pct = Math.min(100, Math.round(p(item.downloaded) / p(item.total_size) * 100))
                    return <div className="mt-1 h-0.5 bg-bg-hover rounded-full overflow-hidden"><div className="h-full bg-fg-accent rounded-full transition-all duration-500" style={{ width: `${pct}%` }} /></div>
                  })()}
                </div>
              ))}
            </div>
            {/* 总进度条 */}
            {downloadTotal > 0 && downloadModels.length > 0 && (
              <div className="px-4 py-2 border-t border-border-subtle">
                <div className="flex justify-between text-[10px] text-fg-secondary mb-1">
                  <span>{downloadModels.length} / {downloadTotal}</span>
                  <span>{Math.round((downloadModels.length / downloadTotal) * 100)}%</span>
                </div>
                <div className="h-1 bg-bg-hover rounded-full overflow-hidden">
                  <div className="h-full bg-border-focus rounded-full transition-all duration-300" style={{ width: `${(downloadModels.length / downloadTotal) * 100}%` }} />
                </div>
              </div>
            )}
          </div>
        )}

        {/* 模型列表 */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center h-32 text-fg-secondary text-xs gap-2">
              <Loader2 size={14} className="animate-spin" />
              加载中...
            </div>
          ) : filteredModels.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-32 text-fg-secondary text-xs gap-2">
              <Package size={24} className="opacity-30" />
              该分组下暂无模型
            </div>
          ) : (
            <div className="divide-y divide-border-subtle">
              {filteredModels.map((model) => (
                <ModelRow
                  key={model.id}
                  model={model}
                  isDeleting={deletingId === model.id}
                  isConfirming={confirmDeleteId === model.id}
                  rowDownload={rowDownloads[model.id] ?? defaultRowState}
                  onDownload={(e) => { e.stopPropagation(); handleDownloadSingle(model) }}
                  onCancel={(e) => { e.stopPropagation(); handleCancelSingle(model.id) }}
                  onDelete={(e) => { e.stopPropagation(); handleDelete(model) }}
                  onCancelConfirm={(e) => { e.stopPropagation(); setConfirmDeleteId(null) }}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── 默认行下载状态 ────────────────────────────────────────────────────────────

const defaultRowState: RowDownloadState = {
  status: 'idle', message: '', speed: '', downloaded: '', total_size: '',
}

// ── 分组项 ─────────────────────────────────────────────────────────────────────

function GroupItem({
  label, stats, active, onClick,
}: {
  label: string
  stats: { total: number; ok: number }
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        'w-full text-left px-3 py-2 text-xs transition-colors',
        active
          ? 'bg-bg-active text-fg-accent'
          : 'text-fg-secondary hover:bg-bg-hover hover:text-fg-primary'
      )}
    >
      <div className="font-medium truncate">{label}</div>
      <div className={clsx('text-[10px] mt-0.5', active ? 'text-fg-accent/70' : 'text-fg-secondary/60')}>
        {stats.ok}/{stats.total} 已下载
      </div>
    </button>
  )
}

// ── 模型行 ────────────────────────────────────────────────────────────────────

function ModelRow({
  model, isDeleting, isConfirming, rowDownload, onDownload, onCancel, onDelete, onCancelConfirm,
}: {
  model: ModelEntry
  isDeleting: boolean
  isConfirming: boolean
  rowDownload: RowDownloadState
  onDownload: (e: React.MouseEvent) => void
  onCancel: (e: React.MouseEvent) => void
  onDelete: (e: React.MouseEvent) => void
  onCancelConfirm: (e: React.MouseEvent) => void
}) {
  // IOPaint 内置模型（cli 模式）无文件可删 / 下载
  const isBuiltin = model.provider === 'IOPaint' && model.iopaint_mode === 'cli'
  const canDelete = !isBuiltin && model.status === 'ok'
  // 非内置 & 非已下载 & 下载状态为 idle → 可单独下载
  const canDownload = !isBuiltin && model.status !== 'ok' && rowDownload.status === 'idle'
  const isRowDownloading = rowDownload.status === 'downloading'

  // 计算行内进度百分比
  const pct = (() => {
    if (!rowDownload.downloaded || !rowDownload.total_size || rowDownload.total_size === '?') return null
    const parseMB = (s: string) => {
      const n = parseFloat(s)
      if (s.includes('GB')) return n * 1024
      if (s.includes('MB')) return n
      if (s.includes('KB')) return n / 1024
      return n / (1024 * 1024)
    }
    const p = Math.min(100, Math.round(parseMB(rowDownload.downloaded) / parseMB(rowDownload.total_size) * 100))
    return isNaN(p) ? null : p
  })()

  return (
    <div className="flex items-start gap-3 px-4 py-3 hover:bg-bg-hover/30 transition-colors">
      {/* 状态图标 */}
      <div className="mt-0.5 flex-shrink-0">
        {isRowDownloading
          ? <Loader2 size={13} className="animate-spin text-fg-accent" />
          : rowDownload.status === 'done'
          ? <CheckCircle2 size={13} className="text-status-success" />
          : rowDownload.status === 'error'
          ? <XCircle size={13} className="text-status-error" />
          : (STATUS_ICON[model.status] ?? STATUS_ICON.unknown)
        }
      </div>

      {/* 主信息 */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-medium text-fg-primary truncate">{model.name}</span>
          {model.badge && (
            <span className={clsx('text-[10px] px-1.5 py-0.5 rounded font-medium', BADGE_COLOR[model.badge] ?? 'bg-bg-hover text-fg-secondary')}>
              {model.badge}
            </span>
          )}
          {isBuiltin && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-bg-hover text-fg-secondary">内置</span>
          )}
        </div>

        {/* 描述 */}
        {model.description && (
          <p className="text-[11px] text-fg-secondary mt-0.5 line-clamp-2 leading-relaxed">
            {model.description}
          </p>
        )}

        {/* 元信息行 / 行内下载进度 */}
        {isRowDownloading ? (
          <div className="mt-1">
            <div className="flex items-center gap-2 flex-wrap">
              {rowDownload.speed && (
                <span className="text-[10px] text-fg-accent font-mono">{rowDownload.speed}</span>
              )}
              {rowDownload.downloaded && (
                <span className="text-[10px] text-fg-secondary font-mono">
                  {rowDownload.downloaded}{rowDownload.total_size ? ` / ${rowDownload.total_size}` : ''}
                </span>
              )}
              {pct !== null && (
                <span className="text-[10px] text-fg-secondary">{pct}%</span>
              )}
              {rowDownload.message && (
                <span className="text-[10px] text-fg-secondary truncate max-w-[160px]">{rowDownload.message}</span>
              )}
            </div>
            {pct !== null && (
              <div className="mt-1 h-0.5 bg-bg-hover rounded-full overflow-hidden">
                <div
                  className="h-full bg-fg-accent rounded-full transition-all duration-500"
                  style={{ width: `${pct}%` }}
                />
              </div>
            )}
          </div>
        ) : rowDownload.status === 'error' ? (
          <div className="flex items-center gap-3 mt-1 flex-wrap">
            <span className="text-[10px] font-medium text-status-error">下载失败</span>
            <span className="text-[10px] text-fg-secondary truncate max-w-[240px]">{rowDownload.message}</span>
          </div>
        ) : rowDownload.status === 'done' ? (
          <div className="flex items-center gap-3 mt-1 flex-wrap">
            <span className="text-[10px] font-medium text-status-success">下载完成</span>
          </div>
        ) : (
          <div className="flex items-center gap-3 mt-1 flex-wrap">
            <span className={clsx(
              'text-[10px] font-medium',
              model.status === 'ok' ? 'text-status-success' :
              model.status === 'missing' ? 'text-status-error' :
              model.status === 'corrupted' ? 'text-orange-400' :
              'text-fg-secondary'
            )}>
              {STATUS_LABEL[model.status] ?? '未知'}
            </span>

            {model.message && model.status !== 'ok' && (
              <span className="text-[10px] text-fg-secondary truncate max-w-[200px]">
                {model.message}
              </span>
            )}

            {model.size_mb && (
              <span className="text-[10px] text-fg-secondary">
                ~{model.size_mb >= 1000 ? `${(model.size_mb / 1024).toFixed(1)} GB` : `${model.size_mb} MB`}
              </span>
            )}

            <span className="text-[10px] text-fg-secondary/50">{model.provider}</span>
          </div>
        )}
      </div>

      {/* 操作区 */}
      <div className="flex items-center gap-1 flex-shrink-0 mt-0.5" onClick={(e) => e.stopPropagation()}>
        {/* 单独下载按钮 */}
        {canDownload && !isConfirming && (
          <button
            onClick={onDownload}
            title="下载此模型"
            className="p-1.5 rounded text-fg-secondary hover:text-fg-accent hover:bg-fg-accent/10 transition-colors"
          >
            <Download size={13} />
          </button>
        )}

        {/* 下载中：取消按钮 */}
        {isRowDownloading && (
          <button
            onClick={onCancel}
            title="取消下载"
            className="p-1.5 rounded text-fg-secondary hover:text-status-error hover:bg-status-error/10 transition-colors"
          >
            <XCircle size={13} />
          </button>
        )}

        {/* 删除区域 */}
        {isDeleting ? (
          <Loader2 size={14} className="animate-spin text-fg-secondary" />
        ) : isConfirming ? (
          <div className="flex items-center gap-1">
            <span className="text-[10px] text-status-error">确认删除?</span>
            <button
              onClick={onDelete}
              className="text-[10px] px-2 py-0.5 bg-status-error/20 text-status-error rounded hover:bg-status-error/30 transition-colors"
            >
              确认
            </button>
            <button
              onClick={onCancelConfirm}
              className="text-[10px] px-2 py-0.5 bg-bg-hover text-fg-secondary rounded hover:bg-bg-active transition-colors"
            >
              取消
            </button>
          </div>
        ) : canDelete ? (
          <button
            onClick={onDelete}
            title="删除本地文件"
            className="p-1.5 rounded text-fg-secondary hover:text-status-error hover:bg-status-error/10 transition-colors"
          >
            <Trash2 size={13} />
          </button>
        ) : null}
      </div>
    </div>
  )
}
