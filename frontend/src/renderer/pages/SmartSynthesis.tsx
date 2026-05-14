import { useState, useRef, useEffect, useCallback } from 'react'
import {
  Upload, Download, Square, Move, Trash2, RotateCcw,
  Image, Shirt, User, Sparkles, Info, ChevronDown, ChevronRight,
  Crosshair, Wand2, MessageSquare, Loader2, Clock, XCircle, CheckCircle2,
  type LucideIcon,
} from 'lucide-react'
import { clsx } from 'clsx'
import ImageCanvas from '../components/ImageCanvas'
import ImageCompare from '../components/ImageCompare'
import PageHeader from '../components/layout/PageHeader'
import { Button, Select, Progress, showToast } from '../components/ui'
import { useImageStore } from '../stores/useImageStore'
import { useSettingsStore } from '../stores/useSettingsStore'
import { useProcessStore } from '../stores/useProcessStore'
import { useBackendAPI } from '../hooks/useBackendAPI'
import { useDeviceOptions } from '../hooks/useDeviceOptions'
import { useDownloadStore } from '../stores/useDownloadStore'
import { useDownloadManager } from '../hooks/useDownloadManager'
import { DownloadStatus, ModelStatus } from '../constants'
import type { SynthesisTagValue } from '../constants'
import type { ROI } from '../stores/useImageStore'

type CanvasTool = 'draw' | 'pan'

// ─── 图标映射表（icon_name → Lucide 组件）────────────────────────────────
const ICON_MAP: Record<string, LucideIcon> = {
  Image, Shirt, User, Sparkles, Crosshair, Wand2, MessageSquare,
}

// ─── 后端返回数据类型 ──────────────────────────────────────────────────────

/** 后端 /api/synthesis/modes 返回的单条模式 */
type RawModeGroup = {
  id: string
  name: string
  icon_name: string
  description: string
  default_model: string
  needs_reference: boolean
  reference_label?: string
  reference_hint?: string
  needs_roi: boolean
  needs_prompt: boolean
  prompt_required?: boolean
  prompt_label?: string
  prompt_hint?: string
}

/** 前端运行时模式配置（icon_name 解析为 Icon 组件） */
type ModeGroup = {
  id: SynthesisTagValue
  name: string
  Icon: LucideIcon
  description: string
  defaultModel: string
  needsReference: boolean
  referenceLabel?: string
  referenceHint?: string
  needsROI: boolean
  needsPrompt: boolean
  promptRequired?: boolean
  promptLabel?: string
  promptHint?: string
}

/** 后端 /api/synthesis/models 返回的单条模型 */
type SynthesisModel = {
  id: string
  name: string
  tags: string[]
  description: string
  badge: string
}

// ─── 工具函数 ──────────────────────────────────────────────────────────────

function parseModeGroup(raw: RawModeGroup): ModeGroup {
  return {
    id: raw.id as SynthesisTagValue,
    name: raw.name,
    Icon: ICON_MAP[raw.icon_name] ?? Image,
    description: raw.description,
    defaultModel: raw.default_model,
    needsReference: raw.needs_reference,
    referenceLabel: raw.reference_label,
    referenceHint: raw.reference_hint,
    needsROI: raw.needs_roi,
    needsPrompt: raw.needs_prompt,
    promptRequired: raw.prompt_required,
    promptLabel: raw.prompt_label,
    promptHint: raw.prompt_hint,
  }
}

function modeModels(models: SynthesisModel[], modeId: string) {
  return models.filter((m) => m.tags.includes(modeId))
}

function defaultModel(models: SynthesisModel[], modeGroup: ModeGroup | undefined) {
  if (!modeGroup) return ''
  const candidates = modeModels(models, modeGroup.id)
  // 优先使用后端 default_model 字段
  if (candidates.find((m) => m.id === modeGroup.defaultModel)) return modeGroup.defaultModel
  return candidates.find((m) => m.badge === '推荐')?.id ?? candidates[0]?.id ?? ''
}

