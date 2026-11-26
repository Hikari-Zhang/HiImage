import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Download, Eraser, Hand, ImagePlus, MousePointer2, Move, Paintbrush, Plus, Redo2, Square, Trash2, Undo2, X } from 'lucide-react'
import MaskCanvas from '../components/MaskCanvas'
import PageHeader from '../components/layout/PageHeader'
import { Button, Select, Slider, showToast } from '../components/ui'
import { downloadBlob } from '../services/fileService'
import {
  defaultRedactionStyle, encodeRedactions, loadRedactionImage, redactionImageTypes,
  regionBounds, renderRedactions, resizeRedactionRegion,
} from '../services/imageRedaction'
import type { RedactionBox, RedactionFormat, RedactionMode, RedactionRegion, RedactionStyle } from '../services/imageRedaction'
import type { MaskStroke } from '../types/mask'

type Tool = 'rectangle' | 'brush' | 'eraser' | 'select' | 'pan' | 'asset'
type LocalImage = Awaited<ReturnType<typeof loadRedactionImage>> & { name: string }
type Asset = LocalImage & { id: string }
type History = { past: RedactionRegion[][]; current: RedactionRegion[]; future: RedactionRegion[][]; group?: string }
type ExportResult = { url: string; blob: Blob; format: RedactionFormat; metadataOnly: boolean }

const emptyHistory: History = { past: [], current: [], future: [] }
const modeOptions = [
  { value: 'mosaic', label: '普通马赛克' },
  { value: 'tile', label: '图片马赛克（平铺）' },
  { value: 'picture', label: '单图覆盖' },
  { value: 'solid', label: '纯色遮挡' },
]
const tools = [
  { value: 'rectangle', label: '框选', icon: Square },
  { value: 'brush', label: '画笔', icon: Paintbrush },
  { value: 'eraser', label: '橡皮擦', icon: Eraser },
  { value: 'select', label: '调整选区', icon: MousePointer2 },
  { value: 'pan', label: '平移画布', icon: Hand },
] as const

