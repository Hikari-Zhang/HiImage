import { useEffect, useRef, useState } from 'react'
import { Download, Eraser, ImagePlus, Paintbrush, RotateCcw, Undo2, Wand2 } from 'lucide-react'
import { create } from 'zustand'
import ImmichActions from '../components/ImmichActions'
import ImageHistory from '../components/ImageHistory'
import MaskCanvas from '../components/MaskCanvas'
import PageHeader from '../components/layout/PageHeader'
import { Button, Progress, Select, Slider, showToast } from '../components/ui'
import { useBackendAPI } from '../hooks/useBackendAPI'
import type { PromptEditStatus } from '../services/api/catalog'
import type { ImageResult } from '../services/api/processing'
import { dataUrlToBlob, downloadDataUrl, fileToDataUrl } from '../services/fileService'
import { bindImmichSource } from '../services/immich'
import { recordImageResult } from '../services/imageHistory'
import { exportMask } from '../services/maskExport'
import { createTaskId } from '../services/taskId'
import { createTaskProgressSession } from '../services/taskProgress'
import { useBackendStore } from '../stores/useBackendStore'
import { isCurrentTask, useProcessStore, useSessionProcess } from '../stores/useProcessStore'
import type { MaskStroke } from '../types/mask'

type EditScope = 'whole' | 'selection'

const useEdit = create<{
  file: File | null
  revision: number
  prompt: string
  scope: EditScope
  modelIds: Record<EditScope, string>
  strokes: MaskStroke[]
  result: (ImageResult & { modelName: string; scope: EditScope }) | null
}>(() => ({
  file: null, revision: 0, prompt: '', scope: 'whole', modelIds: { whole: '', selection: '' }, strokes: [], result: null,
}))

const fileTypes = ['image/png', 'image/jpeg', 'image/webp']

