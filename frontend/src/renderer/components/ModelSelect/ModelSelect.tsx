/**
 * ModelSelect —— 带模型下载状态感知的选择器组件。
 *
 * 与普通 Select 的区别：
 *   - 每个选项旁边显示下载状态图标（✅ / ⬇ / ⏳ / 🕐 / ❌）
 *   - 正在下载/排队的选项显示进度信息
 *   - 点击未下载的选项可触发下载
 *   - 当前选中模型若未下载/下载中，在选择框上方显示状态提示
 */

import { useState, useRef, useEffect, useCallback } from 'react'
import { CheckCircle2, Download, Loader2, Clock, XCircle, ChevronDown } from 'lucide-react'
import { clsx } from 'clsx'
import { useDownloadStore } from '../../stores/useDownloadStore'
import { useDownloadManager } from '../../hooks/useDownloadManager'
import { DownloadStatus, ModelStatus } from '../../constants'
import type { TaskStatus } from '../../stores/useDownloadStore'

// ── 类型 ──────────────────────────────────────────────────────────────────────

export interface ModelOption {
  value: string
  label: string
  description?: string
  disabled?: boolean
}

export interface ModelGroup {
  label: string
  options: ModelOption[]
}

interface ModelSelectProps {
  label?: string
  value: string
  onChange: (value: string) => void
  options?: ModelOption[]
  groups?: ModelGroup[]
  size?: 'sm' | 'md'
  className?: string
  /** 是否在选中模型未下载时自动触发下载（默认 true） */
  autoDownloadOnSelect?: boolean
}

// ── 状态图标 ──────────────────────────────────────────────────────────────────

function StatusIcon({ modelId, size = 11 }: { modelId: string; size?: number }) {
  const task = useDownloadStore((s) => s.tasks[modelId])
  const status = task?.status as TaskStatus | undefined

  // 未加载过状态（syncModelStatus 尚未完成）时不显示
  if (!status) return null

  if (status === ModelStatus.OK || status === DownloadStatus.DONE || status === DownloadStatus.CANCELLED) {
    return <CheckCircle2 size={size} className="text-green-400 flex-shrink-0" />
  }
  if (status === ModelStatus.MISSING) {
    return <Download size={size} className="text-fg-secondary flex-shrink-0" />
  }
  if (status === DownloadStatus.DOWNLOADING) {
    return <Loader2 size={size} className="animate-spin text-blue-400 flex-shrink-0" />
  }
  if (status === DownloadStatus.QUEUED) {
    return <Clock size={size} className="text-orange-400 flex-shrink-0" />
  }
  if (status === DownloadStatus.ERROR) {
    return <XCircle size={size} className="text-red-400 flex-shrink-0" />
  }

  return null
}

/** 显示简短的进度文本（仅 downloading 状态时） */
function ProgressText({ modelId }: { modelId: string }) {
  const task = useDownloadStore((s) => s.tasks[modelId])
  if (!task) return null

  if (task.status === DownloadStatus.DOWNLOADING) {
    const parts: string[] = []
    if (task.speed) parts.push(task.speed)
    if (task.downloaded && task.totalSize) parts.push(`${task.downloaded}/${task.totalSize}`)
    else if (task.downloaded) parts.push(task.downloaded)
    if (parts.length === 0 && task.message) parts.push(task.message)
    return parts.length > 0
      ? <span className="text-[10px] text-blue-400 font-mono ml-auto flex-shrink-0">{parts.join(' ')}</span>
      : null
  }

  if (task.status === DownloadStatus.QUEUED) {
    return <span className="text-[10px] text-orange-400 ml-auto flex-shrink-0">#{task.position}</span>
  }

  if (task.status === DownloadStatus.ERROR) {
    return <span className="text-[10px] text-red-400 ml-auto flex-shrink-0 truncate max-w-[100px]">{task.message}</span>
  }

  return null
}

// ── 主组件 ────────────────────────────────────────────────────────────────────