export default function PrivacyRedactionPage() {
  const [source, setSource] = useState<LocalImage | null>(null)
  const [assets, setAssets] = useState<Asset[]>([])
  const [history, setHistory] = useState(emptyHistory)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [nextStyle, setNextStyle] = useState(defaultRedactionStyle)
  const [tool, setTool] = useState<Tool>('rectangle')
  const [brushSize, setBrushSize] = useState(32)
  const [loading, setLoading] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)
  const [format, setFormat] = useState<RedactionFormat>('image/png')
  const [metadataOnly, setMetadataOnly] = useState(false)
  const [exportResult, setExportResult] = useState<ExportResult | null>(null)
  const [exportError, setExportError] = useState('')
  const sourceInput = useRef<HTMLInputElement>(null)
  const assetInput = useRef<HTMLInputElement>(null)
  const dialog = useRef<HTMLDialogElement>(null)
  const urls = useRef(new Set<string>())
  const mounted = useRef(false)
  const importing = useRef(false)
  const regionCounter = useRef(0)
  const assetCounter = useRef(0)

  const regions = history.current
  const selected = regions.find(region => region.id === selectedId)
  const style = selected?.style ?? nextStyle
  const usesAsset = style.mode === 'tile' || style.mode === 'picture'
  const activeTool = ((!selected && tool === 'eraser') || (tool === 'asset' && (!selected || !usesAsset)))
    ? 'select' : tool
  const image = source?.image
  const assetImages = useMemo(() => new Map(assets.map(asset => [asset.id, asset.image])), [assets])
  const rois = useMemo(() => image ? regions.flatMap(region => {
    const box = regionBounds(region, image.naturalWidth, image.naturalHeight)
    return box ? [{ id: region.id, ...box }] : []
  }) : [], [image, regions])
  const preview = useMemo(() => {
    if (!image || !regions.length) return { canvas: null, error: '' }
    try {
      return { canvas: renderRedactions(image, regions, assetImages, 1280).canvas, error: '' }
    } catch (error) {
      return { canvas: null, error: error instanceof Error ? error.message : '打码预览失败。' }
    }
  }, [image, regions, assetImages])

  useEffect(() => {
    mounted.current = true
    const owned = urls.current
    return () => {
      mounted.current = false
      owned.forEach(url => URL.revokeObjectURL(url))
      owned.clear()
    }
  }, [])

  const undo = useCallback(() => setHistory(state => {
    const previous = state.past[state.past.length - 1]
    return previous ? { past: state.past.slice(0, -1), current: previous, future: [state.current, ...state.future] } : state
  }), [])
  const redo = useCallback(() => setHistory(state => {
    const [next, ...future] = state.future
    return next ? { past: [...state.past, state.current], current: next, future } : state
  }), [])

  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      if (exportOpen || (event.target instanceof HTMLElement &&
        event.target.closest('input, textarea, select, [contenteditable]'))) return
      if (event.key === 'Escape') setSelectedId(null)
      if (!(event.ctrlKey || event.metaKey) || !['z', 'y'].includes(event.key.toLowerCase())) return
      event.preventDefault()
      if (event.shiftKey || event.key.toLowerCase() === 'y') redo()
      else undo()
    }
    window.addEventListener('keydown', keydown)
    return () => window.removeEventListener('keydown', keydown)
  }, [exportOpen, redo, undo])

  useEffect(() => {
    if (!exportOpen || !source) return
    let active = true
    let previewUrl: string | undefined
    setExportResult(null)
    setExportError('')
    const frame = requestAnimationFrame(() => {
      void (async () => {
        try {
          if (!regions.length && !metadataOnly) return
          const { canvas, hasRedactions } = renderRedactions(
            source.image, metadataOnly ? [] : regions, assetImages, Infinity,
            format === 'image/jpeg' ? '#ffffff' : undefined,
          )
          if (!metadataOnly && !hasRedactions) throw new Error('选区已擦除，请添加打码区域后再导出。')
          const blob = await encodeRedactions(canvas, format)
          if (!active) return
          previewUrl = URL.createObjectURL(blob)
          setExportResult({ url: previewUrl, blob, format, metadataOnly })
        } catch (error) {
          if (active) setExportError(error instanceof Error ? error.message : '图片导出失败。')
        }
      })()
    })
    return () => {
      active = false
      cancelAnimationFrame(frame)
      if (previewUrl) URL.revokeObjectURL(previewUrl)
    }
  }, [assetImages, exportOpen, format, metadataOnly, regions, source])

  function update(transform: (current: RedactionRegion[]) => RedactionRegion[], group?: string) {
    setHistory(state => ({
      current: transform(state.current), future: [], group,
      past: group && state.group === group ? state.past : [...state.past, state.current].slice(-30),
    }))
  }

  function finishAdjustment() {
    setHistory(state => state.group ? { ...state, group: undefined } : state)
  }

  function changeStyle(patch: Partial<RedactionStyle>, group?: string) {
    if (!selected) setNextStyle(value => ({ ...value, ...patch }))
    else update(current => current.map(region => region.id === selected.id
      ? { ...region, style: { ...region.style, ...patch } } : region), group && selected.id + group)
  }

  async function importFile(file: File | undefined, kind: 'source' | 'asset') {
    if (!file || importing.current) return
    const regionId = selected?.id
    importing.current = true
    setLoading(true)
    try {
      const local = await loadRedactionImage(file)
      if (!mounted.current) {
        URL.revokeObjectURL(local.url)
        return
      }
      urls.current.add(local.url)
      const item = { ...local, name: file.name }
      if (kind === 'source') {
        if (source) {
          URL.revokeObjectURL(source.url)
          urls.current.delete(source.url)
        }
        setSource(item)
        setHistory(emptyHistory)
        setSelectedId(null)
        setTool('rectangle')
        regionCounter.current = 0
      } else {
        const id = `asset-${++assetCounter.current}`
        setAssets(list => [...list, { ...item, id }])
        if (regionId) update(current => current.map(region => region.id === regionId
          ? { ...region, style: { ...region.style, asset: id } } : region))
        else setNextStyle(value => ({ ...value, asset: id }))
      }
    } catch (error) {
      if (mounted.current) showToast('error', error instanceof Error ? error.message : '图片读取失败。')
    } finally {
      importing.current = false
      if (mounted.current) setLoading(false)
    }
  }

  function addRegion(box?: RedactionBox, strokes: MaskStroke[] = []) {
    const number = ++regionCounter.current
    const id = `region-${number}`
    const region = { id, label: `选区 ${number}`, box, strokes, style: { ...nextStyle } }
    update(current => [...current, region])
    setSelectedId(id)
  }

  function addStroke(stroke: MaskStroke) {
    if (!selected) {
      if (stroke.tool !== 'eraser') addRegion(undefined, [stroke])
      return
    }
    update(current => current.map(region => region.id === selected.id
      ? { ...region, strokes: [...region.strokes, stroke] } : region))
  }

  function adjustRegion(box: RedactionBox & { id: string }) {
    const old = rois.find(roi => roi.id === box.id)
    if (old) update(current => current.map(region => region.id === box.id
      ? resizeRedactionRegion(region, old, box) : region))
  }

  function moveAsset(id: string, dx: number, dy: number) {
    if (!dx && !dy) return
    update(current => current.map(region => region.id === id ? {
      ...region, style: {
        ...region.style, offsetX: region.style.offsetX + dx, offsetY: region.style.offsetY + dy,
      },
    } : region))
  }

  function openExport() {
    setMetadataOnly(false)
    setExportResult(null)
    setExportError('')
    setExportOpen(true)
    dialog.current?.showModal()
  }

  async function download() {
    if (!exportResult) return
    const name = source?.name.replace(/\.[^.]+$/, '') || 'image'
    try {
      await downloadBlob(exportResult.blob, `${name}-${metadataOnly ? 'clean' : 'redacted'}.${format === 'image/png' ? 'png' : 'jpg'}`)
      dialog.current?.close()
    } catch {
      showToast('error', '下载失败，请重试。')
    }
  }

  return (
    <div className="flex h-full flex-col">
      <PageHeader title="隐私打码" right={
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="secondary" icon={<ImagePlus size={16} />} disabled={loading}
            onClick={() => sourceInput.current?.click()}>{source ? '更换图片' : '打开图片'}</Button>
          <Button size="sm" variant="ghost" icon={<Undo2 size={16} />} disabled={!history.past.length} onClick={undo}>撤销</Button>
          <Button size="sm" variant="ghost" icon={<Redo2 size={16} />} disabled={!history.future.length} onClick={redo}>重做</Button>
          <Button size="sm" icon={<Download size={16} />} disabled={!source || loading} onClick={openExport}>导出</Button>
        </div>
      } />
      <input ref={sourceInput} type="file" accept={redactionImageTypes} aria-label="打码原图文件" className="hidden"
        onChange={event => { void importFile(event.target.files?.[0], 'source'); event.target.value = '' }} />
      <input ref={assetInput} type="file" accept={redactionImageTypes} aria-label="打码素材文件" className="hidden"
        onChange={event => { void importFile(event.target.files?.[0], 'asset'); event.target.value = '' }} />

      <div className="flex min-h-0 flex-1 flex-col overflow-auto lg:flex-row">
        <div className="flex min-h-[440px] min-w-0 flex-1 flex-col bg-workspace">
          {source && <div className="flex flex-wrap items-center gap-1 border-b border-border-subtle bg-bg-secondary px-3 py-2">
            {tools.map(({ value, label, icon: Icon }) => (
              <Button key={value} size="sm" icon={<Icon size={16} />} aria-pressed={activeTool === value}
                variant={activeTool === value ? 'secondary' : 'ghost'} disabled={value === 'eraser' && !selected}
                onClick={() => setTool(value)}>{label}</Button>
            ))}
          </div>}
          {preview.error && <p role="alert" className="px-3 py-2 text-sm text-status-error">{preview.error}</p>}
          <div className="relative min-h-0 flex-1" aria-label="打码画布"
            onDragOver={event => event.preventDefault()}
            onDrop={event => { event.preventDefault(); void importFile(event.dataTransfer.files[0], 'source') }}>
            {source && !preview.error ? (
              <MaskCanvas key={source.url} imageSrc={source.url} renderedImage={preview.canvas} showMaskPreview={false}
                strokes={[]} rois={rois} selectedROIs={selected ? [selected.id] : []}
                canvasTool={activeTool === 'rectangle' ? 'rectangle' : 'brush'} panMode={activeTool === 'pan'}
                brushSettings={{ size: brushSize, hardness: 100, shape: 'circle', tool: activeTool === 'eraser' ? 'eraser' : 'brush' }}
                onROIDrawn={box => addRegion(box)} onROISelect={setSelectedId} onStrokeComplete={addStroke} onUndo={undo}
                regionControls={{
                  mode: activeTool === 'asset' ? 'asset' : activeTool === 'select' ? 'select' : 'draw',
                  keepRatio: !!selected?.strokes.length, onChange: adjustRegion, onMoveAsset: moveAsset,
                  onDeselect: () => setSelectedId(null),
                }} />
            ) : !source ? (
              <button type="button" onClick={() => sourceInput.current?.click()} disabled={loading}
                className="absolute inset-4 flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border-subtle text-fg-secondary hover:bg-bg-hover">
                <ImagePlus size={32} strokeWidth={1.5} />
                <span className="text-sm">{loading ? '正在读取图片…' : '拖入图片或点击打开'}</span>
              </button>
            ) : null}
          </div>
        </div>

        {source && <aside className="flex w-full shrink-0 flex-col gap-4 overflow-auto border-t border-border-subtle bg-bg-secondary p-4 lg:w-[318px] lg:border-l lg:border-t-0">
          <section className="flex flex-col gap-3">
            <h2 className="text-sm font-medium text-fg-primary">{selected?.label ?? '新选区样式'}</h2>
            <Select label="打码样式" options={modeOptions} value={style.mode}
              onChange={event => changeStyle({ mode: event.target.value as RedactionMode })} />
            {(style.mode === 'mosaic' || style.mode === 'tile') && (
              <Slider label={style.mode === 'mosaic' ? '方块大小' : '每块大小'} unit=" px"
                min={4} max={Math.max(120, Math.round(Math.min(source.image.naturalWidth, source.image.naturalHeight) / 4))}
                value={style.block} onChange={event => changeStyle({ block: Number(event.target.value) }, 'block')}
                onPointerUp={finishAdjustment} onKeyUp={finishAdjustment} onBlur={finishAdjustment} />
            )}
            {style.mode === 'mosaic' && <p className="text-xs text-fg-secondary">普通马赛克适合弱化细节。需要彻底盖住内容时，可用纯色或图片覆盖。</p>}
            {usesAsset && (
              <>
                <div className="flex flex-wrap gap-2" aria-label="打码素材">
                  <Button size="sm" variant="secondary" icon={<Plus size={14} />} disabled={loading}
                    onClick={() => assetInput.current?.click()}>添加图片</Button>
                  {assets.map(asset => (
                    <button key={asset.id} type="button" aria-label={`使用素材 ${asset.name}`} aria-pressed={style.asset === asset.id}
                      title={asset.name} onClick={() => changeStyle({ asset: asset.id, offsetX: 0, offsetY: 0 })}
                      className={`h-9 w-9 overflow-hidden rounded border ${style.asset === asset.id ? 'border-border-focus ring-1 ring-border-focus' : 'border-border-subtle'}`}>
                      <img src={asset.url} alt="" className="h-full w-full object-contain" />
                    </button>
                  ))}
                </div>
                {!style.asset && <p className="text-xs text-fg-secondary">添加素材后会显示图片效果，当前选区由底色覆盖。</p>}
                {style.mode === 'picture' && (
                  <>
                    <Select label="图片适配" value={style.fit} options={[{ value: 'cover', label: '填满' }, { value: 'contain', label: '完整显示' }]}
                      onChange={event => changeStyle({ fit: event.target.value === 'contain' ? 'contain' : 'cover' })} />
                    <Slider label="素材缩放" unit="%" min={25} max={300} value={style.zoom}
                      onChange={event => changeStyle({ zoom: Number(event.target.value) }, 'zoom')}
                      onPointerUp={finishAdjustment} onKeyUp={finishAdjustment} onBlur={finishAdjustment} />
                  </>
                )}
                {selected && style.asset && (
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant={activeTool === 'asset' ? 'secondary' : 'ghost'} icon={<Move size={14} />}
                      aria-pressed={activeTool === 'asset'} onClick={() => setTool(activeTool === 'asset' ? 'select' : 'asset')}>
                      {style.mode === 'tile' ? '移动平铺起点' : '移动素材'}
                    </Button>
                    {(style.offsetX !== 0 || style.offsetY !== 0) && (
                      <Button size="sm" variant="ghost" onClick={() => changeStyle({ offsetX: 0, offsetY: 0 })}>重置位置</Button>
                    )}
                  </div>
                )}
              </>
            )}
            {style.mode !== 'mosaic' && (
              <label className="flex items-center justify-between text-xs text-fg-secondary">
                {style.mode === 'solid' ? '遮挡颜色' : '不透明底色'}
                <input type="color" value={style.color}
                  onChange={event => changeStyle({ color: event.target.value }, 'color')}
                  onBlur={finishAdjustment} className="h-7 w-10 cursor-pointer rounded border border-border-subtle bg-bg-primary" />
              </label>
            )}
            {(activeTool === 'brush' || activeTool === 'eraser') && (
              <Slider label="笔尖大小" unit=" px" min={2} max={300} value={brushSize}
                onChange={event => setBrushSize(Number(event.target.value))} />
            )}
          </section>

          <section className="flex flex-col gap-2 border-t border-border-subtle pt-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-medium text-fg-primary">选区</h2>
              <Button size="sm" variant="ghost" icon={<Plus size={14} />}
                onClick={() => { setSelectedId(null); if (tool !== 'brush') setTool('rectangle') }}>新建区域</Button>
            </div>
            {regions.length === 0 && <p className="text-xs text-fg-secondary">在画布上框选或涂抹需要打码的区域。</p>}
            <ul className="flex flex-col gap-1" aria-label="打码选区列表">
              {regions.map(region => (
                <li key={region.id} className={`flex items-center gap-1 rounded-lg border px-2 ${region.id === selected?.id ? 'border-border-focus' : 'border-border-subtle'}`}>
                  <button type="button" aria-pressed={region.id === selected?.id}
                    onClick={() => { setSelectedId(region.id); setTool('select') }}
                    className="min-w-0 flex-1 py-2 text-left text-sm text-fg-primary">
                    {region.label}<span className="ml-2 text-xs text-fg-secondary">{modeOptions.find(option => option.value === region.style.mode)?.label}</span>
                  </button>
                  <Button size="sm" variant="ghost" aria-label={`删除${region.label}`} icon={<Trash2 size={14} />}
                    onClick={() => { update(current => current.filter(item => item.id !== region.id)); if (selected?.id === region.id) setSelectedId(null) }} />
                </li>
              ))}
            </ul>
          </section>
        </aside>}
      </div>

      <dialog ref={dialog} aria-labelledby="redaction-export-title"
        onClose={() => { setExportOpen(false); setExportResult(null) }}
        className="m-auto w-[min(92vw,720px)] rounded-xl border border-border-subtle bg-bg-secondary p-4 text-fg-primary backdrop:bg-black/60">
        <div className="flex items-center justify-between gap-2">
          <h2 id="redaction-export-title" className="text-base font-medium">导出预览</h2>
          <Button size="sm" variant="ghost" aria-label="关闭预览" icon={<X size={16} />} onClick={() => dialog.current?.close()} />
        </div>
        <div className="mt-3 flex min-h-36 items-center justify-center overflow-hidden rounded-lg bg-workspace p-3">
          {exportError ? <p role="alert" className="text-sm text-status-error">{exportError}</p>
            : exportResult?.format === format && exportResult.metadataOnly === metadataOnly
              ? <img src={exportResult.url} alt="最终导出预览" className="max-h-[52vh] max-w-full object-contain" />
              : <p role="status" className="text-sm text-fg-secondary">{regions.length || metadataOnly ? '正在准备图片…' : '添加打码区域，或勾选仅清除元数据。'}</p>}
        </div>
        <div className="mt-3 flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-wrap items-end gap-3">
            <Select label="导出格式" value={format} options={[{ value: 'image/png', label: 'PNG' }, { value: 'image/jpeg', label: 'JPEG' }]}
              onChange={event => setFormat(event.target.value === 'image/jpeg' ? 'image/jpeg' : 'image/png')} />
            {!regions.length && (
              <label className="flex min-h-9 items-center gap-2 text-sm">
                <input type="checkbox" checked={metadataOnly} onChange={event => setMetadataOnly(event.target.checked)} />仅清除元数据
              </label>
            )}
          </div>
          <Button icon={<Download size={16} />} onClick={() => void download()}
            disabled={exportResult?.format !== format || exportResult.metadataOnly !== metadataOnly}>下载图片</Button>
        </div>
        <p className="mt-2 text-xs text-fg-secondary">导出会移除原图的定位、拍摄信息和嵌入缩略图。{format === 'image/jpeg' ? ' JPEG 的透明区域使用白底。' : ''}</p>
      </dialog>
    </div>
  )
}