export default function PromptEditPage() {
  const { file, revision, prompt, scope: savedScope, modelIds, strokes, result } = useEdit()
  const { isProcessing, progress, statusMessage, startProcess, finishProcess, setError, reset } = useSessionProcess('prompt-edit')
  const { editWithPrompt, getPromptEditStatus } = useBackendAPI()
  const input = useRef<HTMLInputElement>(null)
  const [preview, setPreview] = useState<{ file: File; url: string } | null>(null)
  const [status, setStatus] = useState<PromptEditStatus | null>(null)
  const [statusError, setStatusError] = useState('')
  const [statusRequest, setStatusRequest] = useState(0)
  const [brushTool, setBrushTool] = useState<'brush' | 'eraser'>('brush')
  const [brushPercent, setBrushPercent] = useState(8)
  const [imageSize, setImageSize] = useState<{ revision: number; width: number; height: number } | null>(null)
  const original = preview && preview.file === file ? preview.url : null
  const edited = result ? `data:image/png;base64,${result.image}` : null
  const canSelectRegion = status?.models?.some(model => model.supports_mask) === true
  const scope = canSelectRegion ? savedScope : 'whole'
  const local = scope === 'selection'
  const models = local ? status?.models?.filter(model => model.supports_mask) : status?.models
  const selectedModel = models?.find(model => model.id === modelIds[scope])
    ?? (local ? models?.find(model => model.available) ?? models?.[0] : models?.find(model => model.id === status?.default_model))
  const available = selectedModel?.available ?? (!local && status?.available === true)
  const unavailableReason = selectedModel ? selectedModel.reason
    : status?.reason ?? (local && status ? '当前后端没有支持选区的模型。' : null)
  const dimensions = imageSize?.revision === revision ? imageSize : null
  const brushSize = dimensions ? Math.max(1, Math.round(Math.min(dimensions.width, dimensions.height) * brushPercent / 100)) : 30
  const ready = Boolean(file && prompt.trim() && available && (!local || (dimensions && strokes.length)))

  useEffect(() => {
    if (!file) return
    const url = URL.createObjectURL(file)
    setPreview({ file, url })
    return () => URL.revokeObjectURL(url)
  }, [file])

  useEffect(() => {
    const controller = new AbortController()
    setStatus(null)
    setStatusError('')
    void getPromptEditStatus(controller.signal).then(next => {
      if (!controller.signal.aborted) {
        useBackendStore.getState().setConnected(true)
        setStatus(next)
      }
    }).catch(error => {
      if (!controller.signal.aborted) setStatusError(error instanceof Error ? error.message : '无法读取编辑状态')
    })
    return () => controller.abort()
  }, [getPromptEditStatus, statusRequest])

  function selectFile(next: File) {
    if (!fileTypes.includes(next.type)) throw new Error('请选择 PNG、JPEG 或 WebP 图片。')
    reset()
    const nextRevision = useEdit.getState().revision + 1
    useEdit.setState({ file: next, result: null, revision: nextRevision, strokes: [] })
    setBrushTool('brush')
    return nextRevision
  }

  function importFile(next: File | undefined) {
    if (!next || isProcessing) return
    try {
      selectFile(next)
    } catch (error) {
      showToast('error', error instanceof Error ? error.message : '导入图片失败')
    }
  }

  async function generate() {
    const instruction = prompt.trim()
    const modelName = selectedModel?.name ?? status?.model
    if (!file || !ready || !modelName || isProcessing) return
    const taskId = createTaskId()
    startProcess(modelName, taskId)
    try {
      const [image, mask] = await Promise.all([
        fileToDataUrl(file),
        local && dimensions ? exportMask(strokes, dimensions.width, dimensions.height) : undefined,
      ])
      const session = createTaskProgressSession(taskId, { isCurrent: () => isCurrentTask(taskId) })
      const next = await session.run(id => editWithPrompt({
        image, mask, prompt: instruction, task_id: id, model: selectedModel?.id,
      }))
      if (isCurrentTask(taskId)) {
        useEdit.setState({ result: { ...next, modelName, scope } })
        recordImageResult(taskId, 'prompt-edit', next.image, file.name)
        finishProcess(taskId)
      }
    } catch (error) {
      if (isCurrentTask(taskId)) setError(taskId, error instanceof Error ? error.message : '图片编辑失败')
    }
  }

  async function useResultAsSource() {
    if (!edited || isProcessing) return
    try {
      const blob = await dataUrlToBlob(edited)
      if (useEdit.getState().result !== result || useProcessStore.getState().sessions['prompt-edit']?.isProcessing) return
      selectFile(new File([blob], 'edited.png', { type: blob.type }))
      showToast('success', '已将处理结果设为原图')
    } catch {
      showToast('error', '无法将编辑结果设为原图')
    }
  }

  function undoStroke() {
    if (!isProcessing) useEdit.setState(state => ({ strokes: state.strokes.slice(0, -1) }))
  }

  return (
    <div className="flex h-full flex-col">
      <PageHeader title="提示词编辑" right={
        <div className="flex gap-2">
          <ImageHistory disabled={isProcessing} onUse={file => { selectFile(file) }} />
          {edited && <>
            <Button size="sm" variant="secondary" icon={<RotateCcw size={16} />} disabled={isProcessing}
              onClick={() => void useResultAsSource()}>将结果设为原图</Button>
            <Button size="sm" variant="secondary" icon={<Download size={16} />} onClick={() => {
              void downloadDataUrl(edited, 'edited.png').catch(() => showToast('error', '下载失败'))
            }}>下载结果</Button>
          </>}
        </div>
      } />
      <div className="flex flex-wrap items-center gap-2 border-b border-border-subtle px-3 py-2">
        <Button size="sm" variant="secondary" icon={<ImagePlus size={16} />} disabled={isProcessing}
          onClick={() => input.current?.click()}>{file ? '更换图片' : '打开图片'}</Button>
        <input ref={input} type="file" accept={fileTypes.join(',')} aria-label="编辑原图文件" className="hidden"
          onChange={event => {
            const next = event.target.files?.[0]
            event.target.value = ''
            importFile(next)
          }} />
        <ImmichActions editor="prompt-edit" revision={revision} original={original} result={edited} limit={isProcessing ? 0 : 1}
          onImport={async ([item]) => {
            if (useEdit.getState().revision !== revision) throw new Error('编辑器中的图片已改变，请重新选择')
            const imported = selectFile(item.file)
            bindImmichSource('prompt-edit', {
              token: item.token, name: item.file.name, taken: item.taken, backend: item.backend, revision: imported,
            })
          }} />
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-auto lg:flex-row">
        <div className="flex min-h-60 min-w-0 flex-1 bg-workspace p-4"
          onDragOver={event => event.preventDefault()}
          onDrop={event => { event.preventDefault(); importFile(event.dataTransfer.files[0]) }}>
          {original ? (
            <div className={`grid w-full min-w-0 gap-4 ${edited ? 'md:grid-cols-2' : ''}`}>
              <figure className="flex min-h-0 min-w-0 flex-col gap-2">
                <figcaption className="text-xs text-fg-secondary">原图</figcaption>
                <div className={`relative flex flex-1 items-center justify-center overflow-hidden ${local ? 'min-h-56' : 'min-h-0'}`}>
                  {local ? (
                    <div aria-label="局部编辑画布" className={`absolute inset-0 ${isProcessing ? 'pointer-events-none' : ''}`}>
                      <MaskCanvas key={revision} imageSrc={original} strokes={strokes} canvasTool="brush"
                        brushSettings={{ size: brushSize, tool: brushTool, shape: 'circle', hardness: 100 }}
                        maskPreviewMaxEdge={1024}
                        onImageLoad={(width, height) => setImageSize({ revision, width, height })}
                        onStrokeComplete={stroke => {
                          if (!isProcessing) useEdit.setState(state => ({ strokes: [...state.strokes, stroke] }))
                        }}
                        onUndo={undoStroke} />
                    </div>
                  ) : (
                    <img src={original} alt="编辑原图" className="max-h-full max-w-full rounded object-contain"
                      onError={() => showToast('error', '无法预览这张图片')} />
                  )}
                </div>
              </figure>
              {edited && result && (
                <figure className="flex min-h-0 min-w-0 flex-col gap-2">
                  <figcaption className="text-xs text-fg-secondary">
                    {result.scope === 'selection' ? '局部编辑结果' : '编辑结果'} · {result.modelName} · {result.width} × {result.height}
                  </figcaption>
                  <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden">
                    <img src={edited} alt="编辑结果"
                      className={`rounded object-contain ${local ? 'max-h-[90%] max-w-[90%]' : 'max-h-full max-w-full'}`} />
                  </div>
                </figure>
              )}
            </div>
          ) : (
            <button type="button" onClick={() => input.current?.click()} className="flex w-full flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border-subtle text-fg-secondary hover:bg-bg-hover">
              <ImagePlus size={30} strokeWidth={1.5} />
              <span className="text-sm">拖入图片或点击打开</span>
            </button>
          )}
        </div>
        <form className="flex w-full flex-shrink-0 flex-col gap-3 border-t border-border-subtle bg-bg-secondary p-4 lg:w-[320px] lg:border-l lg:border-t-0"
          onSubmit={event => { event.preventDefault(); void generate() }}>
          {canSelectRegion && (
            <Select label="编辑范围" value={scope} disabled={isProcessing}
              options={[{ value: 'whole', label: '整图' }, { value: 'selection', label: '局部选区' }]}
              onChange={event => useEdit.setState({ scope: event.target.value as EditScope })} />
          )}
          {models?.length ? (
            <Select label="编辑模型" value={selectedModel?.id ?? ''} disabled={isProcessing}
              options={models.map(model => ({
                value: model.id, label: model.available ? model.name : `${model.name}（未就绪）`,
                description: model.reason ?? undefined,
              }))}
              onChange={event => useEdit.setState(state => ({ modelIds: { ...state.modelIds, [scope]: event.target.value } }))} />
          ) : null}
          {local && (
            <div className="flex flex-col gap-2">
              <p className="text-xs text-fg-secondary">在原图上涂抹要修改的区域。绿色覆盖表示选区。</p>
              <div className="flex flex-wrap gap-1">
                <Button type="button" size="sm" icon={<Paintbrush size={14} />} aria-pressed={brushTool === 'brush'}
                  variant={brushTool === 'brush' ? 'secondary' : 'ghost'} disabled={!original || isProcessing}
                  onClick={() => setBrushTool('brush')}>画笔</Button>
                <Button type="button" size="sm" icon={<Eraser size={14} />} aria-pressed={brushTool === 'eraser'}
                  variant={brushTool === 'eraser' ? 'secondary' : 'ghost'} disabled={!strokes.length || isProcessing}
                  onClick={() => setBrushTool('eraser')}>橡皮擦</Button>
                <Button type="button" size="sm" variant="ghost" icon={<Undo2 size={14} />}
                  disabled={!strokes.length || isProcessing} onClick={undoStroke}>撤销</Button>
                <Button type="button" size="sm" variant="ghost" disabled={!strokes.length || isProcessing}
                  onClick={() => { useEdit.setState({ strokes: [] }); setBrushTool('brush') }}>清空</Button>
              </div>
              <Slider label="笔尖大小" unit="%" value={brushPercent} min={1} max={50}
                disabled={!original || isProcessing} onChange={event => setBrushPercent(Number(event.target.value))} />
            </div>
          )}
          <label htmlFor="edit-prompt" className="text-sm font-medium">修改指令</label>
          <textarea id="edit-prompt" rows={6} maxLength={1000} value={prompt}
            placeholder={local ? '例如：把选中的徽章换成白底黑字' : '例如：把背景改成夜景，保留人物和构图'}
            className="w-full rounded-lg border border-border-subtle bg-bg-primary p-3 text-sm outline-none focus:border-border-focus"
            onChange={event => useEdit.setState({ prompt: event.target.value })} />
          <p className="text-xs text-fg-secondary">
            {local ? '按原图尺寸输出，选区外的像素保持原样。' : '输出长边最多 1024 px'}
          </p>
          {(statusError || unavailableReason) && (
            <div className="flex flex-wrap items-center gap-2">
              <p role="status" className="text-xs text-fg-secondary">{statusError || unavailableReason}</p>
              <Button type="button" size="sm" variant="ghost" onClick={() => setStatusRequest(value => value + 1)}>重新检查</Button>
            </div>
          )}
          {isProcessing && <Progress value={progress} label={statusMessage} />}
          {!isProcessing && progress < 0 && <p role="alert" className="text-sm text-status-error">{statusMessage}</p>}
          <Button type="submit" icon={<Wand2 size={16} />} loading={isProcessing}
            disabled={!ready} className="mt-auto">生成编辑结果</Button>
        </form>
      </div>
    </div>
  )
}
