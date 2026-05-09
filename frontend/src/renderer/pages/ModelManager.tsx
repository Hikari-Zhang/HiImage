import React, { useState, useEffect, useCallback, useRef } from 'react'
import {
  CheckCircle2, XCircle, AlertCircle, Loader2, Trash2, RefreshCw,
  Package, Download, ChevronDown, ChevronUp, Clock,
} from 'lucide-react'
import { clsx } from 'clsx'
import { showToast } from '../components/ui'
import { useBackendStore } from '../stores/useBackendStore'
import { useDownloadStore } from '../stores/useDownloadStore'
import { useDownloadManager, registerDownloadDoneCallback } from '../hooks/useDownloadManager'
import { ModelStatus, DownloadStatus, Provider, IOPaintMode, ApiPath } from '../constants'
import type { DownloadTask } from '../stores/useDownloadStore'
import type { ModelStatusValue } from '../constants'

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
  status: ModelStatusValue
  message: string
}

interface ModeGroup {
  id: string
  name: string
}

// ── 辅助 ──────────────────────────────────────────────────────────────────────

const STATUS_ICON: Record<ModelStatusValue, React.ReactElement> = {
  [ModelStatus.OK]:        <CheckCircle2 size={13} className="text-status-success flex-shrink-0" />,
  [ModelStatus.MISSING]:   <XCircle size={13} className="text-status-error flex-shrink-0" />,
  [ModelStatus.PARTIAL]:   <AlertCircle size={13} className="text-yellow-400 flex-shrink-0" />,
  [ModelStatus.CORRUPTED]: <AlertCircle size={13} className="text-orange-400 flex-shrink-0" />,
  [ModelStatus.UNKNOWN]:   <AlertCircle size={13} className="text-fg-secondary flex-shrink-0" />,
}

const STATUS_LABEL: Record<string, string> = {
  [ModelStatus.OK]:        '已下载',
  [ModelStatus.MISSING]:   '未下载',
  [ModelStatus.PARTIAL]:   '不完整',
  [ModelStatus.CORRUPTED]: '已损坏',
  [ModelStatus.UNKNOWN]:   '未知',
}

const BADGE_COLOR: Record<string, string> = {
  推荐:   'bg-blue-500/20 text-blue-400',
  快速:   'bg-green-500/20 text-green-400',
  高质量: 'bg-purple-500/20 text-purple-400',
  动漫:   'bg-pink-500/20 text-pink-400',
}

// ── 主组件 ────────────────────────────────────────────────────────────────────

