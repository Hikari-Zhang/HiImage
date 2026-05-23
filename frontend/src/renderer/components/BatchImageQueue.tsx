import { useState, useRef, useCallback, useMemo, useEffect, memo } from 'react'
import { X, CheckCircle, Loader2, AlertCircle, Image as ImageIcon } from 'lucide-react'
import { Button } from '../components/ui'

export type ImageQueueItem = {
  id: string
  fileName: string
  filePath?: string
  file?: File
  dataUrl?: string
  status: 'pending' | 'processing' | 'success' | 'error'
  error?: string
  resultPath?: string
  resultDataUrl?: string
}

interface BatchImageQueueProps {
  items: ImageQueueItem[]
  currentIndex: number | null
  onRemove: (id: string) => void
  onClear: () => void
  onAdd: (files: FileList | string[]) => void
  onLoadThumbnail?: (id: string) => void
  disabled?: boolean
}

// ─── 常量 ───────────────────────────────────────────────
const GRID_COLS = 3
const ROW_HEIGHT = 250 // 正方形缩略图 + 文件名 + 错误行
const BUFFER_ROWS = 4

// ─── 单个卡片（memo 化，避免整列表 re-render）────────────────────────────
const QueueCard = memo(function QueueCard({
  item,
  index,
  isCurrent,
  statusIcon,
  onRemove,
  disabled,
  onVisible,    // 进入视口回调
  cardRef,
}: {
  item: ImageQueueItem
  index: number
  isCurrent: boolean
  statusIcon: React.ReactNode
  onRemove: () => void
  disabled: boolean
  onVisible: (el: HTMLDivElement) => void
  cardRef: (el: HTMLDivElement | null) => void
}) {
  return (
    <div
      ref={(el) => {
        cardRef(el)
        if (el) onVisible(el)
      }}
      className={`relative group rounded border transition-all flex flex-col h-full ${
        isCurrent
          ? 'border-blue-400 shadow-lg shadow-blue-400/20'
          : 'border-border-subtle hover:border-border-focus'
      }`}
    >
      {/* 缩略图：正方形 */}
      <div className="aspect-square flex items-center justify-center bg-bg-primary rounded-t overflow-hidden">
        {item.dataUrl ? (
          <img
            src={item.dataUrl}
            alt={item.fileName}
            className="w-full h-full object-cover"
            loading="lazy"
          />
        ) : (
          <ImageIcon size={32} className="text-fg-tertiary" />
        )}
      </div>

      {/* 状态图标 */}
      <div className="absolute top-1 right-1">{statusIcon}</div>

      {/* 删除按钮 */}
      <button
        onClick={onRemove}
        disabled={disabled}
        className="absolute top-1 left-1 opacity-0 group-hover:opacity-100 transition-opacity p-0.5 rounded-full bg-bg-primary/80 hover:bg-red-500/80 text-fg-primary"
      >
        <X size={12} />
      </button>

      {/* 文件名 */}
      <div className="px-1 py-0.5 text-[10px] text-fg-secondary truncate border-t border-border-subtle">
        {item.fileName}
      </div>

      {/* 错误信息 */}
      {item.status === 'error' && item.error && (
        <div className="px-1 py-0.5 text-[9px] text-red-400 border-t border-border-subtle truncate">
          {item.error}
        </div>
      )}

      {/* 处理中遮罩 */}
      {isCurrent && item.status === 'processing' && (
        <div className="absolute inset-0 flex items-center justify-center bg-bg-primary/50 rounded">
          <Loader2 size={24} className="animate-spin text-blue-400" />
        </div>
      )}
    </div>
  )
})

