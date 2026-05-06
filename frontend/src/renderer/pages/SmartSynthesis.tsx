import { useState, useRef, useEffect, useCallback } from 'react'
import {
  Upload, Download, Square, Move, Trash2, RotateCcw,
  Image, Shirt, User, Sparkles, Info, ChevronDown, ChevronRight,
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
import type { ROI } from '../stores/useImageStore'

type CanvasTool = 'draw' | 'pan'

// ─── 模式配置（与后端 SYNTHESIS_MODE_GROUPS 对应）─────────────────────────
const MODE_GROUPS = [
  {
    id: 'background_replace',
    name: '换背景',
    Icon: Image,
    description: '智能抠图后替换背景，支持全图或指定区域',
    needsReference: true,
    referenceLabel: '新背景图',
    referenceHint: '拖拽或点击上传新背景图片',
  },
  {
    id: 'outfit_swap',
    name: '换装模拟',
    Icon: Shirt,
    description: '在指定区域替换服装纹理，边缘自然融合',
    needsReference: true,
    referenceLabel: '目标服装图',
    referenceHint: '上传想要替换的目标服装图片',
  },
  {
    id: 'face_swap',
    name: '换脸模拟',
    Icon: User,
    description: '替换人脸区域，搭配 GFPGAN 增强细节（仅限合法创作）',
    needsReference: true,
    referenceLabel: '目标人脸图',
    referenceHint: '上传目标人物的人脸图片',
  },
  {
    id: 'virtual_tryon',
    name: '虚拟试穿',
    Icon: Sparkles,
    description: '将服装自然叠合到人物身上（AI 近似方案）',
    needsReference: true,
    referenceLabel: '服装图',
    referenceHint: '上传想要试穿的服装图片',
  },
]

// ─── 模型配置（与后端 SYNTHESIS_MODELS 对应）─────────────────────────────
const ALL_MODELS = [
  // 换背景
  {
    id: 'rmbg',
    name: 'RMBG 2.0',
    tags: ['background_replace'],
    description: 'BRIA RMBG 2.0 —— SOTA 抠图，边缘精细',
    badge: '推荐',
  },
  {
    id: 'u2net',
    name: 'U²-Net',
    tags: ['background_replace'],
    description: '通用显著目标检测，支持人像/商品/动物',
    badge: '',
  },
  {
    id: 'modnet',
    name: 'MODNet',
    tags: ['background_replace'],
    description: '实时人像抠图，速度快',
    badge: '快速',
  },
  // 换装 / 换脸
  {
    id: 'lama_inpaint',
    name: 'LaMa（区域替换）',
    tags: ['outfit_swap', 'face_swap'],
    description: '大感受野修复，纹理延续自然，适合换装',
    badge: '推荐',
  },
  {
    id: 'sd15',
    name: 'Stable Diffusion 1.5',
    tags: ['outfit_swap', 'face_swap', 'virtual_tryon'],
    description: '文字引导 Inpainting，可描述目标样式',
    badge: 'SD',
  },
  {
    id: 'powerpaint',
    name: 'PowerPaint v2',
    tags: ['outfit_swap', 'virtual_tryon'],
    description: '多任务 Inpainting，换装效果自然',
    badge: '',
  },
  // 换脸
  {
    id: 'gfpgan',
    name: 'GFPGAN v1.4',
    tags: ['face_swap'],
    description: '人脸生成对抗网络，专精人脸修复与增强',
    badge: '推荐',
  },
]

// ─── 工具函数 ──────────────────────────────────────────────────────────────

function modeModels(modeId: string) {
  return ALL_MODELS.filter((m) => m.tags.includes(modeId))
}

function defaultModel(modeId: string) {
  const models = modeModels(modeId)
  return models.find((m) => m.badge === '推荐')?.id ?? models[0]?.id ?? ''
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
  const { runSynthesis } = useBackendAPI()

  const [tool, setTool] = useState<CanvasTool>('draw')
  const [showResult, setShowResult] = useState(false)
  const [showInfo, setShowInfo] = useState(false)

  // 合成参数
  const [activeMode, setActiveMode] = useState('background_replace')
  const [selectedModel, setSelectedModel] = useState(defaultModel('background_replace'))
  const [prompt, setPrompt] = useState('')

  // 参考图
  const [referenceImage, setReferenceImage] = useState<string | null>(null)
  const [referenceFileName, setReferenceFileName] = useState('')

  const sourceFileInputRef = useRef<HTMLInputElement>(null)
  const referenceFileInputRef = useRef<HTMLInputElement>(null)

  const activeModeConfig = MODE_GROUPS.find((m) => m.id === activeMode)!
  const availableModels = modeModels(activeMode)

  // 切换模式时重置模型选择
  useEffect(() => {
    setSelectedModel(defaultModel(activeMode))
  }, [activeMode])

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
    if (activeModeConfig.needsReference && !referenceImage) {
      showToast('warning', `请先上传${activeModeConfig.referenceLabel}`)
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

  const needsPrompt = selectedModel === 'sd15' || selectedModel === 'powerpaint'

  // ──────────────────────────────────────────────────────────────────────────

  return (
    <div className="flex-1 flex flex-col">
      <PageHeader
        title="智能合成"
        subtitle={imageWidth > 0 ? `${imageWidth} × ${imageHeight}` : undefined}
        right={<span className="text-xs text-fg-secondary">{activeModeConfig.name} | {device}</span>}
      />

      <div className="flex-1 flex overflow-hidden">
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
        <div className="w-[260px] bg-bg-secondary border-l border-border-subtle p-3 flex flex-col gap-3 overflow-y-auto">

          {/* 模式选择 */}
          <section>
            <h3 className="text-xs uppercase tracking-wider text-fg-secondary mb-2">合成模式</h3>
            <div className="grid grid-cols-2 gap-1.5">
              {MODE_GROUPS.map(({ id, name, Icon }) => (
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

            {/* 模式说明折叠 */}
            <button
              className="flex items-center gap-1 mt-2 text-xs text-fg-secondary w-full"
              onClick={() => setShowInfo(!showInfo)}
            >
              <Info size={11} />
              <span>{activeModeConfig.description}</span>
              {showInfo ? <ChevronDown size={11} className="ml-auto" /> : <ChevronRight size={11} className="ml-auto" />}
            </button>
          </section>

          {/* 参考图上传 */}
          {activeModeConfig.needsReference && (
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

          {/* 模型选择 */}
          <section>
            <h3 className="text-xs uppercase tracking-wider text-fg-secondary mb-2">模型</h3>
            <div className="space-y-1.5">
              {availableModels.map((model) => (
                <button
                  key={model.id}
                  onClick={() => setSelectedModel(model.id)}
                  className={clsx(
                    'w-full text-left p-2.5 rounded-lg border transition-all',
                    selectedModel === model.id
                      ? 'border-border-focus bg-bg-active'
                      : 'border-border-subtle bg-bg-primary hover:bg-bg-hover'
                  )}
                >
                  <div className="flex items-center justify-between mb-0.5">
                    <span className={clsx(
                      'text-xs font-medium',
                      selectedModel === model.id ? 'text-fg-accent' : 'text-fg-primary'
                    )}>
                      {model.name}
                    </span>
                    {model.badge && (
                      <span className={clsx(
                        'text-[9px] px-1.5 py-0.5 rounded-full font-medium',
                        model.badge === '推荐'
                          ? 'bg-fg-accent/20 text-fg-accent'
                          : model.badge === '快速'
                            ? 'bg-status-success/20 text-status-success'
                            : 'bg-border-subtle text-fg-secondary'
                      )}>
                        {model.badge}
                      </span>
                    )}
                  </div>
                  <p className="text-[10px] text-fg-secondary leading-snug">{model.description}</p>
                </button>
              ))}
            </div>
          </section>

          {/* SD 文字引导（可选）*/}
          {needsPrompt && (
            <section>
              <h3 className="text-xs uppercase tracking-wider text-fg-secondary mb-2">
                文字引导 <span className="normal-case text-fg-secondary font-normal">（可选）</span>
              </h3>
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="描述你想要的目标效果，例如：a red dress with floral patterns"
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
            options={[
              { value: 'mps', label: 'MPS (Apple Silicon)' },
              { value: 'cpu', label: 'CPU' },
              { value: 'cuda', label: 'CUDA (NVIDIA)' },
            ]}
          />

          {/* 操作按钮 */}
          <div className="mt-auto pt-2 space-y-2">
            <Button
              onClick={handleProcess}
              disabled={isProcessing || !sourceImage || (activeModeConfig.needsReference && !referenceImage)}
              loading={isProcessing}
              className="w-full"
              size="lg"
            >
              {isProcessing ? statusMessage : `执行${activeModeConfig.name}`}
            </Button>

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