export default function ModelSelect({
  label,
  value,
  onChange,
  options,
  groups,
  size = 'sm',
  className,
  autoDownloadOnSelect = true,
}: ModelSelectProps) {
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const { startDownload } = useDownloadManager()
  const tasks = useDownloadStore((s) => s.tasks)

  // 点击外部关闭
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // 找到当前选中项的 label
  const allOptions = [
    ...(options ?? []),
    ...(groups?.flatMap((g) => g.options) ?? []),
  ]
  const selectedOption = allOptions.find((o) => o.value === value)
  const selectedTask = tasks[value]

  const handleSelect = useCallback((optionValue: string) => {
    onChange(optionValue)
    setOpen(false)
    // 如果选中的模型未下载且开启了自动触发，自动 submit
    if (autoDownloadOnSelect) {
      const task = tasks[optionValue]
      // task 不存在（从未进入队列）或 error 时触发下载
      // done/queued/downloading 时不重复触发
      if (!task || task.status === DownloadStatus.ERROR) {
        // 此处不立即触发，等用户点"开始"按钮时再触发
        // （功能页面自己决定是否立即下载）
      }
    }
  }, [onChange, autoDownloadOnSelect, tasks])

  // 下载图标点击：触发该模型的下载
  const handleDownloadClick = useCallback((e: React.MouseEvent, modelId: string) => {
    e.stopPropagation()
    startDownload(modelId)
  }, [startDownload])

  // 当前选中模型的状态提示文字
  const statusHint = (() => {
    if (!selectedTask) return null
    if (selectedTask.status === ModelStatus.MISSING) {
      return { type: 'missing' as const, text: '模型未下载，点击"开始"按钮将自动下载' }
    }
    if (selectedTask.status === DownloadStatus.DOWNLOADING) {
      const p = [selectedTask.speed, selectedTask.downloaded && selectedTask.totalSize
        ? `${selectedTask.downloaded} / ${selectedTask.totalSize}`
        : selectedTask.downloaded].filter(Boolean).join('  ')
      return { type: 'downloading' as const, text: p || '下载中...' }
    }
    if (selectedTask.status === DownloadStatus.QUEUED) {
      return { type: 'queued' as const, text: `排队中，第 ${selectedTask.position} 位` }
    }
    if (selectedTask.status === DownloadStatus.ERROR) {
      return { type: 'error' as const, text: `下载失败: ${selectedTask.message}` }
    }
    return null
  })()

  const renderOption = (opt: ModelOption) => {
    const task = tasks[opt.value]
    const isSelected = opt.value === value
    const isDownloading = task?.status === DownloadStatus.DOWNLOADING
    const isQueued = task?.status === DownloadStatus.QUEUED
    const isError = task?.status === DownloadStatus.ERROR
    const isMissing = task?.status === ModelStatus.MISSING
    const needDownload = isMissing || isError

    return (
      <div
        key={opt.value}
        onClick={() => !opt.disabled && handleSelect(opt.value)}
        className={clsx(
          'flex items-center gap-2 px-3 py-1.5 cursor-pointer transition-colors',
          size === 'sm' ? 'text-xs' : 'text-sm',
          isSelected ? 'bg-bg-active text-fg-accent' : 'hover:bg-bg-hover text-fg-primary',
          opt.disabled && 'opacity-40 cursor-not-allowed',
        )}
      >
        {/* 状态图标 */}
        <span className="flex-shrink-0 w-3.5 flex items-center justify-center">
          {isDownloading && <Loader2 size={11} className="animate-spin text-blue-400" />}
          {isQueued && <Clock size={11} className="text-orange-400" />}
          {isError && <XCircle size={11} className="text-red-400" />}
          {!isDownloading && !isQueued && !isError && (task?.status === DownloadStatus.DONE || task?.status === ModelStatus.OK) && (
            <CheckCircle2 size={11} className="text-green-400" />
          )}
          {!isDownloading && !isQueued && !isError && task?.status === ModelStatus.MISSING && (
            <Download size={11} className="text-fg-secondary/60" />
          )}
        </span>

        {/* 名称 */}
        <span className="flex-1 truncate">{opt.label}</span>

        {/* 右侧信息 */}
        {isDownloading && (
          <span className="text-[10px] text-blue-400 font-mono flex-shrink-0">
            {task.speed || task.message || '下载中...'}
          </span>
        )}
        {isQueued && (
          <span className="text-[10px] text-orange-400 flex-shrink-0">#{task.position}</span>
        )}
        {isError && !isSelected && (
          <button
            onClick={(e) => handleDownloadClick(e, opt.value)}
            title="重新下载"
            className="p-0.5 rounded hover:bg-red-400/20 text-red-400 flex-shrink-0"
          >
            <Download size={10} />
          </button>
        )}
        {needDownload && !isError && !isDownloading && !isQueued && (
          <button
            onClick={(e) => handleDownloadClick(e, opt.value)}
            title="下载此模型"
            className="p-0.5 rounded hover:bg-fg-accent/20 text-fg-secondary hover:text-fg-accent flex-shrink-0 opacity-0 group-hover:opacity-100"
          >
            <Download size={10} />
          </button>
        )}
      </div>
    )
  }

  return (
    <div className={clsx('relative', className)} ref={containerRef}>
      {label && <label className="text-xs text-fg-secondary mb-1.5 block">{label}</label>}

      {/* 触发按钮 */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={clsx(
          'w-full flex items-center gap-2',
          'bg-bg-primary border border-border-subtle text-fg-primary rounded',
          'focus:border-border-focus focus:outline-none transition-colors text-left',
          size === 'sm' ? 'text-xs px-2 py-1.5' : 'text-sm px-3 py-2',
          open && 'border-border-focus',
        )}
      >
        {/* 当前选中的状态图标 */}
        <span className="flex-shrink-0 w-3.5 flex items-center justify-center">
          <StatusIcon modelId={value} size={11} />
        </span>
        <span className="flex-1 truncate">{selectedOption?.label ?? value}</span>
        <ChevronDown size={12} className={clsx('flex-shrink-0 text-fg-secondary transition-transform', open && 'rotate-180')} />
      </button>

      {/* 状态提示条（选中模型非 idle 时显示） */}
      {statusHint && (
        <div className={clsx(
          'mt-1 px-2 py-1 rounded text-[10px] flex items-center gap-1.5',
          statusHint.type === 'missing'     && 'bg-bg-hover text-fg-secondary',
          statusHint.type === 'downloading' && 'bg-blue-500/10 text-blue-400',
          statusHint.type === 'queued'      && 'bg-orange-500/10 text-orange-400',
          statusHint.type === 'error'       && 'bg-red-500/10 text-red-400',
        )}>
          {statusHint.type === 'missing'     && <Download size={10} className="flex-shrink-0" />}
          {statusHint.type === 'downloading' && <Loader2 size={10} className="animate-spin flex-shrink-0" />}
          {statusHint.type === 'queued'      && <Clock size={10} className="flex-shrink-0" />}
          {statusHint.type === 'error'       && <XCircle size={10} className="flex-shrink-0" />}
          <span className="truncate">{statusHint.text}</span>
        </div>
      )}

      {/* 下拉列表 */}
      {open && (
        <div className={clsx(
          'absolute z-50 w-full mt-1 bg-bg-secondary border border-border-subtle rounded shadow-lg',
          'max-h-60 overflow-y-auto',
        )}>
          {/* 无分组 */}
          {options?.map((opt) => renderOption(opt))}

          {/* 分组 */}
          {groups?.map((group) => (
            <div key={group.label}>
              <div className="px-3 py-1 text-[10px] font-medium text-fg-secondary uppercase tracking-wider border-b border-border-subtle bg-bg-tertiary">
                {group.label}
              </div>
              {group.options.map((opt) => renderOption(opt))}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
