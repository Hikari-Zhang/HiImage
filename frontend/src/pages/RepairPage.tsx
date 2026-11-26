import { useEffect, useMemo, useRef, useState } from 'react'
import { Bandage, Download, Eraser, Hand, ImagePlus, Paintbrush, RotateCcw, Square, Trash2, Undo2 } from 'lucide-react'
import { create } from 'zustand'
import ImageHistory from '../components/ImageHistory'
import MaskCanvas from '../components/MaskCanvas'
import PageHeader from '../components/layout/PageHeader'
import { Button, Progress, Select, Slider, showToast } from '../components/ui'
import { useBackendAPI } from '../hooks/useBackendAPI'
import { useDeviceOptions } from '../hooks/useDeviceOptions'
import { downloadDataUrl, fileToDataUrl } from '../services/fileService'
import { recordImageResult } from '../services/imageHistory'
import { exportCombinedMask, type MaskOperation } from '../services/maskExport'
import { createTaskId } from '../services/taskId'
import { createTaskProgressSession } from '../services/taskProgress'
import { isCurrentTask, useProcessStore, useSessionProcess } from '../stores/useProcessStore'
import type { MaskStroke, ROI } from '../types/mask'

type Result = { image: string; taskId: string }
type View = 'before' | 'after' | 'original'
type Selection = { kind: 'stroke'; stroke: MaskStroke } | { kind: 'rectangle'; roi: Omit<ROI, 'id'> }

const repairModels = {
  wm_lama: { value: 'wm_lama', label: 'LaMa', device: 'cpu' },
  flux_fill: { value: 'flux_fill', label: 'FLUX Fill', device: 'cuda' },
} as const
type RepairModel = keyof typeof repairModels

const useRepair = create<{
  file: File | null
  model: RepairModel
  prompt: string
  revision: number
  selection: Selection[]
  result: Result | null
  applied: Result | null
  previous: { applied: Result | null } | null
  view: View
}>(() => ({
  file: null, model: 'wm_lama', prompt: '', revision: 0, selection: [], result: null,
  applied: null, previous: null, view: 'before',
}))

const tools = [
  { id: 'brush', label: '画笔', icon: Paintbrush },
  { id: 'rectangle', label: '矩形', icon: Square },
  { id: 'eraser', label: '橡皮擦', icon: Eraser },
  { id: 'pan', label: '平移画布', icon: Hand },
] as const
const fileTypes = ['image/png', 'image/jpeg', 'image/webp']
const png = (image: string) => `data:image/png;base64,${image}`
const taskRunning = () => Boolean(useProcessStore.getState().sessions.repair?.isProcessing)

