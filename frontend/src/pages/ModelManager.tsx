import React, { useState, useEffect, useRef } from 'react'
import {
  CheckCircle2, XCircle, AlertCircle, Loader2, Trash2, RefreshCw,
  Package, Download, ChevronDown, ChevronUp, Clock, FolderOpen,
} from 'lucide-react'
import { clsx } from 'clsx'
import { showToast } from '../components/ui'
import { useBackendStore } from '../stores/useBackendStore'
import { clearDeletedModelTasks, useDownloadStore } from '../stores/useDownloadStore'
import { useDownloadManager } from '../hooks/useDownloadManager'
import { ModelStatus, DownloadStatus, ApiPath } from '../constants'
import type { DownloadTask } from '../stores/useDownloadStore'
import type { ModelStatusValue } from '../constants'
import { createApiClient } from '../services/apiClient'
import { refreshModelList, useModelList, type ModelEntry } from '../hooks/useModelList'

// ── 类型 ──────────────────────────────────────────────────────────────────────

interface WeightResource {
  id: string
  label: string
  affected_models: { id: string; name: string }[]
}

interface WeightRemoval {
  model: ModelEntry
  resources: WeightResource[]
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

/** 将带单位的大小字符串（如 "1.2 GB"、"300 MB"）统一转换为 MB 数值 */
function parseMB(s: string): number {
  const n = parseFloat(s)
  if (s.includes('GB')) return n * 1024
  if (s.includes('MB')) return n
  if (s.includes('KB')) return n / 1024
  return n / (1024 * 1024)
}

// ── 主组件 ────────────────────────────────────────────────────────────────────

export default function ModelManager() {
  const backend = useBackendStore((state) => createApiClient(state.backendURL).baseUrl)
  return <BackendModelManager key={backend} backend={backend} />
}

function BackendModelManager({ backend }: { backend: string }) {
  const client = createApiClient(backend || '/')
  let showOpenFolder = false
  try {
    const backendAddress = new URL(client.url(ApiPath.MODELS_LIST), window.location.href)
    showOpenFolder = ['localhost', '127.0.0.1', '[::1]'].includes(backendAddress.hostname)
  } catch {
    // 无效的后端地址无法打开本地目录。
  }

  const { data, error: listError, loading } = useModelList(backend)
  const models = data?.models ?? []
  const modeGroups = data?.mode_groups ?? []
  const [selectedGroup, setSelectedGroup] = useState<string>('__all__')
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [weightRemoval, setWeightRemoval] = useState<WeightRemoval | null>(null)
  const [deleteError, setDeleteError] = useState('')
  const [bulkPanelOpen, setBulkPanelOpen] = useState(false)

  // 下载相关（来自独立 store）
  const downloadTasks = useDownloadStore((s) => s.tasks)
  const bulkSession = useDownloadStore((s) => s.bulkSession)
  const { startDownload, cancelDownload, startBulkDownload, cancelBulkDownload } = useDownloadManager()

  const downloadListRef = useRef<HTMLDivElement>(null)
  const mountedRef = useRef(false)

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])
  useEffect(() => {
    if (listError) showToast('error', `加载模型列表失败: ${listError}`)
  }, [listError])
  const loadModels = () => refreshModelList(backend)

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

  const handleOpenFolder = async (modelId: string) => {
    try {
      await client.json(`/api/models/${encodeURIComponent(modelId)}/open-folder`, {
        method: 'POST',
        headers: { 'X-HiImage-Action': 'open-folder' },
      }, '打开模型目录失败')
    } catch (e: unknown) {
      showToast('error', e instanceof Error ? e.message : String(e))
    }
  }

  // ── 删除文件 ─────────────────────────────────────────────────────────────────

  const loadWeightRemoval = async (model: ModelEntry) => {
    setDeleteError('')
    setDeletingId(model.id)
    try {
      const data = await client.json<{ resources: WeightResource[] }>(
        `/api/models/${encodeURIComponent(model.id)}/file-resources`, undefined, '读取本地权重失败',
      )
      if (!mountedRef.current) return
      if (data.resources.length) setWeightRemoval({ model, resources: data.resources })
      else showToast('info', '未找到可删除的本地权重')
    } catch (error) {
      if (mountedRef.current) showToast('error', error instanceof Error ? error.message : String(error))
    } finally {
      if (mountedRef.current) setDeletingId(null)
    }
  }

  const deleteWeight = async (model: ModelEntry, resource: WeightResource) => {
    setDeleteError('')
    setDeletingId(model.id)
    try {
      const data = await client.json<{ ok: boolean; message: string; affected_model_ids: string[] }>(
        `/api/models/${encodeURIComponent(model.id)}/file-resources`,
        {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            resource_id: resource.id,
            affected_model_ids: resource.affected_models.map(({ id }) => id),
          }),
        },
        '删除模型权重失败',
      )
      if (data.ok) {
        clearDeletedModelTasks(backend, data.affected_model_ids)
        void refreshModelList(backend)
        if (mountedRef.current) {
          showToast('success', data.message)
          setWeightRemoval(null)
        }
      } else {
        if (mountedRef.current) setDeleteError(data.message)
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      if (mountedRef.current) setDeleteError(msg)
    } finally {
      if (mountedRef.current) setDeletingId(null)
    }
  }

  // ── 一键下载相关状态 ─────────────────────────────────────────────────────────

  const bulkTasks = bulkSession.modelIds
    .map((id) => downloadTasks[id])
    .filter(Boolean) as DownloadTask[]

  const bulkStatus = (() => {
    if (!bulkSession.active) return 'idle'
    if (bulkTasks.some((t) => t.statusSyncFailed || t.status === DownloadStatus.DOWNLOADING || t.status === DownloadStatus.QUEUED)) return 'running'
    if (bulkTasks.some((t) => t.status === DownloadStatus.ERROR)) return 'error'
    if (bulkTasks.every((t) => t.status === DownloadStatus.DONE || t.status === ModelStatus.OK || t.status === DownloadStatus.CANCELLED)) return 'done'
    return 'running'
  })()

  const doneCount = bulkTasks.filter((t) => t.status === DownloadStatus.DONE || t.status === ModelStatus.OK).length
  const errorCount = bulkTasks.filter((t) => t.status === DownloadStatus.ERROR && !t.statusSyncFailed).length
  const unconfirmedCount = bulkTasks.filter((t) => t.statusSyncFailed).length

  // ── 渲染 ─────────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-1 min-h-0 overflow-hidden">
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
                  ? unconfirmedCount
                    ? `下载状态未确认：${unconfirmedCount} 个待重新连接；${doneCount} / ${bulkSession.modelIds.length} 完成`
                    : `下载中：${doneCount} / ${bulkSession.modelIds.length} 完成`
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
                    deleteDisabled={deletingId !== null}
                    onDownload={(e) => { e.stopPropagation(); startDownload(model.id) }}
                    onCancel={(e) => { e.stopPropagation(); cancelDownload(model.id) }}
                    onDelete={() => { void loadWeightRemoval(model) }}
                    onOpenFolder={showOpenFolder ? () => handleOpenFolder(model.id) : undefined}
                  />
                )
              })}
            </div>
          )}
        </div>
      </div>
      {weightRemoval && (
        <WeightRemovalDialog
          plan={weightRemoval}
          busy={deletingId !== null}
          error={deleteError}
          onClose={() => setWeightRemoval(null)}
          onDelete={(resource) => { void deleteWeight(weightRemoval.model, resource) }}
        />
      )}
    </div>
  )
}

