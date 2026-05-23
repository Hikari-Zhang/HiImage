import { useState, useRef } from 'react'
import { X, CheckCircle, Loader2, AlertCircle, Image as ImageIcon } from 'lucide-react'
import { Button } from '../ui'

export type ImageQueueItem = {
  id: string
  fileName: string
  filePath?: string
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
  disabled?: boolean
}

export default function BatchImageQueue({
  items,
  currentIndex,
  onRemove,
  onClear,
  onAdd,
  disabled = false,
}: BatchImageQueueProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [isDragOver, setIsDragOver] = useState(false)

  const getThumbnail = (item: ImageQueueItem): string => {
    if (item.dataUrl) return item.dataUrl
    // 对于文件路径，需要异步加载，这里先返回占位符
    return ''
  }

  const getStatusIcon = (item: ImageQueueItem, index: number) => {
    if (currentIndex === index && item.status === 'pending') {
      return <Loader2 size={16} className="animate-spin text-blue-400" />
    }
    switch (item.status) {
      case 'success':
        return <CheckCircle size={16} className="text-green-400" />
      case 'error':
        return <AlertCircle size={16} className="text-red-400" />
      case 'processing':
        return <Loader2 size={16} className="animate-spin text-blue-400" />
      default:
        return null
    }
  }

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (files && files.length > 0) {
      onAdd(files)
    }
    // 重置 input 以允许重复选择同一文件
    e.target.value = ''
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(false)
    const files = e.dataTransfer.files
    if (files && files.length > 0) {
      onAdd(files)
    }
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(true)
  }

  const handleDragLeave = () => {
    setIsDragOver(false)
  }

  const pendingCount = items.filter(i => i.status === 'pending').length
  const successCount = items.filter(i => i.status === 'success').length
  const errorCount = items.filter(i => i.status === 'error').length

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
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
          <Button
            variant="ghost"
            size="sm"
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled}
          >
            添加
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={onClear}
            disabled={items.length === 0 || disabled}
          >
            清空
          </Button>
        </div>
      </div>

      {/* Drop zone + Image grid */}
      <div
        className={`flex-1 overflow-y-auto rounded border-2 border-dashed transition-colors ${
          isDragOver
            ? 'border-blue-400 bg-blue-400/10'
            : 'border-border-subtle hover:border-border-focus'
        } p-2 min-h-[200px]`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
      >
        {items.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-fg-tertiary gap-2">
            <ImageIcon size={48} strokeWidth={1} />
            <p className="text-sm">拖拽图片到此处，或点击"添加"按钮</p>
            <p className="text-xs">支持 PNG、JPG、WEBP、BMP、TIFF</p>
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-2">
            {items.map((item, index) => (
              <div
                key={item.id}
                className={`relative group rounded border transition-all ${
                  currentIndex === index
                    ? 'border-blue-400 shadow-lg shadow-blue-400/20'
                    : 'border-border-subtle hover:border-border-focus'
                }`}
              >
                {/* Thumbnail */}
                <div className="aspect-square flex items-center justify-center bg-bg-primary rounded-t overflow-hidden">
                  {item.dataUrl ? (
                    <img
                      src={item.dataUrl}
                      alt={item.fileName}
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <ImageIcon size={32} className="text-fg-tertiary" />
                  )}
                </div>

                {/* Status overlay */}
                <div className="absolute top-1 right-1">
                  {getStatusIcon(item, index)}
                </div>

                {/* Remove button */}
                <button
                  onClick={() => onRemove(item.id)}
                  disabled={disabled}
                  className="absolute top-1 left-1 opacity-0 group-hover:opacity-100 transition-opacity p-0.5 rounded-full bg-bg-primary/80 hover:bg-red-500/80 text-fg-primary"
                >
                  <X size={12} />
                </button>

                {/* File name */}
                <div className="px-1 py-0.5 text-[10px] text-fg-secondary truncate border-t border-border-subtle">
                  {item.fileName}
                </div>

                {/* Error message */}
                {item.status === 'error' && item.error && (
                  <div className="px-1 py-0.5 text-[9px] text-red-400 border-t border-border-subtle truncate">
                    {item.error}
                  </div>
                )}

                {/* Processing indicator */}
                {currentIndex === index && item.status === 'processing' && (
                  <div className="absolute inset-0 flex items-center justify-center bg-bg-primary/50 rounded">
                    <Loader2 size={24} className="animate-spin text-blue-400" />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Hidden file input */}
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