export default function RepairPage() {
  const { file, model, prompt, revision, selection, result, applied, previous, view } = useRepair()
  const { isProcessing, progress, statusMessage, startProcess, finishProcess, setError, reset } = useSessionProcess('repair')
  const { inpaintWithMask } = useBackendAPI()
  const { loading: detectingDevices, cudaAvailable } = useDeviceOptions({ device: repairModels[model].device })
  const input = useRef<HTMLInputElement>(null)
  const [source, setSource] = useState<{ file: File; url: string } | null>(null)
  const [tool, setTool] = useState<(typeof tools)[number]['id']>('brush')
  const [brushSize, setBrushSize] = useState(24)
  const [dilation, setDilation] = useState(2)
  const [dimensions, setDimensions] = useState<{ width: number; height: number } | null>(null)
  const [imageError, setImageError] = useState('')
  const [hasMask, setHasMask] = useState(false)
  const original = source?.file === file ? source?.url : null
  const base = original && applied ? png(applied.image) : original
  const override = view === 'after' && result ? png(result.image) : view === 'original' && applied ? original : null
  const [preview, setPreview] = useState<{ url: string; image: HTMLImageElement } | null>(null)
  const editing = !result && view !== 'original'
  const finalImage = result ?? applied
  const isRedraw = model === 'flux_fill'
  const modelReady = !isRedraw || cudaAvailable === true
  let deviceMessage = ''
  if (isRedraw) {
    if (detectingDevices) deviceMessage = '正在检测 CUDA 设备…'
    else if (cudaAvailable === false) deviceMessage = 'FLUX Fill 需要 CUDA。当前可使用 LaMa 修复。'
    else if (cudaAvailable === null) deviceMessage = '无法确认 CUDA 设备状态，请刷新页面后重试。'
  }

  const mask = useMemo(() => {
    const strokes: MaskStroke[] = []
    const rois: Array<ROI & { id: string }> = []
    const operationOrder: MaskOperation[] = []
    selection.forEach((entry, index) => {
      if (entry.kind === 'stroke') {
        operationOrder.push({ kind: 'stroke', index: strokes.length })
        strokes.push(entry.stroke)
      } else {
        operationOrder.push({ kind: 'rectangle', index: rois.length })
        rois.push({ ...entry.roi, id: `repair-region-${index}` })
      }
    })
    return { strokes, rois, operationOrder }
  }, [selection])

  useEffect(() => {
    if (!file) return
    const url = URL.createObjectURL(file)
    setSource({ file, url })
    return () => URL.revokeObjectURL(url)
  }, [file])

  useEffect(() => {
    if (!override) return
    const image = new Image()
    image.onload = () => setPreview({ url: override, image })
    image.onerror = () => showToast('error', '无法预览图片')
    image.src = override
    return () => { image.onload = null; image.onerror = null }
  }, [override])

  function importFile(next: File | undefined) {
    if (!next || taskRunning()) return
    if (!fileTypes.includes(next.type)) {
      showToast('error', '请选择 PNG、JPEG 或 WebP 图片。')
      return
    }
    reset()
    setDimensions(null)
    setImageError('')
    setHasMask(false)
    setTool('brush')
    useRepair.setState(state => ({
      file: next, revision: state.revision + 1, selection: [], result: null, applied: null, previous: null, view: 'before',
    }))
  }

  function updateSelection(update: (current: Selection[]) => Selection[]) {
    if (!editing || taskRunning()) return
    setHasMask(false)
    useRepair.setState(state => ({ selection: update(state.selection) }))
  }

  async function repair() {
    if (!file || !editing || !dimensions || !hasMask || !modelReady || taskRunning()) return
    const { label, device } = repairModels[model]
    const redrawPrompt = isRedraw ? prompt.trim() : ''
    const taskId = createTaskId()
    startProcess(label, taskId)
    try {
      const [image, selectionMask] = await Promise.all([
        applied ? png(applied.image) : fileToDataUrl(file),
        exportCombinedMask(mask.strokes, mask.rois, dimensions.width, dimensions.height, 1, {
          operationOrder: mask.operationOrder, requireNonempty: true,
        }),
      ])
      const session = createTaskProgressSession(taskId, { isCurrent: () => isCurrentTask(taskId) })
      const response = await session.run(id => inpaintWithMask({
        image, mask: selectionMask, model, device, dilation, disable_nsfw: false,
        redraw: redrawPrompt ? { prompt: redrawPrompt } : undefined,
        preserve_alpha: true, task_id: id,
      }))
      if (!isCurrentTask(taskId)) return
      useRepair.setState({ result: { image: response.image, taskId }, view: 'after' })
      finishProcess(taskId, '修复完成')
    } catch (error) {
      if (isCurrentTask(taskId)) setError(taskId, error instanceof Error ? error.message : '选区修复失败')
    }
  }

  function applyResult() {
    if (!result || taskRunning()) return
    setHasMask(false)
    useRepair.setState({ applied: result, previous: { applied }, result: null, selection: [], view: 'before' })
    recordImageResult(result.taskId, 'repair', result.image, file?.name)
  }

  async function download() {
    if (!finalImage) return
    const name = file?.name.replace(/\.[^.]+$/, '') || 'image'
    try {
      await downloadDataUrl(png(finalImage.image), `${name}-repaired.png`)
      recordImageResult(finalImage.taskId, 'repair', finalImage.image, file?.name)
    } catch {
      showToast('error', '下载失败，请重试。')
    }
  }

  return (
    <div className="flex h-full flex-col">
      <PageHeader title="瑕疵修复" right={
        <div className="flex flex-wrap items-center gap-2">
          <ImageHistory disabled={isProcessing} onUse={importFile} />
          <Button size="sm" variant="secondary" icon={<ImagePlus size={16} />} disabled={isProcessing}
            onClick={() => input.current?.click()}>{file ? '更换图片' : '打开图片'}</Button>
          {finalImage && <Button size="sm" variant="secondary" icon={<Download size={16} />}
            disabled={isProcessing} onClick={() => void download()}>下载结果</Button>}
        </div>
      } />
      <input ref={input} type="file" accept={fileTypes.join(',')} aria-label="修复原图文件" className="hidden"
        onChange={event => { importFile(event.target.files?.[0]); event.target.value = '' }} />
      <div className="flex min-h-0 flex-1 flex-col overflow-auto lg:flex-row">
        <div className="flex min-h-[390px] min-w-0 flex-1 flex-col bg-workspace">
          {base && dimensions && <div className="flex flex-wrap gap-1 border-b border-border-subtle bg-bg-secondary px-3 py-2">
            {editing && <>
              {tools.map(({ id, label, icon: Icon }) => (
                <Button key={id} size="sm" variant={tool === id ? 'secondary' : 'ghost'} icon={<Icon size={16} />}
                  aria-pressed={tool === id} disabled={isProcessing || (id === 'eraser' && !selection.length)}
                  onClick={() => setTool(id)}>{label}</Button>
              ))}
              <Button size="sm" variant="ghost" icon={<Undo2 size={16} />} disabled={isProcessing || !selection.length}
                onClick={() => updateSelection(items => items.slice(0, -1))}>撤销</Button>
              <Button size="sm" variant="ghost" icon={<Trash2 size={16} />} disabled={isProcessing || !selection.length}
                onClick={() => updateSelection(() => [])}>清空</Button>
            </>}
            {result && (['before', 'after'] as const).map(next => (
              <Button key={next} size="sm" variant={view === next ? 'secondary' : 'ghost'}
                aria-pressed={view === next} onClick={() => useRepair.setState({ view: next })}>
                {next === 'before' ? '修复前' : '修复后'}
              </Button>
            ))}
            {applied && <Button size="sm" variant="ghost" disabled={isProcessing}
              aria-pressed={view === 'original'} onClick={() => useRepair.setState({
                view: view === 'original' ? (result ? 'after' : 'before') : 'original',
              })}>查看最初原图</Button>}
          </div>}
          <div className="relative min-h-0 flex-1" aria-label="修复画布"
            onDragOver={event => event.preventDefault()}
            onDrop={event => { event.preventDefault(); importFile(event.dataTransfer.files[0]) }}>
            {base ? <MaskCanvas key={revision} imageSrc={base} renderedImage={preview?.url === override ? preview?.image : undefined}
              keepViewOnSameSize readOnly={!editing || isProcessing} showMaskPreview={editing} maskPreviewMaxEdge={1024}
              maskExpansion={dilation} maskOperationOrder={mask.operationOrder} onMaskPresence={setHasMask}
              strokes={mask.strokes} rois={editing ? mask.rois : undefined}
              canvasTool={tool === 'rectangle' ? 'rectangle' : 'brush'} panMode={tool === 'pan' || !editing}
              brushSettings={{ size: brushSize, hardness: 100, shape: 'circle', tool: tool === 'eraser' ? 'eraser' : 'brush' }}
              onImageLoad={(width, height) => setDimensions({ width, height })}
              onImageError={() => { setDimensions(null); setImageError('无法读取这张图片，请重新打开。') }}
              onROIDrawn={roi => updateSelection(items => [...items, { kind: 'rectangle', roi }])}
              onStrokeComplete={stroke => updateSelection(items => [...items, { kind: 'stroke', stroke }])}
              onUndo={() => { if (selection.length) updateSelection(items => items.slice(0, -1)) }} />
              : <button type="button" onClick={() => input.current?.click()}
                className="absolute inset-4 flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border-subtle text-fg-secondary hover:bg-bg-hover">
                <ImagePlus size={32} strokeWidth={1.5} />
                <span className="text-sm">拖入图片或点击打开</span>
              </button>}
          </div>
        </div>
        <aside className="flex w-full shrink-0 flex-col gap-3 border-t border-border-subtle bg-bg-secondary p-4 lg:w-[318px] lg:border-l lg:border-t-0">
          <Select label="修复模型" value={model} options={Object.values(repairModels)} disabled={isProcessing || !editing}
            onChange={event => useRepair.setState({ model: event.target.value as RepairModel })} />
          {isRedraw && <label className="block text-xs text-fg-secondary">
            重绘提示词
            <textarea className="mt-1 block w-full rounded-lg border border-border-subtle bg-bg-primary p-2 text-sm text-fg-primary"
              maxLength={1000} rows={3} placeholder="留空使用模型默认提示词" value={prompt}
              disabled={isProcessing || !editing} onChange={event => useRepair.setState({ prompt: event.target.value })} />
          </label>}
          {deviceMessage && <p role="status" className="text-xs text-fg-secondary">{deviceMessage}</p>}
          {file && editing && <>
            <Slider label="画笔大小" unit=" px" min={1} max={160} value={brushSize}
              disabled={isProcessing} onChange={event => setBrushSize(Number(event.target.value))} />
            <Slider label="选区外扩" unit=" px" min={0} max={20} value={dilation}
              disabled={isProcessing} onChange={event => setDilation(Number(event.target.value))} />
            <p className="text-xs text-fg-secondary">绿色为选区，浅橙色为外扩预览。</p>
          </>}
          {imageError && <p role="alert" className="text-sm text-status-error">{imageError}</p>}
          {isProcessing && <Progress value={progress} label={statusMessage} />}
          {!isProcessing && progress < 0 && <p role="alert" className="text-sm text-status-error">{statusMessage}</p>}
          <div className="mt-auto flex flex-col gap-2">
            {result ? <>
              <Button onClick={applyResult}>应用并继续</Button>
              <Button variant="secondary" onClick={() => {
                useRepair.setState({ result: null, view: 'before' })
                reset()
              }}>返回调整</Button>
            </> : <>
              <Button icon={<Bandage size={16} />} loading={isProcessing}
                disabled={!editing || !dimensions || !hasMask || !modelReady} onClick={() => void repair()}>修复选区</Button>
              {previous && <Button variant="ghost" icon={<RotateCcw size={16} />} disabled={isProcessing || !!selection.length}
                onClick={() => useRepair.setState({ applied: previous.applied, previous: null, view: 'before' })}>撤回上次修复</Button>}
            </>}
          </div>
        </aside>
      </div>
    </div>
  )
}
