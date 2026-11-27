import type { BeginSourceImport, ImportIsCurrent } from '../services/sourceImport'
import { useEffect, useRef, useState } from 'react'
import { History, ImagePlus, Trash2 } from 'lucide-react'
import ModalDialog from './ModalDialog'
import Button from './ui/Button'
import {
  clearImageHistory, imageHistoryLabels, listImageHistory, loadHistoryImage, removeImageHistory, subscribeImageHistory,
  type ImageHistoryEntry,
} from '../services/imageHistory'

interface ImageHistoryProps {
  beginImport?: BeginSourceImport
  onUse: (file: File, isCurrent?: ImportIsCurrent) => void | Promise<void>
  disabled?: boolean
  useLabel?: string
}

const pageSize = 24
const dateFormat = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' })

function Thumbnail({ entry }: { entry: ImageHistoryEntry }) {
  const frame = useRef<HTMLDivElement>(null)
  const [url, setUrl] = useState('')
  useEffect(() => {
    let active = true
    let objectUrl: string | undefined
    const load = () => {
      void loadHistoryImage(entry).then(file => {
        if (!active) return
        objectUrl = URL.createObjectURL(file)
        setUrl(objectUrl)
      }).catch(() => { if (active) setUrl('') })
    }
    const observer = new IntersectionObserver(([item]) => {
      if (!item.isIntersecting) return
      observer.disconnect()
      load()
    }, { rootMargin: '80px' })
    if (frame.current) observer.observe(frame.current)
    return () => {
      active = false
      observer.disconnect()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [entry.id, entry.name])

  return (
    <div ref={frame} className="flex h-16 w-20 shrink-0 items-center justify-center overflow-hidden rounded bg-workspace">
      {url && <img src={url} alt={entry.name} className="h-full w-full object-contain" loading="lazy" decoding="async" />}
    </div>
  )
}

export default function ImageHistory(props: ImageHistoryProps) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button type="button" size="sm" variant="secondary" icon={<History size={16} />}
        onClick={() => setOpen(true)}>历史记录</Button>
      {open && <HistoryDialog {...props} close={() => setOpen(false)} />}
    </>
  )
}

function HistoryDialog({ beginImport, onUse, disabled, useLabel = '设为原图', close }: ImageHistoryProps & { close: () => void }) {
  const [entries, setEntries] = useState<ImageHistoryEntry[]>([])
  const [limit, setLimit] = useState(pageSize)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [confirmClear, setConfirmClear] = useState(false)
  const [error, setError] = useState('')
  const [loadError, setLoadError] = useState('')
  const [revision, setRevision] = useState(0)

  useEffect(() => subscribeImageHistory(() => setRevision(value => value + 1)), [])
  useEffect(() => {
    let current = true
    void listImageHistory().then(items => {
      if (!current) return
      setEntries(items)
      setLoadError('')
    }).catch(() => {
      if (current) setLoadError('读取历史记录失败')
    }).finally(() => {
      if (current) setLoading(false)
    })
    return () => { current = false }
  }, [revision])

  async function run(action: () => void | Promise<void>, failure: string) {
    setBusy(true)
    setError('')
    try {
      await action()
    } catch (error) {
      setError(error instanceof Error && error.message ? error.message : failure)
    } finally {
      setBusy(false)
    }
  }

  return (
    <ModalDialog title="历史记录" busy={busy} close={close}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-fg-secondary">四个工作台共用历史记录，图片保存在当前浏览器。</p>
        {entries.length > 0 && !confirmClear && (
          <Button type="button" size="sm" variant="ghost" icon={<Trash2 size={14} />} disabled={busy}
            onClick={() => setConfirmClear(true)}>清空历史记录</Button>
        )}
      </div>
      {confirmClear && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-border-subtle p-2 text-sm">
          <span className="flex-1">删除当前浏览器的全部历史图片？</span>
          <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setConfirmClear(false)}>取消</Button>
          <Button type="button" size="sm" variant="danger" disabled={busy} onClick={() => void run(async () => {
            await clearImageHistory()
            setEntries([])
            setLimit(pageSize)
            setConfirmClear(false)
          }, '清空历史记录失败')}>确认清空</Button>
        </div>
      )}
      {error && <p role="alert" className="mb-2 text-sm text-status-error">{error}</p>}
      {loadError && (
        <div className="mb-2 flex items-center gap-2">
          <p role="alert" className="text-sm text-status-error">{loadError}</p>
          <Button type="button" size="sm" variant="ghost" disabled={busy}
            onClick={() => setRevision(value => value + 1)}>重试</Button>
        </div>
      )}
      {loading && <p role="status" className="py-6 text-center text-sm text-fg-secondary">正在读取历史记录…</p>}
      {!loading && !loadError && entries.length === 0 && (
        <p className="py-8 text-center text-sm text-fg-secondary">暂无历史图片。处理成功的图片会自动记录。</p>
      )}
      <ul className="divide-y divide-border-subtle">
        {entries.slice(0, limit).map(entry => (
          <li key={entry.id} className="flex flex-wrap items-center gap-3 py-2 sm:flex-nowrap">
            <Thumbnail entry={entry} />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium" title={entry.name}>{entry.name}</p>
              <p className="text-xs text-fg-secondary">{imageHistoryLabels[entry.operation]} · {dateFormat.format(entry.createdAt)}</p>
            </div>
            <div className="flex shrink-0 flex-wrap gap-1">
              <Button type="button" size="sm" variant="secondary" icon={<ImagePlus size={14} />}
                disabled={busy || disabled || confirmClear} onClick={() => void run(async () => {
                  const isCurrent = beginImport?.()
                  const file = await loadHistoryImage(entry)
                  if (!isCurrent || isCurrent()) await onUse(file, isCurrent)
                  close()
                }, '载入历史图片失败')}>{useLabel}</Button>
              <Button type="button" size="sm" variant="ghost" disabled={busy || confirmClear}
                onClick={() => void run(async () => {
                  await removeImageHistory(entry.id)
                  setEntries(items => items.filter(item => item.id !== entry.id))
                }, '删除历史图片失败')}>删除</Button>
            </div>
          </li>
        ))}
      </ul>
      {entries.length > limit && <Button type="button" size="sm" variant="ghost" className="mt-2"
        onClick={() => setLimit(value => value + pageSize)}>显示更多</Button>}
    </ModalDialog>
  )
}
