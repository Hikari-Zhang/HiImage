import { forwardRef, useEffect, useImperativeHandle, useState } from 'react'
import { dataUrlToBlob } from '../services/fileService'
import {
  immichApi, jsonRequest, operationId, useImmich,
  type ImmichAlbum, type ImmichConnection, type ImmichEditor, type ImmichSource,
} from '../services/immich'
import { useBackendStore } from '../stores/useBackendStore'
import Button from './ui/Button'
import ImmichDialog from './ImmichDialog'
import ImmichPicker, { type ImmichImport } from './ImmichPicker'

type Preview = { confirmation: string; albums: ImmichAlbum[]; taken: string; favorite: boolean }
type Outcome = { url: string; state: string; album_pending?: boolean; album_added?: boolean; migration_pending?: boolean; message?: string }

function SaveDialog({ result, original, source, connection, close }: {
  result: string; original: string | null; source?: ImmichSource; connection: ImmichConnection; close: () => void
}) {
  const [name, setName] = useState(source ? source.name.replace(/\.[^.]+$/, '') + '-HiImage' : 'HiImage')
  const [album, setAlbum] = useState('')
  const [albums, setAlbums] = useState<ImmichAlbum[]>([])
  const [preview, setPreview] = useState<Preview>()
  const [operation, setOperation] = useState(operationId)
  const [busy, setBusy] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [error, setError] = useState('')
  const [outcome, setOutcome] = useState<Outcome>()

  useEffect(() => {
    const abort = new AbortController()
    void immichApi().json<ImmichAlbum[]>('/albums', { signal: abort.signal }).then(setAlbums).catch(() => {})
    return () => abort.abort()
  }, [])

  async function preflight() {
    if (!source) return
    setBusy(true)
    setError('')
    try {
      setPreview(await immichApi().json<Preview>('/replace/preview', jsonRequest('POST', { source: source.token })))
      setOperation(operationId())
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }

  async function save(useDuplicate = false) {
    setBusy(true)
    setError('')
    setSubmitted(true)
    const api = immichApi()
    try {
      const body = new FormData()
      body.set('operation', operation)
      body.set('image', await dataUrlToBlob(result), name + '.png')
      if (preview) body.set('confirmation', preview.confirmation)
      else {
        body.set('generation', connection.generation)
        if (album) body.set('album', album)
        if (useDuplicate) body.set('use_duplicate', 'true')
        if (source?.taken) body.set('taken', source.taken)
      }
      setOutcome(await api.json<Outcome>(preview ? '/replace' : '/save', { method: 'POST', body }))
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }

  const pending = outcome?.album_pending || outcome?.migration_pending
  const attachDuplicate = outcome?.state === 'duplicate' && album && !outcome.album_added
  const src = (value: string) => value.startsWith('data:') ? value : 'data:image/png;base64,' + value
  return (
    <ImmichDialog title={preview ? '确认替换来源图片' : '保存到 Immich'} busy={busy} close={close}>
      <div className="space-y-3 text-sm">
        <p className="break-all">目标服务 {connection.url}</p>
        <div className="flex gap-3">
          {preview && original && <figure className="min-w-0 flex-1"><figcaption>原图 · {source?.name}</figcaption><img className="max-h-48 object-contain" src={src(original)} alt="Immich 原图" /></figure>}
          <figure className="min-w-0 flex-1"><figcaption>处理结果</figcaption><img className="max-h-48 object-contain" src={src(result)} alt="处理结果" /></figure>
        </div>
        <label className="block">文件名 <input className="bg-bg-tertiary p-2 rounded" disabled={submitted} value={name} maxLength={150} onChange={event => setName(event.target.value)} /></label>
        {preview ? <>
          <p>新图将获得独立的资产 ID。验证新图与迁移信息后，原图会移入 Immich 回收站。</p>
          <p>拍摄时间 {preview.taken} · {preview.favorite ? '已收藏' : '未收藏'}</p>
          <p>保留相册 {preview.albums.map(item => item.name).join('、') || '无'}</p>
          <p>人物识别、完整 EXIF、编辑历史和原图直达链接不会迁移。</p>
        </> : <>
          <label className="block">目标相册 <select className="bg-bg-tertiary p-2 rounded" disabled={submitted} value={album} onChange={event => setAlbum(event.target.value)}>
            <option value="">仅保存到图库</option>
            {albums.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select></label>
          <p>{source?.taken ? `沿用拍摄时间 ${source.taken}` : '使用保存时间'}</p>
        </>}
        {error && <p role="alert">{error}</p>}
        {outcome && <p role="status">
          {outcome.migration_pending ? `新图已上传，原图仍保留。待验证迁移：${outcome.message}`
            : outcome.album_pending ? '图片已上传，加入相册失败'
              : outcome.state === 'duplicate' ? (outcome.album_added ? '相同图片已加入相册' : '图库已存在相同内容')
                : outcome.state === 'replaced' ? '替换完成，可在 Immich 回收站恢复原图' : '图片已保存'}
          {' '}<a href={outcome.url} target="_blank" rel="noreferrer" className="underline">在 Immich 中查看</a>
        </p>}
        {attachDuplicate && !outcome?.album_pending && <Button size="sm" variant="secondary" loading={busy} onClick={() => void save(true)}>将已有图片加入相册</Button>}
        {(!outcome || pending) && <div className="flex flex-wrap gap-2">
          <Button size="sm" variant={preview ? 'danger' : 'primary'} loading={busy} disabled={!name.trim()} onClick={() => void save(Boolean(attachDuplicate && outcome?.album_pending))}>
            {outcome?.migration_pending ? '继续验证迁移' : outcome?.album_pending ? '重试加入相册'
              : preview ? '上传结果并移原图到回收站' : '保存为新图片'}
          </Button>
          {!submitted && !preview && source?.token && connection.mode === 'replace' &&
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => void preflight()}>替换来源图片…</Button>}
          {!submitted && preview && <Button size="sm" variant="ghost" disabled={busy} onClick={() => setPreview(undefined)}>返回另存</Button>}
        </div>}
      </div>
    </ImmichDialog>
  )
}

export type ImmichActionsHandle = { chooseSource: () => void }

type ImmichActionsProps = {
  editor: ImmichEditor; revision: number; original: string | null; result: string | null
  onImport: (items: ImmichImport[]) => Promise<void>; multiple?: boolean; limit?: number; inputLabel?: string
  onOpenLocal?: () => void
}

export default forwardRef<ImmichActionsHandle, ImmichActionsProps>(function ImmichActions({
  editor, revision, original, result, onImport, multiple = false, limit = 1,
  inputLabel = '从 Immich 选择', onOpenLocal,
}, ref) {
  const backend = useBackendStore(state => state.backendURL)
  const access = useImmich(state => state.accessBackend === backend ? state.access : '')
  const binding = useImmich(state => state.sources[editor])
  const source = binding?.revision === revision && binding.backend === backend ? binding : undefined
  const [view, setView] = useState<'source' | 'pick' | 'save'>()
  const [connection, setConnection] = useState<ImmichConnection>()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useImperativeHandle(ref, () => ({
    chooseSource: () => {
      setError('')
      setView('source')
    },
  }), [])

  async function open(next: 'pick' | 'save') {
    setLoading(true)
    setError('')
    try {
      const current = await immichApi().json<ImmichConnection>('/connection')
      if (!current.configured) throw new Error('请先在设置中配置 Immich')
      if (next === 'save' && current.mode === 'read') throw new Error('此 Immich 连接为只读')
      setConnection(current)
      setView(next)
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-1">
      {!multiple && source && <span className="max-w-28 truncate text-xs" title={source.name}>Immich · {source.name}</span>}
      {access
        ? <Button size="sm" variant="ghost" disabled={loading || limit <= 0} onClick={() => void open('pick')}>{inputLabel}</Button>
        : <a href="#/settings?tab=immich" className="text-xs text-fg-secondary">配置或解锁 Immich</a>}
      {access && result && <Button size="sm" variant="ghost" disabled={loading} onClick={() => void open('save')}>保存到 Immich</Button>}
      {view !== 'source' && error && <span role="alert" className="text-xs">{error} <a className="underline" href="#/settings?tab=immich">设置</a></span>}
      {view === 'source' && onOpenLocal && (
        <ImmichDialog title="选择图片来源" close={() => setView(undefined)} busy={loading}>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" disabled={loading} onClick={() => {
              setView(undefined)
              onOpenLocal()
            }}>从本机选择</Button>
            {access
              ? <Button disabled={limit <= 0} loading={loading} onClick={() => void open('pick')}>{inputLabel}</Button>
              : <a href="#/settings?tab=immich" className="self-center underline" onClick={() => setView(undefined)}>配置或解锁 Immich</a>}
          </div>
          {error && <p role="alert" className="mt-3 text-sm">{error} <a className="underline" href="#/settings?tab=immich" onClick={() => setView(undefined)}>前往设置</a></p>}
        </ImmichDialog>
      )}
      {view === 'pick'  && <ImmichPicker close={() => setView(undefined)} multiple={multiple} limit={limit} onImport={onImport} />}
      {view === 'save' && result && connection && <SaveDialog key={revision} result={result} original={original} source={source} connection={connection} close={() => setView(undefined)} />}
    </div>
  )
})
