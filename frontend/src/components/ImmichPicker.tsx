import type { BeginSourceImport, ImportIsCurrent } from '../services/sourceImport'
import { MAX_FILE_BYTES, MAX_TOTAL_INPUT_BYTES } from '../services/batchLimits'
import { useEffect, useState } from 'react'
import { immichApi, type ImmichAlbum, type ImmichAsset } from '../services/immich'
import Button from './ui/Button'
import ImmichDialog from './ModalDialog'

export type ImmichImport = { file: File; id: string; token: string; taken?: string; backend: string }
const fieldClass = 'bg-bg-tertiary rounded p-2 text-sm'

function Thumbnail({ asset, preview = false }: { asset: ImmichAsset; preview?: boolean }) {
  const [url, setUrl] = useState('')
  useEffect(() => {
    const abort = new AbortController()
    let objectUrl = ''
    void immichApi().fetch(`/assets/${asset.id}/thumbnail?size=${preview ? 'preview' : 'thumbnail'}`, { signal: abort.signal })
      .then(response => response.blob()).then(blob => {
        if (abort.signal.aborted) return
        objectUrl = URL.createObjectURL(blob)
        setUrl(objectUrl)
      }).catch(() => {})
    return () => { abort.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [asset.id, preview])
  return url ? <img src={url} alt={preview ? asset.name : ""} className={preview ? "mx-auto max-h-72 object-contain" : "w-full aspect-square object-contain"} />
    : <span className="block aspect-square bg-bg-primary" />
}

export default function ImmichPicker({ close, limit, multiple, beginImport, onImport }: {
  close: () => void; limit: number; multiple: boolean; beginImport?: BeginSourceImport
  onImport: (items: ImmichImport[], isCurrent?: ImportIsCurrent) => Promise<void>
}) {
  const [albums, setAlbums] = useState<ImmichAlbum[]>([])
  const [items, setItems] = useState<ImmichAsset[]>([])
  const [selected, setSelected] = useState<ImmichAsset[]>([])
  const [name, setName] = useState('')
  const [album, setAlbum] = useState('')
  const [after, setAfter] = useState('')
  const [before, setBefore] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [preview, setPreview] = useState(false)
  const [next, setNext] = useState(false)
  const [loading, setLoading] = useState(true)
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const abort = new AbortController()
    void immichApi().json<ImmichAlbum[]>('/albums', { signal: abort.signal }).then(setAlbums).catch(() => {})
    return () => abort.abort()
  }, [])
  useEffect(() => {
    const abort = new AbortController()
    setLoading(true)
    setError('')
    void immichApi().json<{ items: ImmichAsset[]; next: string | null }>(
      `/assets?page=${page}&${query}`, { signal: abort.signal },
    ).then(result => {
      if (abort.signal.aborted) return
      setItems(result.items)
      setNext(Boolean(result.next))
    }).catch(error => {
      if (!abort.signal.aborted) setError(error.message)
    }).finally(() => { if (!abort.signal.aborted) setLoading(false) })
    return () => abort.abort()
  }, [page, query])

  async function importSelected() {
    setImporting(true)
    setError('')
    const api = immichApi()
    const isCurrent = beginImport?.()
    const ready: ImmichImport[] = []
    const errors: string[] = []
    let bytes = 0
    for (const asset of selected) {
      try {
        const response = await api.fetch(`/assets/${asset.id}/original`)
        const blob = await response.blob()
        if (multiple && blob.size > MAX_FILE_BYTES) throw new Error('单图超过 20 MB')
        if (multiple && bytes + blob.size > MAX_TOTAL_INPUT_BYTES) throw new Error('本次批量导入超过 128 MB')
        bytes += blob.size
        ready.push({
          id: asset.id,
          taken: response.headers.get('X-Immich-Taken-At') ?? undefined,
          file: new File([blob], asset.name, { type: blob.type }),
          token: response.headers.get('X-Immich-Source') ?? '',
          backend: api.backend,
        })
      } catch (error) {
        errors.push(`${asset.name}：${error instanceof Error ? error.message : String(error)}`)
      }
    }
    try {
      if (ready.length && (!isCurrent || isCurrent())) await onImport(ready, isCurrent)
      if (!errors.length) { close(); return }
      setSelected(selected.filter(item => !ready.some(entry => entry.id === item.id)))
      setError(errors.join('；'))
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error))
    } finally {
      setImporting(false)
    }
  }

  return (
    <ImmichDialog title="从 Immich 选择图片" close={close} busy={importing}>
      <form className="flex flex-wrap gap-2" onSubmit={event => {
        event.preventDefault()
        setPage(1)
        const params = new URLSearchParams({ name, album, after })
        if (!album) params.delete('album')
        if (before) params.set('before', before + 'T23:59:59.999Z')
        setQuery(params.toString())
      }}>
        <input aria-label="文件名" placeholder="文件名" value={name} onChange={event => setName(event.target.value)} className={fieldClass} />
        <select aria-label="相册" value={album} onChange={event => setAlbum(event.target.value)} className={fieldClass}>
          <option value="">所有相册</option>
          {albums.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
        <input type="date" aria-label="拍摄起始日期" value={after} onChange={event => setAfter(event.target.value)} className={fieldClass} />
        <input type="date" aria-label="拍摄截止日期" value={before} onChange={event => setBefore(event.target.value)} className={fieldClass} />
        <Button size="sm" type="submit" disabled={importing}>筛选</Button>
      </form>
      {loading ? <p className="my-4">正在加载图库…</p> : (
        <div className="my-4 grid grid-cols-2 sm:grid-cols-4 gap-2">
          {items.map(asset => {
            const checked = selected.some(item => item.id === asset.id)
            return <button key={asset.id} type="button" title={asset.name} aria-pressed={checked}
              disabled={importing || (!checked && multiple && selected.length >= limit)}
              className={`min-w-0 rounded border p-1 text-left ${checked ? 'border-border-focus' : 'border-border-subtle'}`}
              onClick={() => setSelected(checked ? selected.filter(item => item.id !== asset.id) : multiple ? [...selected, asset] : [asset])}>
              <Thumbnail asset={asset} />
              <span className="block truncate text-xs">{asset.name}</span>
              <span className="block text-xs text-fg-secondary">{asset.taken?.slice(0, 10)}</span>
            </button>
          })}
          {!items.length && !error && <p className="col-span-full">没有符合条件的图片</p>}
        </div>
      )}
      {preview && selected.length === 1 && <div className="mb-3"><Thumbnail key={selected[0].id} asset={selected[0]} preview /></div>}
      {error && <p role="alert" className="mb-3 text-sm">{error}</p>}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="secondary" disabled={loading || importing || page === 1} onClick={() => setPage(page - 1)}>上一页</Button>
        <Button size="sm" variant="secondary" disabled={loading || importing || !next} onClick={() => setPage(page + 1)}>下一页</Button>
        {selected.length === 1 && <Button size="sm" variant="secondary" disabled={importing} onClick={() => setPreview(!preview)}>
          {preview ? '关闭预览' : '预览选中图片'}
        </Button>}
        <Button size="sm" loading={importing} disabled={!selected.length || selected.length > limit} onClick={() => void importSelected()}>
          导入{selected.length ? ` ${selected.length} 张` : ''}
        </Button>
      </div>
    </ImmichDialog>
  )
}