export default function ModelManager() {
  const backendURL = useBackendStore((s) => s.backendURL)

  const [models, setModels] = useState<ModelEntry[]>([])
  const [modeGroups, setModeGroups] = useState<ModeGroup[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedGroup, setSelectedGroup] = useState<string>('__all__')
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const [bulkPanelOpen, setBulkPanelOpen] = useState(false)

  // 下载相关（来自独立 store）
  const downloadTasks = useDownloadStore((s) => s.tasks)
  const bulkSession = useDownloadStore((s) => s.bulkSession)
  const { startDownload, cancelDownload, startBulkDownload, cancelBulkDownload } = useDownloadManager()

  const downloadListRef = useRef<HTMLDivElement>(null)
  const loadModelsRef = useRef<() => Promise<void>>(async () => {})
  const loadingRef = useRef(false)  // 防止并发调用堆积

  // ── 加载模型列表 ────────────────────────────────────────────────────────────

  const loadModels = useCallback(async () => {
    if (loadingRef.current) return  // 已在加载中，跳过
    loadingRef.current = true
    setLoading(true)
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 15000)  // 15s 超时
    try {
      const url = window.electronAPI?.getBackendURL
        ? await window.electronAPI.getBackendURL()
        : backendURL
      const res = await fetch(`${url}${ApiPath.MODELS_LIST}`, { signal: controller.signal })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json() as { models?: ModelEntry[]; mode_groups?: ModeGroup[] }
      setModels(data.models ?? [])
      setModeGroups(data.mode_groups ?? [])
    } catch (e: unknown) {
      if (e instanceof Error && e.name === 'AbortError') {
        // 超时静默处理，不弹 Toast，loading 会在 finally 里清掉
      } else {
        const msg = e instanceof Error ? e.message : String(e)
        showToast('error', `加载模型列表失败: ${msg}`)
      }
    } finally {
      clearTimeout(timer)
      loadingRef.current = false
      setLoading(false)
    }
  }, [backendURL])

  // 始终保持 ref 指向最新的 loadModels 闭包
  useEffect(() => { loadModelsRef.current = loadModels }, [loadModels])

  // 挂载时无条件执行一次
  useEffect(() => {
    loadModelsRef.current()
  }, [])

  // 注册下载完成回调
  useEffect(() => {
    registerDownloadDoneCallback(() => loadModelsRef.current())
  }, [])

  // ── 批次面板自动展开 ────────────────────────────────────────────────────────

  useEffect(() => {
    if (bulkSession.active) setBulkPanelOpen(true)
  }, [bulkSession.active])

  // ── 自动滚动到底部 ──────────────────────────────────────────────────────────

  useEffect(() => {
    if (downloadListRef.current) {
      downloadListRef.current.scrollTop = downloadListRef.current.scrollHeight
    }
  }, [bulkSession.modelIds.length])

  // ── 分组逻辑 ─────────────────────────────────────────────────────────────────

  const allGroupIds = new Set(modeGroups.map((g) => g.id))

  const filteredModels = (() => {
    if (selectedGroup === '__all__') return models
    if (selectedGroup === '__ungrouped__') {
      return models.filter((m) => !m.tags?.some((t) => allGroupIds.has(t)))
    }
    return models.filter((m) => m.tags?.includes(selectedGroup))
  })()

  const ungroupedCount = models.filter(
    (m) => !m.tags?.some((t) => allGroupIds.has(t))
  ).length

  const groupStats = (groupId: string) => {
    const list = groupId === '__all__'
      ? models
      : groupId === '__ungrouped__'
        ? models.filter((m) => !m.tags?.some((t) => allGroupIds.has(t)))
        : models.filter((m) => m.tags?.includes(groupId))
    const ok = list.filter((m) => m.status === ModelStatus.OK).length
    return { total: list.length, ok }
  }

  // ── 删除文件 ─────────────────────────────────────────────────────────────────

  const handleDelete = async (model: ModelEntry) => {
    if (confirmDeleteId !== model.id) {
      setConfirmDeleteId(model.id)
      return
    }
    setConfirmDeleteId(null)
    setDeletingId(model.id)
    try {
      const url = window.electronAPI?.getBackendURL
        ? await window.electronAPI.getBackendURL()
        : backendURL
      const res = await fetch(`${url}/api/models/${model.id}/files`, { method: 'DELETE' })
      const data = await res.json() as { ok: boolean; message?: string }
      if (data.ok) {
        showToast('success', data.message ?? '删除成功')
        // 更新 downloadStore：删除后状态变为 missing，功能页面模型选择器立即同步
        useDownloadStore.getState().setTask(model.id, {
          status: ModelStatus.MISSING,
          message: '',
          speed: '',
          downloaded: '',
          totalSize: '',
        })
        loadModels()
      } else {
        showToast('error', data.message ?? '删除失败')
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      showToast('error', `删除失败: ${msg}`)
    } finally {
      setDeletingId(null)
    }
  }

  const cancelConfirm = () => setConfirmDeleteId(null)

  // ── 一键下载相关状态 ─────────────────────────────────────────────────────────

  const bulkTasks = bulkSession.modelIds
    .map((id) => downloadTasks[id])
    .filter(Boolean) as DownloadTask[]

  const bulkStatus = (() => {
    if (!bulkSession.active) return 'idle'
    if (bulkTasks.some((t) => t.status === DownloadStatus.DOWNLOADING || t.status === DownloadStatus.QUEUED)) return 'running'
    if (bulkTasks.some((t) => t.status === DownloadStatus.ERROR)) return 'error'
    if (bulkTasks.every((t) => t.status === DownloadStatus.DONE || t.status === ModelStatus.OK || t.status === DownloadStatus.CANCELLED)) return 'done'
    return 'running'
  })()

  const doneCount = bulkTasks.filter((t) => t.status === DownloadStatus.DONE || t.status === ModelStatus.OK).length
  const errorCount = bulkTasks.filter((t) => t.status === DownloadStatus.ERROR).length

  // ── 渲染 ─────────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-1 min-h-0 overflow-hidden" onClick={cancelConfirm}>
      {/* ── 左侧分组导航 ──────────────────────────────────────────────────── */}
      <div className="w-36 flex-shrink-0 border-r border-border-subtle overflow-y-auto py-2">
        <GroupItem
          label="全部"
          stats={groupStats('__all__')}
          active={selectedGroup === '__all__'}
          onClick={() => setSelectedGroup('__all__')}
        />
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
        {ungroupedCount > 0 && (
          <GroupItem
            label="未分组"
            stats={groupStats('__ungrouped__')}
            active={selectedGroup === '__ungrouped__'}
            onClick={() => setSelectedGroup('__ungrouped__')}
          />
        )}
      </div>

      {/* ── 右侧模型列表 ────────────────────────────────────────────────── */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {/* 列表头部工具栏 */}
        <div className="flex items-center justify-between px-4 py-2 border-b border-border-subtle flex-shrink-0 gap-2">
          <span className="text-xs text-fg-secondary flex-shrink-0">
            {filteredModels.length} 个模型
            {filteredModels.length > 0 && (
              <span className="ml-2">
                · 已下载 {filteredModels.filter((m) => m.status === ModelStatus.OK).length}
                · 缺失 {filteredModels.filter((m) => m.status === ModelStatus.MISSING).length}
              </span>
            )}
          </span>

          <div className="flex items-center gap-2 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
            {/* 批次下载完成/失败状态指示 */}
            {bulkStatus === 'done' && (
              <span className="text-[11px] text-status-success flex items-center gap-1">
                <CheckCircle2 size={11} /> 下载完成
              </span>
            )}
            {bulkStatus === 'error' && (
              <span className="text-[11px] text-status-error flex items-center gap-1">
                <XCircle size={11} /> 部分失败
              </span>
            )}

            {/* 展开/收起下载进度面板 */}
            {bulkStatus !== 'idle' && (
              <button
                onClick={() => setBulkPanelOpen((v) => !v)}
                className="flex items-center gap-1 text-[11px] text-fg-secondary hover:text-fg-primary transition-colors"
              >
                {bulkPanelOpen ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                {bulkStatus === 'running' ? '下载中' : '进度'}
              </button>
            )}

            {/* 一键下载 / 取消 按钮 */}
            {bulkStatus === 'running' ? (
              <button
                onClick={cancelBulkDownload}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded transition-colors bg-status-error/20 text-status-error hover:bg-status-error/30"
              >
                <XCircle size={12} />
                取消下载
              </button>
            ) : (
              <button
                onClick={startBulkDownload}
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

        {/* 批次下载进度面板（可折叠） */}
        {bulkStatus !== 'idle' && bulkPanelOpen && (
          <div
            className="border-b border-border-subtle bg-bg-tertiary flex-shrink-0"
            onClick={(e) => e.stopPropagation()}
          >
            {/* 汇总 */}
            <div className="px-4 py-1.5 border-b border-border-subtle">
              <p className="text-[11px] text-fg-secondary">
                {bulkStatus === 'running'
                  ? `下载中：${doneCount} / ${bulkSession.modelIds.length} 完成`
                  : bulkStatus === 'done'
                    ? `全部完成：${doneCount} 个成功，${errorCount} 个失败`
                    : `已完成：${doneCount} 个成功，${errorCount} 个失败`}
              </p>
            </div>

            {/* 各模型进度 */}
            <div ref={downloadListRef} className="max-h-40 overflow-y-auto divide-y divide-border-subtle">
              {bulkTasks.length === 0 && (
                <div className="px-4 py-2 text-[11px] text-fg-secondary">检测中...</div>
              )}
              {bulkTasks.map((task) => (
                <BulkTaskRow key={task.modelId} task={task} />
              ))}
            </div>

            {/* 总进度条 */}
            {bulkSession.modelIds.length > 0 && (
              <div className="px-4 py-2 border-t border-border-subtle">
                <div className="flex justify-between text-[10px] text-fg-secondary mb-1">
                  <span>{doneCount} / {bulkSession.modelIds.length}</span>
                  <span>{Math.round((doneCount / bulkSession.modelIds.length) * 100)}%</span>
                </div>
                <div className="h-1 bg-bg-hover rounded-full overflow-hidden">
                  <div
                    className="h-full bg-border-focus rounded-full transition-all duration-300"
                    style={{ width: `${(doneCount / bulkSession.modelIds.length) * 100}%` }}
                  />
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
              {filteredModels.map((model) => {
                const downloadTask = downloadTasks[model.id]
                return (
                  <ModelRow
                    key={model.id}
                    model={model}
                    downloadTask={downloadTask}
                    isDeleting={deletingId === model.id}
                    isConfirming={confirmDeleteId === model.id}
                    onDownload={(e) => { e.stopPropagation(); startDownload(model.id) }}
                    onCancel={(e) => { e.stopPropagation(); cancelDownload(model.id) }}
                    onDelete={(e) => { e.stopPropagation(); handleDelete(model) }}
                    onCancelConfirm={(e) => { e.stopPropagation(); setConfirmDeleteId(null) }}
                  />
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── 批次进度行 ────────────────────────────────────────────────────────────────

function BulkTaskRow({ task }: { task: DownloadTask }) {
  return (
    <div className="px-4 py-1.5">
      <div className="flex items-center gap-2">
        <span className="flex-shrink-0">
          {task.status === DownloadStatus.DOWNLOADING && <Loader2 size={11} className="animate-spin text-fg-accent" />}
          {task.status === DownloadStatus.QUEUED      && <Clock size={11} className="text-orange-400" />}
          {(task.status === DownloadStatus.DONE || task.status === ModelStatus.OK) && <CheckCircle2 size={11} className="text-status-success" />}
          {task.status === DownloadStatus.ERROR       && <XCircle size={11} className="text-status-error" />}
          {task.status === DownloadStatus.CANCELLED   && <XCircle size={11} className="text-fg-secondary" />}
        </span>
        <span className={clsx(
          'flex-1 text-[11px] truncate',
          task.status === DownloadStatus.ERROR ? 'text-status-error' : 'text-fg-primary',
        )}>
          {task.modelName}
        </span>
        <div className="flex items-center gap-2 flex-shrink-0">
          {task.status === DownloadStatus.DOWNLOADING && task.speed && (
            <span className="text-[10px] text-fg-accent font-mono min-w-[70px] text-right">{task.speed}</span>
          )}
          {task.status === DownloadStatus.DOWNLOADING && task.downloaded && (
            <span className="text-[10px] text-fg-secondary font-mono">
              {task.downloaded}{task.totalSize ? ` / ${task.totalSize}` : ''}
            </span>
          )}
          {task.status === DownloadStatus.QUEUED && (
            <span className="text-[10px] text-orange-400">#{task.position}</span>
          )}
          {task.status !== DownloadStatus.DOWNLOADING && task.status !== DownloadStatus.QUEUED && (
            <span className="text-[10px] text-fg-secondary max-w-[160px] truncate">{task.message}</span>
          )}
          {task.status === DownloadStatus.DOWNLOADING && !task.speed && (
            <span className="text-[10px] text-fg-secondary max-w-[140px] truncate">{task.message}</span>
          )}
        </div>
      </div>
      {/* 单行进度条 */}
      {task.status === DownloadStatus.DOWNLOADING && task.downloaded && task.totalSize && task.totalSize !== '?' && (() => {
        const parseMB = (s: string) => {
          const n = parseFloat(s)
          if (s.includes('GB')) return n * 1024
          if (s.includes('MB')) return n
          if (s.includes('KB')) return n / 1024
          return n / (1024 * 1024)
        }
        const pct = Math.min(100, Math.round(parseMB(task.downloaded) / parseMB(task.totalSize) * 100))
        return (
          <div className="mt-1 h-0.5 bg-bg-hover rounded-full overflow-hidden">
            <div
              className="h-full bg-fg-accent rounded-full transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
        )
      })()}
    </div>
  )
}

// ── 分组项 ────────────────────────────────────────────────────────────────────

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
  model, downloadTask, isDeleting, isConfirming,
  onDownload, onCancel, onDelete, onCancelConfirm,
}: {
  model: ModelEntry
  downloadTask: DownloadTask | undefined
  isDeleting: boolean
  isConfirming: boolean
  onDownload: (e: React.MouseEvent) => void
  onCancel: (e: React.MouseEvent) => void
  onDelete: (e: React.MouseEvent) => void
  onCancelConfirm: (e: React.MouseEvent) => void
}) {
  const isBuiltin = model.provider === Provider.IOPAINT && model.iopaint_mode === IOPaintMode.CLI
  const isDownloading = downloadTask?.status === DownloadStatus.DOWNLOADING
  const isQueued = downloadTask?.status === DownloadStatus.QUEUED
  const isDone = downloadTask?.status === DownloadStatus.DONE || model.status === ModelStatus.OK
  const canDelete = !isBuiltin && isDone && !isDownloading && !isQueued
  const canDownload = !isBuiltin && !isDone && !isDownloading && !isQueued

  // 行内进度百分比
  const pct = (() => {
    if (!downloadTask?.downloaded || !downloadTask.totalSize || downloadTask.totalSize === '?') return null
    const parseMB = (s: string) => {
      const n = parseFloat(s)
      if (s.includes('GB')) return n * 1024
      if (s.includes('MB')) return n
      if (s.includes('KB')) return n / 1024
      return n / (1024 * 1024)
    }
    const p = Math.min(100, Math.round(parseMB(downloadTask.downloaded) / parseMB(downloadTask.totalSize) * 100))
    return isNaN(p) ? null : p
  })()

  return (
    <div className="flex items-start gap-3 px-4 py-3 hover:bg-bg-hover/30 transition-colors">
      {/* 状态图标 */}
      <div className="mt-0.5 flex-shrink-0">
        {isDownloading
          ? <Loader2 size={13} className="animate-spin text-fg-accent" />
          : isQueued
          ? <Clock size={13} className="text-orange-400" />
          : downloadTask?.status === DownloadStatus.DONE
          ? <CheckCircle2 size={13} className="text-status-success" />
          : downloadTask?.status === DownloadStatus.ERROR
          ? <XCircle size={13} className="text-status-error" />
          : (STATUS_ICON[model.status] ?? STATUS_ICON[ModelStatus.UNKNOWN])
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

        {model.description && (
          <p className="text-[11px] text-fg-secondary mt-0.5 line-clamp-2 leading-relaxed">
            {model.description}
          </p>
        )}

        {/* 进度/状态信息行 */}
        {isDownloading ? (
          <div className="mt-1">
            <div className="flex items-center gap-2 flex-wrap">
              {downloadTask.speed && <span className="text-[10px] text-fg-accent font-mono">{downloadTask.speed}</span>}
              {downloadTask.downloaded && (
                <span className="text-[10px] text-fg-secondary font-mono">
                  {downloadTask.downloaded}{downloadTask.totalSize ? ` / ${downloadTask.totalSize}` : ''}
                </span>
              )}
              {pct !== null && <span className="text-[10px] text-fg-secondary">{pct}%</span>}
              {downloadTask.message && <span className="text-[10px] text-fg-secondary truncate max-w-[160px]">{downloadTask.message}</span>}
            </div>
            {pct !== null && (
              <div className="mt-1 h-0.5 bg-bg-hover rounded-full overflow-hidden">
                <div className="h-full bg-fg-accent rounded-full transition-all duration-500" style={{ width: `${pct}%` }} />
              </div>
            )}
          </div>
        ) : isQueued ? (
          <div className="mt-1">
            <span className="text-[10px] text-orange-400">排队中，第 {downloadTask.position} 位</span>
          </div>
        ) : downloadTask?.status === DownloadStatus.ERROR ? (
          <div className="flex items-center gap-3 mt-1 flex-wrap">
            <span className="text-[10px] font-medium text-status-error">下载失败</span>
            <span className="text-[10px] text-fg-secondary truncate max-w-[240px]">{downloadTask.message}</span>
          </div>
        ) : downloadTask?.status === DownloadStatus.DONE ? (
          <div className="mt-1">
            <span className="text-[10px] font-medium text-status-success">下载完成</span>
          </div>
        ) : (
          <div className="flex items-center gap-3 mt-1 flex-wrap">
            <span className={clsx(
              'text-[10px] font-medium',
              model.status === ModelStatus.OK        ? 'text-status-success' :
              model.status === ModelStatus.MISSING   ? 'text-status-error' :
              model.status === ModelStatus.CORRUPTED ? 'text-orange-400' :
              'text-fg-secondary'
            )}>
              {STATUS_LABEL[model.status] ?? '未知'}
            </span>
            {model.message && model.status !== ModelStatus.OK && (
              <span className="text-[10px] text-fg-secondary truncate max-w-[200px]">{model.message}</span>
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

        {/* 下载中/排队中：取消按钮 */}
        {(isDownloading || isQueued) && (
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