// ─── 组件 ─────────────────────────────────────────────────────────────────

export default function SmartSynthesis() {
  const {
    sourceImage, resultImage, rois, selectedROIs, imageWidth, imageHeight,
    setSourceImage, setImageDimensions, setResultImage,
    addROI, removeROI, clearROIs, selectROI,
  } = useImageStore()

  const { isProcessing, progress, statusMessage, startProcess, finishProcess, setError, reset } = useProcessStore()
  const { device, setDevice } = useSettingsStore()
  const { runSynthesis, getSynthesisModes, getSynthesisModels } = useBackendAPI()
  const { options: deviceOptions } = useDeviceOptions()
  const downloadTasks = useDownloadStore((s) => s.tasks)
  const { startDownload } = useDownloadManager()

  const [tool, setTool] = useState<CanvasTool>('draw')
  const [showResult, setShowResult] = useState(false)
  const [showInfo, setShowInfo] = useState(false)

  // ── 动态加载的模式与模型 ─────────────────────────────────────────────────
  const [modeGroups, setModeGroups] = useState<ModeGroup[]>([])
  const [allModels, setAllModels] = useState<SynthesisModel[]>([])
  const [configLoading, setConfigLoading] = useState(true)

  // 合成参数
  const [activeMode, setActiveMode] = useState<SynthesisTagValue>('' as SynthesisTagValue)
  const [selectedModel, setSelectedModel] = useState('')
  const [prompt, setPrompt] = useState('')

  // 参考图
  const [referenceImage, setReferenceImage] = useState<string | null>(null)
  const [referenceFileName, setReferenceFileName] = useState('')

  const sourceFileInputRef = useRef<HTMLInputElement>(null)
  const referenceFileInputRef = useRef<HTMLInputElement>(null)

  // ── 加载模式与模型配置 ────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [modesResp, modelsResp] = await Promise.all([
          getSynthesisModes(),
          getSynthesisModels(),
        ])
        if (cancelled) return
        const parsedModes: ModeGroup[] = (modesResp.modes ?? [])
          .map(parseModeGroup)
          .filter(mode => mode.id !== 'upscale' && mode.id !== 'watermark_removal')  // 超分辨率和去水印有专门页签，从智能合成中移除
        const parsedModels: SynthesisModel[] = modelsResp.models ?? []
        setModeGroups(parsedModes)
        setAllModels(parsedModels)
        // 初始化默认模型
        const firstMode = parsedModes[0]
        if (firstMode) {
          setActiveMode(firstMode.id)
          setSelectedModel(defaultModel(parsedModels, firstMode))
        }
      } catch (err) {
        if (!cancelled) showToast('error', '获取模型配置失败，请检查后端连接')
      } finally {
        if (!cancelled) setConfigLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  const activeModeConfig = modeGroups.find((m) => m.id === activeMode)
  const availableModels = modeModels(allModels, activeMode)

  // 切换模式时重置模型选择
  useEffect(() => {
    if (!activeModeConfig) return
    setSelectedModel(defaultModel(allModels, activeModeConfig))
  }, [activeMode])  // eslint-disable-line react-hooks/exhaustive-deps

  // ── 图片加载工具 ─────────────────────────────────────────────────────────

  const loadImageAsDataUrl = (file: File): Promise<string> =>
    new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result as string)
      reader.onerror = reject
      reader.readAsDataURL(file)
    })

  const loadSourceFromPath = async (filePath: string) => {
    try {
      const dataUrl = await window.electronAPI!.readImageFile(filePath)
      reset()
      setSourceImage(dataUrl, filePath)
      setShowResult(false)
    } catch (err: any) {
      showToast('error', `读取主图失败: ${err.message}`)
    }
  }

  const loadReferenceFromPath = async (filePath: string) => {
    try {
      const dataUrl = await window.electronAPI!.readImageFile(filePath)
      setReferenceImage(dataUrl)
      setReferenceFileName(filePath.split(/[\\/]/).pop() ?? filePath)
    } catch (err: any) {
      showToast('error', `读取参考图失败: ${err.message}`)
    }
  }

  // ── 主图上传 ─────────────────────────────────────────────────────────────

  const handleOpenSource = async () => {
    if (window.electronAPI) {
      const filePath = await window.electronAPI.openFile()
      if (filePath) loadSourceFromPath(filePath)
    } else {
      sourceFileInputRef.current?.click()
    }
  }

  const handleSourceFileInput = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const electronFile = file as File & { path?: string }
    if (window.electronAPI && electronFile.path) {
      loadSourceFromPath(electronFile.path)
      return
    }
    const dataUrl = await loadImageAsDataUrl(file)
    reset()
    setSourceImage(dataUrl)
    setShowResult(false)
  }

  const handleSourceDrop = async (e: React.DragEvent) => {
    e.preventDefault()
    const file = e.dataTransfer.files[0]
    if (!file || !file.type.startsWith('image/')) return
    const electronFile = file as File & { path?: string }
    if (window.electronAPI && electronFile.path) {
      loadSourceFromPath(electronFile.path)
      return
    }
    const dataUrl = await loadImageAsDataUrl(file)
    reset()
    setSourceImage(dataUrl)
    setShowResult(false)
  }

  // ── 参考图上传 ────────────────────────────────────────────────────────────

  const handleOpenReference = async () => {
    if (window.electronAPI) {
      const filePath = await window.electronAPI.openFile()
      if (filePath) loadReferenceFromPath(filePath)
    } else {
      referenceFileInputRef.current?.click()
    }
  }

  const handleReferenceFileInput = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const electronFile = file as File & { path?: string }
    if (window.electronAPI && electronFile.path) {
      loadReferenceFromPath(electronFile.path)
      return
    }
    const dataUrl = await loadImageAsDataUrl(file)
    setReferenceImage(dataUrl)
    setReferenceFileName(file.name)
  }

  const handleReferenceDrop = async (e: React.DragEvent) => {
    e.preventDefault()
    const file = e.dataTransfer.files[0]
    if (!file || !file.type.startsWith('image/')) return
    const electronFile = file as File & { path?: string }
    if (window.electronAPI && electronFile.path) {
      loadReferenceFromPath(electronFile.path)
      return
    }
    const dataUrl = await loadImageAsDataUrl(file)
    setReferenceImage(dataUrl)
    setReferenceFileName(file.name)
  }

  // ── ROI 操作 ─────────────────────────────────────────────────────────────

  const handleROIDrawn = useCallback((roi: Omit<ROI, 'id'>) => { addROI(roi) }, [addROI])
  const handleROISelect = useCallback((id: string) => { selectROI(id) }, [selectROI])
  const handleImageLoad = useCallback((w: number, h: number) => { setImageDimensions(w, h) }, [setImageDimensions])

  // ── 执行合成 ──────────────────────────────────────────────────────────────

  const handleProcess = async () => {
    if (!sourceImage) return
    if (activeModeConfig?.needsReference && !referenceImage) {
      showToast('warning', `请先上传${activeModeConfig.referenceLabel}`)
      return
    }
    if (activeModeConfig?.needsROI && rois.length === 0) {
      showToast('warning', '请先在画布上框选处理区域（ROI）')
      return
    }
    if (activeModeConfig?.promptRequired && !prompt.trim()) {
      showToast('warning', `请输入${activeModeConfig.promptLabel ?? '提示词'}`)
      return
    }

    // 检查模型下载状态
    const dlTask = downloadTasks[selectedModel]
    if (dlTask?.status === DownloadStatus.DOWNLOADING || dlTask?.status === DownloadStatus.QUEUED) {
      showToast('info', `模型${dlTask.status === DownloadStatus.QUEUED ? '排队中，' : ''}下载中，请稍候...`)
      return
    }
    if (dlTask?.status === ModelStatus.MISSING || dlTask?.status === DownloadStatus.ERROR) {
      showToast('info', '模型未下载，已自动加入下载队列，完成后可继续操作')
      startDownload(selectedModel)
      return
    }

    try {
      startProcess(selectedModel)

      const roiList = rois.length > 0 ? rois.map((r) => [r.x1, r.y1, r.x2, r.y2]) : undefined

      const result = await runSynthesis({
        source_image: sourceImage,
        reference_image: referenceImage ?? undefined,
        rois: roiList,
        mode: activeMode,
        model_id: selectedModel,
        device,
        prompt,
      })

      setResultImage(`data:image/png;base64,${result.image}`)
      setShowResult(true)
      finishProcess('合成完成')
      showToast('success', `智能合成完成 (${result.width}×${result.height})`)
    } catch (err: any) {
      setError(err.message)
      showToast('error', err.message)
    }
  }

  // ── 保存 ─────────────────────────────────────────────────────────────────

  const handleSave = async () => {
    if (!resultImage) return
    const fileName = `synthesis_${activeMode}.png`
    if (window.electronAPI) {
      const filePath = await window.electronAPI.saveFile(fileName)
      if (filePath) {
        const res = await window.electronAPI.saveImageFile(filePath, resultImage)
        if (res.success) showToast('success', `已保存至: ${filePath}`)
        else showToast('error', `保存失败: ${res.error}`)
      }
    } else {
      const a = document.createElement('a')
      a.href = resultImage
      a.download = fileName
      a.click()
    }
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }

  const needsPrompt = activeModeConfig?.needsPrompt ?? false

  // ──────────────────────────────────────────────────────────────────────────

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <PageHeader
        title="智能合成"
        subtitle={imageWidth > 0 ? `${imageWidth} × ${imageHeight}` : undefined}
        right={<span className="text-xs text-fg-secondary">{activeModeConfig?.name ?? '...'} | {device}</span>}
      />

      <div className="flex-1 flex overflow-hidden min-h-0">
        {/* ── 画布区域 ── */}
        <div
          className="flex-1 bg-[#1a1a1a] relative"
          onDrop={handleSourceDrop}
          onDragOver={handleDragOver}
        >
          {!sourceImage ? (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 text-fg-secondary">
              <Upload size={48} strokeWidth={1} />
              <p className="text-sm">拖拽主图到此处，或点击打开</p>
              <Button onClick={handleOpenSource}>打开主图</Button>
              <input ref={sourceFileInputRef} type="file" accept="image/*" className="hidden" onChange={handleSourceFileInput} />
            </div>
          ) : showResult && resultImage && sourceImage ? (
            <ImageCompare
              beforeSrc={sourceImage}
              afterSrc={resultImage}
              beforeLabel="原图"
              afterLabel="合成结果"
            />
          ) : (
            <>
              <ImageCanvas
                imageSrc={sourceImage}
                rois={rois}
                selectedROIs={selectedROIs}
                tool={tool}
                onROIDrawn={handleROIDrawn}
                onROISelect={handleROISelect}
                onImageLoad={handleImageLoad}
              />
              {/* 悬浮工具栏 */}
              <div className="absolute bottom-3 left-1/2 -translate-x-1/2 flex gap-1 bg-bg-secondary rounded-lg p-1 border border-border-subtle shadow-lg">
                <button
                  onClick={() => setTool('draw')}
                  className={clsx('w-8 h-8 rounded flex items-center justify-center transition-colors', tool === 'draw' ? 'bg-bg-active text-fg-accent' : 'hover:bg-bg-hover text-fg-secondary')}
                  title="绘制处理区域 (ROI)"
                >
                  <Square size={16} />
                </button>
                <button
                  onClick={() => setTool('pan')}
                  className={clsx('w-8 h-8 rounded flex items-center justify-center transition-colors', tool === 'pan' ? 'bg-bg-active text-fg-accent' : 'hover:bg-bg-hover text-fg-secondary')}
                  title="平移"
                >
                  <Move size={16} />
                </button>
                <div className="w-px bg-border-subtle mx-0.5" />
                <button
                  onClick={handleOpenSource}
                  className="w-8 h-8 rounded flex items-center justify-center hover:bg-bg-hover text-fg-secondary transition-colors"
                  title="更换主图"
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
            </>
          )}
          <input ref={sourceFileInputRef} type="file" accept="image/*" className="hidden" onChange={handleSourceFileInput} />
        </div>

        {/* ── 控制面板 ── */}
        <div className="w-[260px] bg-bg-secondary border-l border-border-subtle p-3 flex flex-col gap-3 overflow-y-auto min-h-0">

          {/* 模式选择 */}
          <section>
            <h3 className="text-xs uppercase tracking-wider text-fg-secondary mb-2">合成模式</h3>
            {configLoading ? (
              <div className="grid grid-cols-2 gap-1.5">
                {Array.from({ length: 6 }).map((_, i) => (
                  <div key={i} className="h-[58px] rounded-lg bg-bg-primary border border-border-subtle animate-pulse" />
                ))}
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-1.5">
                {modeGroups.map(({ id, name, Icon }) => (
                  <button
                    key={id}
                    onClick={() => setActiveMode(id)}
                    className={clsx(
                      'flex flex-col items-center gap-1 py-2.5 px-1 rounded-lg border text-xs transition-all',
                      activeMode === id
                        ? 'border-border-focus bg-bg-active text-fg-accent'
                        : 'border-border-subtle bg-bg-primary text-fg-secondary hover:bg-bg-hover hover:text-fg-primary'
                    )}
                  >
                    <Icon size={18} strokeWidth={1.5} />
                    <span className="font-medium leading-tight text-center">{name}</span>
                  </button>
                ))}
              </div>
            )}

            {/* 模式说明折叠 */}
            <button
              className="flex items-center gap-1 mt-2 text-xs text-fg-secondary w-full"
              onClick={() => setShowInfo(!showInfo)}
            >
              <Info size={11} />
              <span>{activeModeConfig?.description ?? ''}</span>
              {showInfo ? <ChevronDown size={11} className="ml-auto" /> : <ChevronRight size={11} className="ml-auto" />}
            </button>
          </section>

          {/* 参考图上传 */}
          {activeModeConfig?.needsReference && (
            <section>
              <h3 className="text-xs uppercase tracking-wider text-fg-secondary mb-2">
                {activeModeConfig.referenceLabel}
                <span className="ml-1 text-status-error">*</span>
              </h3>
              <div
                className={clsx(
                  'relative rounded-lg border-2 border-dashed transition-colors cursor-pointer overflow-hidden',
                  referenceImage
                    ? 'border-border-focus'
                    : 'border-border-subtle hover:border-border-focus'
                )}
                style={{ aspectRatio: '16/9', minHeight: '72px' }}
                onClick={handleOpenReference}
                onDrop={handleReferenceDrop}
                onDragOver={handleDragOver}
              >
                {referenceImage ? (
                  <>
                    <img
                      src={referenceImage}
                      alt="参考图"
                      className="w-full h-full object-cover"
                    />
                    <div className="absolute inset-0 bg-black/40 opacity-0 hover:opacity-100 transition-opacity flex items-center justify-center">
                      <span className="text-white text-xs">点击更换</span>
                    </div>
                    {referenceFileName && (
                      <div className="absolute bottom-0 left-0 right-0 bg-black/60 px-2 py-0.5">
                        <span className="text-white text-[10px] truncate block">{referenceFileName}</span>
                      </div>
                    )}
                  </>
                ) : (
                  <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 text-fg-secondary">
                    <Upload size={20} strokeWidth={1.5} />
                    <span className="text-[10px] text-center px-2">{activeModeConfig.referenceHint}</span>
                  </div>
                )}
              </div>
              <input ref={referenceFileInputRef} type="file" accept="image/*" className="hidden" onChange={handleReferenceFileInput} />
              {referenceImage && (
                <button
                  onClick={() => { setReferenceImage(null); setReferenceFileName('') }}
                  className="mt-1 text-[10px] text-fg-secondary hover:text-status-error flex items-center gap-1"
                >
                  <Trash2 size={10} /> 清除参考图
                </button>
              )}
            </section>
          )}

          {/* ROI 区域列表 */}
          <section>
            <h3 className="text-xs uppercase tracking-wider text-fg-secondary mb-2">
              处理区域
              <span className="ml-1 text-fg-secondary font-normal normal-case">（不选则全图）</span>
            </h3>
            <div className="bg-bg-primary rounded border border-border-subtle p-1 min-h-[48px] max-h-[120px] overflow-y-auto">
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
                    <span>区域 {i + 1} ({roi.x2 - roi.x1}×{roi.y2 - roi.y1})</span>
                    <button
                      onClick={(e) => { e.stopPropagation(); removeROI(roi.id) }}
                      className="text-fg-secondary hover:text-status-error"
                    >
                      <Trash2 size={11} />
                    </button>
                  </div>
                ))
              )}
            </div>
            {rois.length > 0 && (
              <button
                onClick={clearROIs}
                className="mt-1 text-[10px] text-fg-secondary hover:text-status-error flex items-center gap-1"
              >
                <Trash2 size={10} /> 清除全部区域
              </button>
            )}
          </section>

          {/* ROI 必填提示（精准替换模式）*/}
          {activeModeConfig?.needsROI && rois.length === 0 && (
            <div className="bg-status-warning/10 border border-status-warning/30 rounded px-2 py-1.5 text-[10px] text-status-warning flex items-start gap-1.5">
              <Square size={11} className="mt-0.5 shrink-0" />
              <span>请在左侧画布上拖拽框选要替换的区域</span>
            </div>
          )}

          {/* 模型选择 */}
          <section>
            <h3 className="text-xs uppercase tracking-wider text-fg-secondary mb-2">模型</h3>
            <div className="space-y-1.5">
              {availableModels.map((model) => {
                const dlTask = downloadTasks[model.id]
                const isSelected = selectedModel === model.id
                const isOk = dlTask?.status === ModelStatus.OK || dlTask?.status === DownloadStatus.DONE
                const isMissing = dlTask?.status === ModelStatus.MISSING
                const isDownloading = dlTask?.status === DownloadStatus.DOWNLOADING
                const isQueued = dlTask?.status === DownloadStatus.QUEUED
                const isError = dlTask?.status === DownloadStatus.ERROR
                return (
                <button
                  key={model.id}
                  onClick={() => setSelectedModel(model.id)}
                  className={clsx(
                    'w-full text-left p-2.5 rounded-lg border transition-all',
                    isSelected
                      ? 'border-border-focus bg-bg-active'
                      : 'border-border-subtle bg-bg-primary hover:bg-bg-hover'
                  )}
                >
                  <div className="flex items-center justify-between mb-0.5">
                    <span className={clsx(
                      'text-xs font-medium',
                      isSelected ? 'text-fg-accent' : 'text-fg-primary'
                    )}>
                      {model.name}
                    </span>
                    <div className="flex items-center gap-1.5">
                      {isDownloading && <Loader2 size={10} className="animate-spin text-blue-400" />}
                      {isQueued     && <Clock size={10} className="text-orange-400" />}
                      {isError      && <XCircle size={10} className="text-red-400" />}
                      {isOk         && <CheckCircle2 size={10} className="text-green-400" />}
                      {isMissing    && <Download size={10} className="text-fg-secondary/60" />}
                      {model.badge && (
                        <span className={clsx(
                          'text-[9px] px-1.5 py-0.5 rounded-full font-medium',
                          model.badge === '推荐'
                            ? 'bg-fg-accent/20 text-fg-accent'
                            : model.badge === '快速'
                              ? 'bg-status-success/20 text-status-success'
                              : model.badge === '高质量'
                                ? 'bg-purple-500/20 text-purple-400'
                                : model.badge === '动漫'
                                  ? 'bg-pink-500/20 text-pink-400'
                                  : 'bg-border-subtle text-fg-secondary'
                        )}>
                          {model.badge}
                        </span>
                      )}
                    </div>
                  </div>
                  <p className="text-[10px] text-fg-secondary leading-snug">{model.description}</p>
                  {/* 下载进度（仅选中且下载中/排队时显示） */}
                  {isSelected && (isDownloading || isQueued) && (
                    <div className={clsx(
                      'mt-1.5 text-[10px] flex items-center gap-1.5',
                      isDownloading ? 'text-blue-400' : 'text-orange-400',
                    )}>
                      {isDownloading && (
                        <>
                          {dlTask.speed && <span className="font-mono">{dlTask.speed}</span>}
                          {dlTask.downloaded && dlTask.totalSize && (
                            <span className="font-mono">{dlTask.downloaded} / {dlTask.totalSize}</span>
                          )}
                          {dlTask.message && !dlTask.speed && <span>{dlTask.message}</span>}
                        </>
                      )}
                      {isQueued && <span>排队中，第 {dlTask.position} 位</span>}
                    </div>
                  )}
                </button>
                )
              })}
            </div>
          </section>

          {/* SD 文字引导（可选）*/}
          {needsPrompt && (
            <section>
              <h3 className="text-xs uppercase tracking-wider text-fg-secondary mb-2">
                {activeModeConfig?.promptLabel ?? '提示词'}
                {activeModeConfig?.promptRequired
                  ? <span className="ml-1 text-status-error">*</span>
                  : <span className="normal-case text-fg-secondary font-normal"> （可选）</span>
                }
              </h3>
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder={activeModeConfig?.promptHint ?? '描述你想要的目标效果'}
                className="w-full bg-bg-primary border border-border-subtle rounded text-xs text-fg-primary p-2 resize-none focus:border-border-focus focus:outline-none"
                rows={3}
              />
            </section>
          )}

          {/* 设备选择 */}
          <Select
            label="设备"
            value={device}
            onChange={(e) => setDevice(e.target.value)}
            size="sm"
            options={deviceOptions}
          />

          {/* 操作按钮 */}
          <div className="mt-auto pt-2 space-y-2">
            {(() => {
              const dlTask = downloadTasks[selectedModel]
              const isModelBusy = dlTask?.status === DownloadStatus.DOWNLOADING || dlTask?.status === DownloadStatus.QUEUED
              return (
                <Button
                  onClick={handleProcess}
                  disabled={
                    isProcessing
                    || !sourceImage
                    || (activeModeConfig?.needsReference && !referenceImage)
                    || (activeModeConfig?.needsROI && rois.length === 0)
                    || (activeModeConfig?.promptRequired && !prompt.trim())
                    || isModelBusy
                  }
                  loading={isProcessing}
                  className="w-full"
                  size="lg"
                >
                  {isProcessing
                    ? statusMessage
                    : dlTask?.status === DownloadStatus.DOWNLOADING
                      ? '等待下载...'
                      : dlTask?.status === DownloadStatus.QUEUED
                        ? `排队中 #${dlTask.position}`
                        : (dlTask?.status === ModelStatus.MISSING || dlTask?.status === DownloadStatus.ERROR)
                          ? '点击下载并处理'
                          : `执行${activeModeConfig?.name ?? ''}`}
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