// ─── 主组件 ────────────────────────────────────────────
export default function BatchImageQueue({
  items,
  currentIndex,
  onRemove,
  onClear,
  onAdd,
  onLoadThumbnail,
  disabled = false,
}: BatchImageQueueProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [isDragOver, setIsDragOver] = useState(false)

  // ── 虚拟滚动 ──────────────────────────────────────
  const [visibleRange, setVisibleRange] = useState({ startRow: 0, endRow: 0 })
  const totalRows = Math.ceil(items.length / GRID_COLS)

  const updateVisibleRange = useCallback(() => {
    const c = containerRef.current
    if (!c) return
    const { scrollTop, clientHeight } = c
    const s = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - BUFFER_ROWS)
    const e = Math.min(totalRows, Math.ceil((scrollTop + clientHeight) / ROW_HEIGHT) + BUFFER_ROWS)
    setVisibleRange(prev => (prev.startRow === s && prev.endRow === e ? prev : { startRow: s, endRow: e }))
  }, [totalRows])

  useEffect(() => {
    const c = containerRef.current
    if (!c) return
    updateVisibleRange()
    c.addEventListener('scroll', updateVisibleRange, { passive: true })
    return () => c.removeEventListener('scroll', updateVisibleRange)
  }, [updateVisibleRange, items.length])

  const visibleStartIdx = visibleRange.startRow * GRID_COLS
  const visibleEndIdx = Math.min(items.length, visibleRange.endRow * GRID_COLS)

  // ── 统一 IntersectionObserver（性能关键）────────────────
  const observerRef = useRef<IntersectionObserver | null>(null)
  const pendingLoads = useRef(new Map<string, HTMLDivElement>())

  useEffect(() => {
    observerRef.current = new IntersectionObserver(
      (entries) => {
        entries.forEach(entry => {
          if (entry.isIntersecting) {
            const el = entry.target as HTMLDivElement
            const id = el.dataset.id
            if (id && onLoadThumbnail) {
              onLoadThumbnail(id)
            }
            observerRef.current?.unobserve(el)
          }
        })
      },
      { root: containerRef.current, threshold: 0.1 },
    )
    return () => observerRef.current?.disconnect()
  }, [onLoadThumbnail])

  const cardRef = useCallback((el: HTMLDivElement | null) => {
    if (el) {
      observerRef.current?.observe(el)
    }
  }, [])

  // ── 状态统计 ────────────────────────────────────────
  const pendingCount = useMemo(() => items.filter(i => i.status === 'pending').length, [items])
  const successCount = useMemo(() => items.filter(i => i.status === 'success').length, [items])
  const errorCount = useMemo(() => items.filter(i => i.status === 'error').length, [items])

  // ── 添加文件 ────────────────────────────────────────
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleAddClick = async () => {
    if (window.electronAPI?.openFiles) {
      try {
        const paths = await window.electronAPI.openFiles()
        if (paths && paths.length > 0) onAdd(paths)
      } catch { /* 用户取消 */ }
      return
    }
    fileInputRef.current?.click()
  }

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (files && files.length > 0) onAdd(files)
    e.target.value = ''
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(false)
    const files = e.dataTransfer.files
    if (files && files.length > 0) onAdd(files)
  }

  // ── 生成可见行的索引数组（稳定引用）────────────────────
  const visibleRowIndices = useMemo(() => {
    const rows: number[] = []
    for (let r = visibleRange.startRow; r < visibleRange.endRow; r++) {
      if (r * GRID_COLS >= items.length) break
      rows.push(r)
    }
    return rows
  }, [visibleRange.startRow, visibleRange.endRow, items.length])

  // ── 渲染 ────────────────────────────────────────────
  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between mb-2 flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-fg-secondary">
              图片队列 ({items.length} 张)
          </span>
          {pendingCount > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 bg-blue-500/20 text-blue-400 rounded">
              {pendingCount} 待处理
            </span>
          )}
          {successCount > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 bg-green-500/20 text-green-400 rounded">
              {successCount} 成功
            </span>
          )}
          {errorCount > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 bg-red-500/20 text-red-400 rounded">
              {errorCount} 失败
            </span>
          )}
        </div>
        <div className="flex gap-1">
          <Button variant="ghost" size="sm" onClick={handleAddClick} disabled={disabled}>
              添加
          </Button>
          <Button variant="ghost" size="sm" onClick={onClear} disabled={items.length === 0 || disabled}>
              清空
          </Button>
        </div>
      </div>

      {/* 拖拽区域 + 虚拟滚动网格 */}
      <div
        ref={containerRef}
        className={`flex-1 overflow-y-auto rounded border-2 border-dashed transition-colors ${
          isDragOver
            ? 'border-blue-400 bg-blue-400/10'
            : 'border-border-subtle hover:border-border-focus'
        } min-h-[200px] flex-shrink-0`}
        onDrop={handleDrop}
        onDragOver={e => { e.preventDefault(); setIsDragOver(true) }}
        onDragLeave={() => setIsDragOver(false)}
      >
        {items.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-fg-tertiary gap-2">
            <ImageIcon size={48} strokeWidth={1} />
            <p className="text-sm">拖拽图片到此处，或点击"添加"按钮</p>
            <p className="text-xs">支持 PNG、JPG、WEBP、BMP、TIFF</p>
          </div>
        ) : (
          <div style={{ height: totalRows * ROW_HEIGHT, position: 'relative', width: '100%' }}>
            {/* 可见区域偏移 */}
            <div style={{ position: 'absolute', top: visibleRange.startRow * ROW_HEIGHT, left: 0, right: 0 }}>
              {visibleRowIndices.map(rowIdx => {
                const itemIdx = rowIdx * GRID_COLS
                return (
                  <div key={`row-${rowIdx}`} className="grid grid-cols-3 gap-2" style={{ height: ROW_HEIGHT }}>
                    {[0, 1, 2].map(col => {
                      const idx = itemIdx + col
                      if (idx >= items.length) return <div key={`e-${idx}`} />
                      const item = items[idx]
                      return (
                        <QueueCard
                          key={item.id}
                          item={item}
                          index={idx}
                          isCurrent={currentIndex === idx}
                          statusIcon={
                            item.status === 'success'
                              ? <CheckCircle size={16} className="text-green-400" />
                              : item.status === 'error'
                              ? <AlertCircle size={16} className="text-red-400" />
                              : null
                          }
                          onRemove={() => onRemove(item.id)}
                          disabled={disabled}
                          onVisible={(el) => {
                            if (el) (el as HTMLDivElement).dataset.id = item.id
                            cardRef(el)
                          }}
                          cardRef={() => {}}
                        />
                      )
                    })}
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>

      {/* 隐藏的文件输入框 */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={handleFileInput}
      />
    </div>
  )
}
