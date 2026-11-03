import { useState, useCallback, useRef, useEffect } from 'react'
import { Upload, Wand2, Trash2, Download, Move, Square, PenTool, RotateCcw, ChevronDown, ChevronRight } from 'lucide-react'
import { clsx } from 'clsx'
import ImageCanvas from '../components/ImageCanvas'
import MaskCanvas from '../components/MaskCanvas'
import MaskToolbar from '../components/MaskToolbar'
import ImageCompare from '../components/ImageCompare'
import PageHeader from '../components/layout/PageHeader'
import SendToEditor from '../components/SendToEditor'
import { Button, Select, Slider, Progress, showToast } from '../components/ui'
import ModelSelect from '../components/ModelSelect/ModelSelect'
import { useImageStore } from '../stores/useImageStore'
import { useEditorSettings } from '../stores/useEditorSettings'
import { useModelStore } from '../stores/useModelStore'
import { isCurrentTask, useSessionProcess } from '../stores/useProcessStore'
import { useBackendAPI } from '../hooks/useBackendAPI'
import { useDeviceOptions } from '../hooks/useDeviceOptions'
import { useDownloadStore } from '../stores/useDownloadStore'
import { useDownloadManager } from '../hooks/useDownloadManager'
import { useMaskExport } from '../hooks/useMaskExport'
import { PostprocessMethod, DownloadStatus, ModelStatus } from '../constants'
import type { MaskTool } from '../types/mask'
import type { ROI } from '../types/mask'
import { fileToDataUrl, downloadDataUrl } from '../services/fileService'
import { createTaskId } from '../services/taskId'
import { createTaskProgressSession } from '../services/taskProgress'
import type { RedrawOptions, WatermarkCandidate } from '../services/api/processing'

type CanvasTool = 'draw' | 'pan'

const REDRAW_FIELDS = [
  { key: 'seed', label: '随机种子', min: 0, max: 2147483647, step: 1, placeholder: '42' },
  { key: 'num_inference_steps', label: '重绘步数', min: 1, max: 50, step: 1, placeholder: '50' },
  { key: 'guidance_scale', label: '引导强度', min: 1, max: 30, step: 'any', placeholder: '30' },
] as const

// 后处理方法选项
const POSTPROCESS_OPTIONS = [
  { value: PostprocessMethod.NONE,        label: '无后处理' },
  { value: PostprocessMethod.POISSON,     label: 'Poisson 融合（修边缘）' },
  { value: PostprocessMethod.GFPGAN,      label: 'GFPGAN（人脸专用）' },
]