function WeightRemovalDialog({ plan, busy, error, onClose, onDelete }: {
  plan: WeightRemoval
  busy: boolean
  error: string
  onClose: () => void
  onDelete: (resource: WeightResource) => void
}) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [selectedId, setSelectedId] = useState(plan.resources[0].id)
  const resource = plan.resources.find(({ id }) => id === selectedId) ?? plan.resources[0]
  useEffect(() => {
    const dialog = dialogRef.current
    dialog?.showModal()
    return () => dialog?.close()
  }, [])

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="weight-removal-title"
      onCancel={(event) => { event.preventDefault(); if (!busy) onClose() }}
      className="w-[min(92vw,480px)] max-h-[85vh] overflow-auto rounded-xl bg-bg-secondary p-5 text-fg-primary backdrop:bg-black/60"
    >
      <h2 id="weight-removal-title" className="font-medium">删除模型权重</h2>
      <p className="mt-1 text-sm text-fg-secondary">{plan.model.name}</p>
      <fieldset disabled={busy} className="mt-4 space-y-2">
        <legend className="text-sm text-fg-secondary">选择要删除的权重</legend>
        {plan.resources.map((entry) => (
          <label key={entry.id} className="flex items-start gap-2 break-all rounded bg-bg-hover px-3 py-2 text-sm">
            <input
              type="radio"
              name="weight-resource"
              checked={entry.id === resource.id}
              onChange={() => setSelectedId(entry.id)}
              className="mt-1"
            />
            {entry.label}
          </label>
        ))}
      </fieldset>
      <p className="mt-4 text-sm text-fg-secondary">
        {resource.affected_models.length > 1 ? '这是共享权重。以下模型需要重新下载该权重。' : '以下模型需要重新下载该权重。'}
      </p>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
        {resource.affected_models.map(({ id, name }) => <li key={id}>{name}</li>)}
      </ul>
      {error && <p role="alert" className="mt-4 text-sm text-status-error">{error}</p>}
      <div className="mt-5 flex justify-end gap-2">
        <button type="button" disabled={busy} onClick={onClose} className="rounded bg-bg-hover px-3 py-2 text-sm">取消</button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onDelete(resource)}
          className="rounded bg-status-error/20 px-3 py-2 text-sm text-status-error disabled:opacity-50"
        >
          {busy ? '正在删除…' : '确认删除'}
        </button>
      </div>
    </dialog>
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
          {task.statusSyncFailed && <AlertCircle size={11} className="text-orange-400" />}
          {task.status === DownloadStatus.ERROR && !task.statusSyncFailed && <XCircle size={11} className="text-status-error" />}
          {task.status === DownloadStatus.CANCELLED   && <XCircle size={11} className="text-fg-secondary" />}
        </span>
        <span className={clsx(
          'flex-1 text-[11px] truncate',
          task.status === DownloadStatus.ERROR && !task.statusSyncFailed ? 'text-status-error' : 'text-fg-primary',
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
  model, downloadTask, isDeleting, deleteDisabled,
  onDownload, onCancel, onDelete, onOpenFolder,
}: {
  model: ModelEntry
  downloadTask: DownloadTask | undefined
  isDeleting: boolean
  deleteDisabled: boolean
  onDownload: (e: React.MouseEvent) => void
  onCancel: (e: React.MouseEvent) => void
  onDelete: () => void
  onOpenFolder?: () => void
}) {
  const isDownloading = downloadTask?.status === DownloadStatus.DOWNLOADING
  const isQueued = downloadTask?.status === DownloadStatus.QUEUED
  const syncFailed = downloadTask?.status === DownloadStatus.ERROR && downloadTask.statusSyncFailed === true
  const isDone = downloadTask?.status === DownloadStatus.DONE || model.status === ModelStatus.OK
  const canDelete = (model.has_local_path ?? isDone) && !isDownloading && !isQueued && !syncFailed
  const canDownload = !isDone && !isDownloading && !isQueued

  // 行内进度百分比
  const pct = (() => {
    if (!downloadTask?.downloaded || !downloadTask.totalSize || downloadTask.totalSize === '?') return null
    const p = Math.min(100, Math.round(parseMB(downloadTask.downloaded) / parseMB(downloadTask.totalSize) * 100))
    return isNaN(p) ? null : p
  })()

  return (
    <div className="flex items-start gap-3 px-4 py-3 hover:bg-bg-hover/30 transition-colors">
      {/* 状态图标 */}
      <div className="mt-0.5 flex-shrink-0">
        {syncFailed
          ? <AlertCircle size={13} className="text-orange-400" />
          : isDownloading
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
            <span className={clsx('text-[10px] font-medium', syncFailed ? 'text-orange-400' : 'text-status-error')}>
              {syncFailed ? '下载状态未确认' : '下载失败'}
            </span>
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
        {(canDownload || syncFailed) && (
          <button
            onClick={onDownload}
            title={syncFailed ? '重新连接下载状态' : '下载此模型'}
            className="p-1.5 rounded text-fg-secondary hover:text-fg-accent hover:bg-fg-accent/10 transition-colors"
          >
            {syncFailed ? <RefreshCw size={13} /> : <Download size={13} />}
          </button>
        )}

        {/* 下载中/排队中：取消按钮 */}
        {(isDownloading || isQueued || syncFailed) && (
          <button
            onClick={onCancel}
            title="取消下载"
            className="p-1.5 rounded text-fg-secondary hover:text-status-error hover:bg-status-error/10 transition-colors"
          >
            <XCircle size={13} />
          </button>
        )}

        {onOpenFolder && (
          <button
            type="button"
            onClick={onOpenFolder}
            disabled={isDeleting || !(model.has_local_path ?? isDone)}
            title={(model.has_local_path ?? isDone) ? '打开模型所在目录' : '暂无本地模型文件'}
            aria-label={`打开 ${model.name} 所在目录`}
            className="flex items-center gap-1 px-2 py-1.5 rounded text-[11px] text-fg-secondary enabled:hover:text-fg-accent enabled:hover:bg-fg-accent/10 disabled:opacity-40 transition-colors"
          >
            <FolderOpen size={13} />
            打开目录
          </button>
        )}

        {/* 删除区域 */}
        {isDeleting ? (
          <Loader2 size={14} className="animate-spin text-fg-secondary" />
        ) : canDelete ? (
          <button
            onClick={onDelete}
            disabled={deleteDisabled}
            title="删除模型权重"
            aria-label={`删除 ${model.name} 的模型权重`}
            className="p-1.5 rounded text-fg-secondary hover:text-status-error hover:bg-status-error/10 transition-colors"
          >
            <Trash2 size={13} />
          </button>
        ) : null}
      </div>
    </div>
  )
}