export default function WatermarkRemoval({ isVisible = true }: { isVisible?: boolean }) {
  const {
    sourceRevision, sourceImage, resultImage, showResult, setShowResult, rois, selectedROIs, imageWidth, imageHeight,
    maskStrokes, brushSettings, canvasTool,
    setSourceImage, setImageDimensions, setResultImage,
    addROI, removeROI, clearROIs, selectROI, setROIs,
    addMaskStroke, undoMaskStroke, clearMaskStrokes, setCanvasTool, setBrushSettings,
  } = useImageStore()

  const { isProcessing, progress, statusMessage, startProcess, finishProcess, setError } = useSessionProcess('watermark')
  const { detectWatermark, inpaintWithMask, runPipeline } = useBackendAPI()
  const {
    device, inpaintModel: preferredInpaintModel, defaultDilation, sensitivity, disableNsfw,
    postprocessMethod, postprocessEnabled,
    upscaleModel, upscaleEnabled,
    setDevice, setInpaintModel, setDilation, setSensitivity,
    setPostprocessMethod, setPostprocessEnabled, setUpscaleEnabled, setUpscaleModel,
  } = useEditorSettings('watermark')
  const { inpaintGroups, upscaleGroups, inpaintDeviceOverrides, inpaintPipelines, defaultInpaintModel } = useModelStore()
  const hasModel = (id: string) => inpaintGroups.some(group => group.options.some(model => model.value === id))
  const inpaintModel = hasModel(preferredInpaintModel) ? preferredInpaintModel : defaultInpaintModel
  const repairAvailable = hasModel(inpaintModel)
  const effectiveDevice = inpaintDeviceOverrides?.[inpaintModel] || device
  const { options: deviceOptions, loading: detectingDevices, cudaAvailable } = useDeviceOptions({ device, setDevice })
  const isRedraw = inpaintPipelines[inpaintModel] === 'flux_fill'
  const [prompt, setPrompt] = useState('')
  const [redrawFields, setRedrawFields] = useState({ seed: '', num_inference_steps: '', guidance_scale: '' })
  const redraw: RedrawOptions | undefined = isRedraw ? { prompt: prompt.trim() || undefined } : undefined
  let invalidRedraw = false
  if (redraw) {
    for (const { key, min, max, step } of REDRAW_FIELDS) {
      if (redrawFields[key] === '') continue
      const value = Number(redrawFields[key])
      if (!Number.isFinite(value) || value < min || value > max || (step === 1 && !Number.isInteger(value))) {
        invalidRedraw = true
      }
      redraw[key] = value
    }
  }
  let redrawDeviceMessage = ''
  if (isRedraw) {
    if (detectingDevices) redrawDeviceMessage = '正在检测 CUDA 设备…'
    else if (cudaAvailable === false) redrawDeviceMessage = '当前后端没有可用的 CUDA 设备。可切换到 LaMa 快速修复。'
    else if (cudaAvailable !== true) redrawDeviceMessage = '无法确认 CUDA 设备状态，请刷新页面后重试。'
    else if (device !== 'cuda') redrawDeviceMessage = 'FLUX Fill 局部重绘需要选择 CUDA 设备。'
  }
  const repairReady = repairAvailable && !redrawDeviceMessage && !invalidRedraw
  const downloadTasks = useDownloadStore((s) => s.tasks)
  const { startDownload } = useDownloadManager()
  const { exportMask, exportCombinedMask } = useMaskExport()

  const [tool, setTool] = useState<CanvasTool>('draw')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [showMaskPreview, setShowMaskPreview] = useState(true)
  const [candidates, setCandidates] = useState<WatermarkCandidate[]>([])
  const [detectionMessage, setDetectionMessage] = useState('')
  useEffect(() => { setCandidates([]); setDetectionMessage('') }, [sourceRevision])

  const toggleCandidate = (candidate: WatermarkCandidate, index: number) => {
    const id = `ocr-${sourceRevision}-${index}`
    if (rois.some(roi => roi.id === id)) removeROI(id)
    else {
      const [x1, y1, x2, y2] = candidate.region
      setROIs([...rois, { id, x1, y1, x2, y2, polygon: candidate.polygon || undefined }])
    }
  }

  const fileInputRef = useRef<HTMLInputElement>(null)

  // Open file
  const handleOpenFile = async () => {
    fileInputRef.current?.click()
  }

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    fileToDataUrl(file).then((dataUrl) => {
      setSourceImage(dataUrl)
      setShowResult(false)
    }).catch((err) => showToast('error', `读取图片失败: ${err.message}`))
  }

  // Drag & drop
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const file = e.dataTransfer.files[0]
    if (!file || !file.type.startsWith('image/')) return
    fileToDataUrl(file).then((dataUrl) => {
      setSourceImage(dataUrl)
      setShowResult(false)
    }).catch((err) => showToast('error', `读取图片失败: ${err.message}`))
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }

  // ROI handlers
  const handleROIDrawn = useCallback((roi: Omit<ROI, 'id'>) => {
    addROI(roi)
  }, [addROI])

  const handleROISelect = useCallback((id: string) => {
    selectROI(id)
  }, [selectROI])

  const handleImageLoad = useCallback((w: number, h: number) => {
    setImageDimensions(w, h, sourceRevision)
  }, [setImageDimensions, sourceRevision])

  // Auto detect
  const handleAutoDetect = async () => {
    if (!sourceImage) return
    const taskId = createTaskId()
    try {
      startProcess('detect', taskId)
      // 检测接口没有增量进度，直接提交，避免依赖额外的 WebSocket 握手。
      const result = await detectWatermark(sourceImage, sensitivity / 100, taskId)
      if (!isCurrentTask(taskId)) return
      // 重新检测清理旧候选选区，手动选区和笔画保留。
      setROIs(rois.filter(roi => !roi.id.startsWith('ocr-')))
      setCandidates(result.candidates || result.regions.map(region => ({
        region, polygon: null, text: '', text_confidence: null,
        watermark_score: null, reasons: ['需人工确认'],
      })))
      setDetectionMessage(result.warning || '文字候选不一定是水印，请勾选需要去除的区域。')
      finishProcess(taskId, `检测到 ${result.regions.length} 个候选，请勾选`)
      if (result.regions.length === 0) {
        showToast('info', '未找到可靠的文字候选，不代表没有水印；可提高敏感度或手动圈选')
      }
    } catch (err: any) {
      if (!isCurrentTask(taskId)) return
      setError(taskId, err.message)
      showToast('error', err.message)
    }
  }

  // Process
  const handleProcess = async () => {
    if (!repairReady) return
    if (!sourceImage) return
    // 优先使用遮罩模式，否则使用 ROI 模式
    if (maskStrokes.length === 0 && rois.length === 0) return

    // 检查当前模型的下载状态
    const task = downloadTasks[inpaintModel]
    if (task?.status === DownloadStatus.DOWNLOADING || task?.status === DownloadStatus.QUEUED) {
      showToast('info', `模型${task.status === DownloadStatus.QUEUED ? '排队中，' : ''}下载中，请稍候...`)
      return
    }
    if (task?.status === ModelStatus.MISSING || task?.status === DownloadStatus.ERROR) {
      showToast('info', '模型未下载，已自动加入下载队列，完成后可继续操作')
      startDownload(inpaintModel)
      return
    }

    const taskId = createTaskId()
    const session = createTaskProgressSession(taskId, { isCurrent: () => isCurrentTask(taskId) })
    try {
      startProcess(inpaintModel, taskId)
      const usePipeline = postprocessEnabled || upscaleEnabled
      const resultBase64 = await session.run(async (requestTaskId) => {
        // 导出合并遮罩为 base64（画笔遮罩 + ROI 矩形）
        const maskDataURL = await exportCombinedMask(maskStrokes, rois, imageWidth, imageHeight)
        if (!isCurrentTask(requestTaskId)) throw new Error('任务已被新任务替代')

        if (usePipeline) {
          // 走完整 Pipeline（带遮罩）
          const result = await runPipeline({
            image: sourceImage,
            mask: maskDataURL,
            inpaint_model: inpaintModel,
            device,
            dilation: defaultDilation,
            disable_nsfw: disableNsfw,
            postprocess_method: postprocessMethod,
            postprocess_enabled: postprocessEnabled,
            upscale_enabled: upscaleEnabled,
            upscale_model: upscaleModel,
            ...(redraw && { redraw }),
            task_id: requestTaskId,
          })
          return result.image
        } else {
          // 仅去水印（遮罩模式）
          const result = await inpaintWithMask({
            image: sourceImage,
            mask: maskDataURL,
            model: inpaintModel,
            device,
            dilation: defaultDilation,
            disable_nsfw: disableNsfw,
            ...(redraw && { redraw }),
            task_id: requestTaskId,
          })
          return result.image
        }
      })

      if (!isCurrentTask(taskId)) return
      setResultImage(`data:image/png;base64,${resultBase64}`)
      setShowResult(true)
      finishProcess(taskId, '处理完成')
      showToast('success', usePipeline ? 'Pipeline 处理完成' : '水印去除完成')
    } catch (err: any) {
      if (!isCurrentTask(taskId)) return
      setError(taskId, err.message)
      showToast('error', err.message)
    }
  }

  // 将遮罩笔划导出为 base64 PNG（主线程，简化版）
  const exportMaskToDataURL = (strokes: any[], width: number, height: number): string => {
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const ctx = canvas.getContext('2d')!

    // 黑色背景（遮罩区域为白色）
    ctx.fillStyle = 'black'
    ctx.fillRect(0, 0, width, height)

    // 绘制所有笔划（白色表示遮罩区域）
    strokes.forEach((stroke) => {
      const isEraser = stroke.tool === 'eraser'
      
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      ctx.lineWidth = stroke.size
      ctx.globalCompositeOperation = isEraser ? 'destination-out' : 'source-over'
      
      if (!isEraser) {
        ctx.strokeStyle = 'white'
      }

      ctx.beginPath()
      const points = stroke.points
      for (let i = 0; i < points.length; i += 2) {
        const x = points[i]
        const y = points[i + 1]
        if (i === 0) {
          ctx.moveTo(x, y)
        } else {
          ctx.lineTo(x, y)
        }
      }
      ctx.stroke()
    })

    return canvas.toDataURL('image/png')
  }

  // Save
  const handleSave = async () => {
    if (!resultImage) return
    // 默认文件名：原文件名_processed.png
    const defaultName = 'result_processed.png'
    await downloadDataUrl(resultImage, defaultName)
    showToast('success', '结果已下载')
  }

  // 将处理结果作为新的源图进行再次处理
  const handleUseResultAsSource = () => {
    if (!resultImage) return
    clearROIs()
    setSourceImage(resultImage)
    setShowResult(false)
    showToast('success', '已将处理结果设为原图')
  }

  // Delete selected ROIs
  const handleDeleteSelected = () => {
    selectedROIs.forEach((id) => removeROI(id))
  }

  // Keyboard shortcuts (必须在 handleDeleteSelected 之后定义)
  useEffect(() => {
    if (!isVisible) return
    const handleKeyDown = (e: KeyboardEvent) => {
      // 忽略输入框中的按键
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
        return
      }

      switch (e.code) {
        case 'KeyB':
          if (!e.ctrlKey && !e.metaKey) {
            setCanvasTool('brush')
            setBrushSettings({ tool: 'brush' })
          }
          break
        case 'KeyE':
          if (!e.ctrlKey && !e.metaKey) {
            setCanvasTool('brush')
            setBrushSettings({ tool: 'eraser' })
          }
          break
        case 'KeyW':
          if (!e.ctrlKey && !e.metaKey) {
            setBrushSettings({ tool: 'magicWand' })
          }
          break
        case 'BracketLeft': // [
          if (!e.ctrlKey && !e.metaKey) {
            setBrushSettings({ size: Math.max(1, brushSettings.size - 5) })
          }
          break
        case 'BracketRight': // ]
          if (!e.ctrlKey && !e.metaKey) {
            setBrushSettings({ size: Math.min(100, brushSettings.size + 5) })
          }
          break
        case 'Delete':
        case 'Backspace':
          if (canvasTool === 'brush') {
            undoMaskStroke()
          } else {
            handleDeleteSelected()
          }
          break
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isVisible, canvasTool, brushSettings.size, setCanvasTool, setBrushSettings, undoMaskStroke, handleDeleteSelected])

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <PageHeader
        title="去水印"
        subtitle={imageWidth > 0 ? `${imageWidth} x ${imageHeight}` : undefined}
        right={<div className="flex items-center gap-2"><SendToEditor image={resultImage} current="watermark" /><span className="text-xs text-fg-secondary">{inpaintModel} | 实际设备 {effectiveDevice}{effectiveDevice !== device ? `（请求 ${device}）` : ''}</span></div>}
      />

      {/* Content area */}
      <div className="editor-workspace flex-1 flex overflow-hidden min-h-0">
        {/* Canvas area */}
        <div
          className="flex-1 min-w-0 bg-workspace relative"
          onDrop={handleDrop}
          onDragOver={handleDragOver}
        >
          {!sourceImage ? (
            <div className="empty-canvas absolute inset-0 flex flex-col items-center justify-center gap-4 text-fg-secondary">
              <Upload size={48} strokeWidth={1} />
              <p className="text-sm">拖拽图片到此处，或点击打开文件</p>
              <Button onClick={handleOpenFile}>打开图片</Button>
              <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleFileInput} />
            </div>
          ) : showResult && resultImage && sourceImage ? (
            <ImageCompare beforeSrc={sourceImage} afterSrc={resultImage} beforeLabel="原图" afterLabel="处理后" />
          ) : (
            <>
              {/* 始终显示 MaskCanvas，同时支持 ROI 和画笔遮罩 */}
              <MaskCanvas
                key={sourceRevision}
                isActive={isVisible}
                onImageLoad={handleImageLoad}
                imageSrc={sourceImage}
                strokes={maskStrokes}
                brushSettings={brushSettings}
                canvasTool={canvasTool}
                rois={rois}
                selectedROIs={selectedROIs}
                onROISelect={handleROISelect}
                onROIDrawn={handleROIDrawn}
                onStrokeComplete={addMaskStroke}
                onUndo={undoMaskStroke}
                showMaskPreview={showMaskPreview}
              />

              {/* 工具栏 */}
              <div className="absolute bottom-3 left-1/2 -translate-x-1/2 z-10">
                {canvasTool === 'rectangle' ? (
                  <div className="flex gap-1 bg-bg-secondary rounded-lg p-1 border border-border-subtle shadow-lg">
                    <button
                      onClick={() => setTool('draw')}
                      className={clsx('w-8 h-8 rounded flex items-center justify-center transition-colors', tool === 'draw' ? 'bg-bg-active text-fg-accent' : 'hover:bg-bg-hover text-fg-secondary')}
                      title="绘制 ROI (R)"
                    >
                      <Square size={16} />
                    </button>
                    <button
                      onClick={() => setTool('pan')}
                      className={clsx('w-8 h-8 rounded flex items-center justify-center transition-colors', tool === 'pan' ? 'bg-bg-active text-fg-accent' : 'hover:bg-bg-hover text-fg-secondary')}
                      title="平移 (Space)"
                    >
                      <Move size={16} />
                    </button>
                    <button
                      onClick={() => setCanvasTool('brush')}
                      className={clsx('w-8 h-8 rounded flex items-center justify-center transition-colors', 'hover:bg-bg-hover text-fg-secondary')}
                      title="画笔模式 (B)"
                    >
                      <PenTool size={16} />
                    </button>
                    <div className="w-px bg-border-subtle mx-0.5" />
                    <button
                      onClick={handleOpenFile}
                      className="w-8 h-8 rounded flex items-center justify-center hover:bg-bg-hover text-fg-secondary transition-colors"
                      title="打开图片"
                    >
                      <Upload size={16} />
                    </button>
                    {showResult && (
                      <button
                        onClick={() => setShowResult(false)}
                        className="w-8 h-8 rounded flex items-center justify-center hover:bg-bg-hover text-fg-secondary transition-colors"
                        title="返回编辑"
                      >
                        <RotateCcw size={16} />
                      </button>
                    )}
                  </div>
                ) : (
                  <MaskToolbar
                    brushSettings={brushSettings}
                    canvasTool={canvasTool}
                    canUndo={maskStrokes.length > 0}
                    onBrushSettingsChange={setBrushSettings}
                    onCanvasToolChange={setCanvasTool}
                    onUndo={undoMaskStroke}
                  />
                )}
              </div>
            </>
          )}
          <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleFileInput} />
        </div>

        {/* Control panel */}
        <div className="editor-inspector w-[280px] bg-bg-secondary border-l border-border-subtle p-3 flex flex-col gap-3 overflow-y-auto min-h-0">
          {/* ROI list */}
          <section>
            <h3 className="text-xs uppercase tracking-wider text-fg-secondary mb-2">水印区域</h3>
            <div className="bg-bg-primary rounded border border-border-subtle p-1 min-h-[60px] max-h-[140px] overflow-y-auto">
              {/* 画笔遮罩模式 */}
              {maskStrokes.length > 0 && (
                <div className="p-2">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs text-fg-primary">画笔遮罩</span>
                    <span className="text-xs text-fg-accent">{maskStrokes.length} 笔</span>
                  </div>
                  <div className="text-xs text-fg-secondary">
                    已绘制 {maskStrokes.filter(s => s.tool === 'brush').length} 笔画笔，
                    {maskStrokes.filter(s => s.tool === 'eraser').length} 笔橡皮擦
                  </div>
                </div>
              )}
              {rois.length === 0 ? (
                <p className="text-xs text-fg-secondary p-2 text-center">在画布上拖拽绘制区域</p>
              ) : (
                rois.map((roi, i) => (
                  <div
                    key={roi.id}
                    onClick={() => selectROI(roi.id)}
                    className={clsx(
                      'flex items-center justify-between px-2 py-1 rounded text-xs cursor-pointer transition-colors',
                      selectedROIs.includes(roi.id) ? 'bg-bg-active text-fg-accent' : 'hover:bg-bg-hover text-fg-primary'
                    )}
                  >
                    <span>区域 {i + 1} ({roi.x2 - roi.x1}x{roi.y2 - roi.y1})</span>
                    <button
                      onClick={(e) => { e.stopPropagation(); removeROI(roi.id) }}
                      className="text-fg-secondary hover:text-status-error"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                ))
              )}
            </div>
            <div className="flex gap-1 mt-2">
                <>
                  <Button size="sm" variant="primary" onClick={handleAutoDetect} disabled={!sourceImage || isProcessing} icon={<Wand2 size={12} />} className="flex-1">
                    自动检测
                  </Button>
                  {selectedROIs.length > 0 ? (
                    <Button size="sm" variant="danger" onClick={handleDeleteSelected} className="flex-1">
                      删除选中 ({selectedROIs.length})
                    </Button>
                  ) : (
                    <Button size="sm" variant="ghost" onClick={() => { clearROIs(); clearMaskStrokes() }} className="flex-1" disabled={rois.length === 0 && maskStrokes.length === 0}>
                      清除全部
                    </Button>
                  )}
                </>
            </div>
          </section>

          {detectionMessage && <section className="space-y-2">
            <p className="text-xs text-fg-secondary">{detectionMessage}</p>
            <div className="max-h-48 overflow-y-auto space-y-2">
              {candidates.map((candidate, index) => <label key={index}
                className="flex gap-2 text-xs text-fg-primary cursor-pointer">
                <input type="checkbox" checked={rois.some(roi => roi.id === `ocr-${sourceRevision}-${index}`)}
                  disabled={isProcessing} onChange={() => toggleCandidate(candidate, index)}
                  aria-label={`候选 ${index + 1} ${candidate.text}`} />
                <span>{candidate.text || `候选区域 ${index + 1}`}
                  <span className="block text-fg-secondary">{candidate.reasons.join('、')}</span>
                </span>
              </label>)}
            </div>
          </section>}

          {/* Sensitivity */}
          <label className="flex items-center justify-between text-xs text-fg-secondary">
            显示遮罩预览
            <input type="checkbox" checked={showMaskPreview}
              onChange={e => setShowMaskPreview(e.target.checked)} />
          </label>
          <p className="text-xs text-fg-secondary">绿色为选中区域；画笔补选，橡皮擦取消，Ctrl+Z 撤销。扩张参数会在修复时进一步扩大范围。</p>
          <Slider
            label="检测敏感度"
            min={10}
            max={90}
            value={sensitivity}
            onChange={(e) => setSensitivity(Number(e.target.value))}
            unit="%"
          />

          {/* Model selector */}
          <ModelSelect
            label="模型"
            value={inpaintModel}
            onChange={(v) => setInpaintModel(v)}
            size="sm"
            groups={inpaintGroups}
          />

          {/* Device */}
          <Select
            label="设备"
            value={device}
            onChange={(e) => setDevice(e.target.value)}
            size="sm"
            options={deviceOptions}
          />

          {isRedraw && (
            <fieldset disabled={isProcessing} className="space-y-2">
              <legend className="text-sm font-medium">局部重绘参数</legend>
              <label className="block text-xs">
                重绘提示词
                <textarea
                  className="mt-1 block w-full rounded border border-border bg-transparent p-2"
                  maxLength={1000}
                  rows={3}
                  placeholder="留空时根据周围背景和纹理自然补绘"
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value)}
                />
              </label>
              {REDRAW_FIELDS.map(({ key, label, ...attributes }) => (
                <label key={key} className="flex items-center justify-between gap-2 text-xs">
                  {label}
                  <input
                    type="number"
                    className="w-24 rounded border border-border bg-transparent p-1"
                    {...attributes}
                    value={redrawFields[key]}
                    onChange={(event) => setRedrawFields({ ...redrawFields, [key]: event.target.value })}
                  />
                </label>
              ))}
              <p className="text-xs text-fg-secondary">留空使用模型默认参数。</p>
              {redrawDeviceMessage && <p role="status" className="text-xs text-fg-secondary">{redrawDeviceMessage}</p>}
              {invalidRedraw && <p role="alert" className="text-xs text-fg-secondary">请检查随机种子、步数和引导强度的取值范围。</p>}
            </fieldset>
          )}

          {/* Dilation */}
          <Slider
            label="遮罩扩张"
            min={0}
            max={30}
            value={defaultDilation}
            onChange={(e) => setDilation(Number(e.target.value))}
            unit="px"
          />

          {/* Advanced: Postprocess & Upscale */}
          <section>
            <button
              className="flex items-center gap-1 text-xs uppercase tracking-wider text-fg-secondary w-full mb-2"
              onClick={() => setShowAdvanced(!showAdvanced)}
            >
              {showAdvanced ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              后处理增强
              {(postprocessEnabled || upscaleEnabled) && (
                <span className="ml-auto text-fg-accent">●</span>
              )}
            </button>

            {showAdvanced && (
              <div className="space-y-2">
                {/* Postprocess toggle */}
                <label className="flex items-center justify-between gap-2 cursor-pointer">
                  <span className="text-xs text-fg-primary">背景修复</span>
                  <input
                    type="checkbox"
                    checked={postprocessEnabled}
                    onChange={(e) => setPostprocessEnabled(e.target.checked)}
                    className="accent-[var(--color-accent)]"
                  />
                </label>

                {postprocessEnabled && (
                  <Select
                    label=""
                    value={postprocessMethod}
                    onChange={(e) => setPostprocessMethod(e.target.value)}
                    size="sm"
                    options={POSTPROCESS_OPTIONS}
                  />
                )}

                {/* Upscale toggle */}
                <label className="flex items-center justify-between gap-2 cursor-pointer">
                  <span className="text-xs text-fg-primary">超分辨率</span>
                  <input
                    type="checkbox"
                    checked={upscaleEnabled}
                    onChange={(e) => setUpscaleEnabled(e.target.checked)}
                    className="accent-[var(--color-accent)]"
                  />
                </label>

                {upscaleEnabled && (
                  <Select
                    label=""
                    value={upscaleModel}
                    onChange={(e) => setUpscaleModel(e.target.value)}
                    size="sm"
                    groups={upscaleGroups}
                  />
                )}
              </div>
            )}
          </section>

          {!repairAvailable && <p role="status" className="text-xs text-fg-secondary">当前没有可用的修复模型，仍可检测和标记水印。</p>}
          {/* Process button */}
          <div className="mt-auto pt-2 space-y-2">
            {(() => {
              const task = downloadTasks[inpaintModel]
              const isModelBusy = task?.status === DownloadStatus.DOWNLOADING || task?.status === DownloadStatus.QUEUED
              const isModelMissing = task?.status === ModelStatus.MISSING || task?.status === DownloadStatus.ERROR
              const canProcess = repairReady && sourceImage && (maskStrokes.length > 0 || rois.length > 0) && !isModelBusy
              return (
                <Button
                  onClick={handleProcess}
                  disabled={isProcessing || !canProcess}
                  loading={isProcessing}
                  className="w-full"
                  size="lg"
                >
                  {isProcessing
                    ? statusMessage
                    : task?.status === DownloadStatus.DOWNLOADING
                      ? '等待下载...'
                      : task?.status === DownloadStatus.QUEUED
                        ? `排队中 #${task.position}`
                        : isModelMissing
                          ? '点击下载并处理'
                          : (postprocessEnabled || upscaleEnabled)
                            ? (isRedraw ? '重绘 + 增强' : '去水印 + 增强')
                            : isRedraw ? '开始重绘' : '去除水印'}
                </Button>
              )
            })()}

            {isProcessing && (
              <Progress value={progress} label={`${statusMessage} ${progress > 0 ? progress + '%' : ''}`} />
            )}

            {resultImage && (
              <>
                <Button
                  variant="secondary"
                  onClick={() => setShowResult(!showResult)}
                  className="w-full"
                  size="sm"
                >
                  {showResult ? '返回编辑' : '查看对比'}
                </Button>
                <Button
                  variant="ghost"
                  onClick={handleUseResultAsSource}
                  icon={<Upload size={14} />}
                  className="w-full"
                  size="sm"
                >
                  将结果设为原图
                </Button>
                <Button
                  variant="ghost"
                  onClick={handleSave}
                  icon={<Download size={14} />}
                  className="w-full"
                  size="sm"
                >
                  保存结果
                </Button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
