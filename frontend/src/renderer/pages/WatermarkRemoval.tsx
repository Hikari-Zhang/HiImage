import { useState, useCallback, useRef, useEffect } from 'react'
import { Upload, Wand2, Trash2, Download, ZoomIn, ZoomOut, Move, Square, RotateCcw, ChevronDown, ChevronRight, RefreshCw } from 'lucide-react'
import { clsx } from 'clsx'
import ImageCanvas from '../components/ImageCanvas'
import ImageCompare from '../components/ImageCompare'
import PageHeader from '../components/layout/PageHeader'
import { Button, Select, Slider, Progress, showToast } from '../components/ui'
import ModelSelect from '../components/ModelSelect/ModelSelect'
import { useImageStore } from '../stores/useImageStore'
import { useSettingsStore } from '../stores/useSettingsStore'
import { useModelStore } from '../stores/useModelStore'
import { useProcessStore } from '../stores/useProcessStore'
import { useBackendAPI } from '../hooks/useBackendAPI'
import { useDeviceOptions } from '../hooks/useDeviceOptions'
import { useDownloadStore } from '../stores/useDownloadStore'
import { useDownloadManager } from '../hooks/useDownloadManager'
import type { ROI } from '../stores/useImageStore'

type CanvasTool = 'draw' | 'pan'

// 后处理方法选项
const POSTPROCESS_OPTIONS = [
  { value: 'none', label: '无后处理' },
  { value: 'poisson', label: 'Poisson 融合（修边缘）' },
  { value: 'lama_refine', label: 'LaMa 二次精修' },
  { value: 'gfpgan', label: 'GFPGAN（人脸专用）' },
]

export default function WatermarkRemoval() {
  const {
    sourceImage, resultImage, rois, selectedROIs, imageWidth, imageHeight,
    setSourceImage, setImageDimensions, setResultImage,
    addROI, removeROI, clearROIs, selectROI,
  } = useImageStore()

  const { isProcessing, progress, statusMessage, startProcess, updateProgress, finishProcess, setError, reset } = useProcessStore()
  const { detectWatermark, inpaint, runPipeline } = useBackendAPI()
  const {
    device, inpaintModel, defaultDilation, sensitivity, disableNsfw,
    postprocessMethod, postprocessEnabled,
    upscaleModel, upscaleEnabled,
    setDevice, setInpaintModel, setDilation, setSensitivity,
    setPostprocessMethod, setPostprocessEnabled, setUpscaleEnabled, setUpscaleModel,
  } = useSettingsStore()
  const { inpaintGroups, upscaleGroups } = useModelStore()
  const { options: deviceOptions } = useDeviceOptions()
  const downloadTasks = useDownloadStore((s) => s.tasks)
  const { startDownload } = useDownloadManager()

  const [tool, setTool] = useState<CanvasTool>('draw')
  const [showResult, setShowResult] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const wsRef = useRef<WebSocket | null>(null)

  // WebSocket listener for progress updates
  useEffect(() => {
    const connectProgressWS = async () => {
      try {
        const backendURL = window.electronAPI
          ? await window.electronAPI.getBackendURL()
          : 'http://127.0.0.1:8787'
        const wsURL = backendURL.replace('http', 'ws') + '/api/ws/progress'

        const ws = new WebSocket(wsURL)
        ws.onmessage = (event) => {
          try {
            const msg = JSON.parse(event.data)
            if (msg.type === 'progress' && typeof msg.percent === 'number') {
              updateProgress(msg.percent, msg.message || '')
            } else if (msg.type === 'complete') {
              finishProcess(msg.message || '处理完成')
            } else if (msg.type === 'error') {
              setError(msg.message || '处理出错')
            }
          } catch (err) {
            console.error('Failed to parse progress message:', err)
          }
        }
        ws.onerror = () => {
          console.error('Progress WebSocket error')
        }
        ws.onclose = () => {
          // Reconnect after 3s
          setTimeout(connectProgressWS, 3000)
        }
        wsRef.current = ws
      } catch (err) {
        console.error('Failed to connect progress WebSocket:', err)
        setTimeout(connectProgressWS, 3000)
      }
    }

    connectProgressWS()
    return () => {
      wsRef.current?.close()
    }
  }, [updateProgress, finishProcess, setError])


  // Open file
  const handleOpenFile = async () => {
    if (window.electronAPI) {
      const filePath = await window.electronAPI.openFile()
      if (filePath) {
        loadImageFromPath(filePath)
      }
    } else {
      fileInputRef.current?.click()
    }
  }

  const loadImageFromPath = async (filePath: string) => {
    try {
      // 通过 IPC 读取文件内容为 base64 data URL，避免 file:// 协议权限问题
      const dataUrl = await window.electronAPI!.readImageFile(filePath)
      reset()
      setSourceImage(dataUrl, filePath)
      setShowResult(false)
    } catch (err: any) {
      showToast('error', `读取图片失败: ${err.message}`)
    }
  }

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    // Electron 环境下 File 对象有 path 属性，走文件路径加载可保留 sourceFilePath
    const electronFile = file as File & { path?: string }
    if (window.electronAPI && electronFile.path) {
      loadImageFromPath(electronFile.path)
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      reset()
      setSourceImage(reader.result as string)
      setShowResult(false)
    }
    reader.readAsDataURL(file)
  }

  // Drag & drop
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const file = e.dataTransfer.files[0]
    if (!file || !file.type.startsWith('image/')) return
    // Electron 环境下 File 对象有 path 属性
    const electronFile = file as File & { path?: string }
    if (window.electronAPI && electronFile.path) {
      loadImageFromPath(electronFile.path)
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      reset()
      setSourceImage(reader.result as string)
      setShowResult(false)
    }
    reader.readAsDataURL(file)
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
    setImageDimensions(w, h)
  }, [setImageDimensions])

  // Auto detect
  const handleAutoDetect = async () => {
    if (!sourceImage) return
    try {
      startProcess('detect')
      const result = await detectWatermark(sourceImage, sensitivity / 100)
      result.regions.forEach(([x1, y1, x2, y2]) => {
        addROI({ x1, y1, x2, y2 })
      })
      finishProcess(`检测到 ${result.regions.length} 个区域`)
      if (result.regions.length === 0) {
        showToast('info', '未检测到水印区域，请尝试调整敏感度或手动绘制')
      }
    } catch (err: any) {
      setError(err.message)
      showToast('error', err.message)
    }
  }

  // Process
  const handleProcess = async () => {
    if (!sourceImage || rois.length === 0) return

    // 检查当前模型的下载状态
    const task = downloadTasks[inpaintModel]
    if (task?.status === 'downloading' || task?.status === 'queued') {
      showToast('info', `模型${task.status === 'queued' ? '排队中，' : ''}下载中，请稍候...`)
      return
    }
    if (task?.status === 'missing' || task?.status === 'error') {
      showToast('info', '模型未下载，已自动加入下载队列，完成后可继续操作')
      startDownload(inpaintModel)
      return
    }

    try {
      startProcess(inpaintModel)
      const roiList = rois.map((r) => [r.x1, r.y1, r.x2, r.y2])

      const usePipeline = postprocessEnabled || upscaleEnabled

      let resultBase64: string

      if (usePipeline) {
        // 走完整 Pipeline
        const result = await runPipeline({
          image: sourceImage,
          rois: roiList,
          inpaint_model: inpaintModel,
          device,
          dilation: defaultDilation,
          disable_nsfw: disableNsfw,
          postprocess_method: postprocessMethod,
          postprocess_enabled: postprocessEnabled,
          upscale_enabled: upscaleEnabled,
          upscale_model: upscaleModel,
        })
        resultBase64 = result.image
      } else {
        // 仅去水印
        const result = await inpaint({
          image: sourceImage,
          rois: roiList,
          model: inpaintModel,
          device,
          dilation: defaultDilation,
          disable_nsfw: disableNsfw,
        })
        resultBase64 = result.image
      }

      setResultImage(`data:image/png;base64,${resultBase64}`)
      setShowResult(true)
      finishProcess('处理完成')
      showToast('success', usePipeline ? 'Pipeline 处理完成' : '水印去除完成')
    } catch (err: any) {
      setError(err.message)
      showToast('error', err.message)
    }
  }

  // Save
  const handleSave = async () => {
    if (!resultImage) return
    // 默认文件名：原文件名_processed.png
    const { sourceFilePath } = useImageStore.getState()
    let defaultName = 'result.png'
    if (sourceFilePath) {
      const baseName = sourceFilePath.split(/[\\/]/).pop()?.replace(/\.[^.]+$/, '') || 'result'
      defaultName = `${baseName}_processed.png`
    }
    if (window.electronAPI) {
      const filePath = await window.electronAPI.saveFile(defaultName)
      if (filePath) {
        const result = await window.electronAPI.saveImageFile(filePath, resultImage)
        if (result.success) {
          showToast('success', `已保存至: ${filePath}`)
        } else {
          showToast('error', `保存失败: ${result.error}`)
        }
      }
    } else {
      const a = document.createElement('a')
      a.href = resultImage
      a.download = defaultName
      a.click()
    }
  }

  // 将处理结果作为新的源图进行再次处理
  const handleUseResultAsSource = () => {
    if (!resultImage) return
    reset()
    clearROIs()
    setSourceImage(resultImage, undefined)
    setShowResult(false)
    showToast('success', '已将处理结果设为新源图，可继续去水印')
  }

  // Delete selected ROIs
  const handleDeleteSelected = () => {
    selectedROIs.forEach((id) => removeROI(id))
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <PageHeader
        title="去水印"
        subtitle={imageWidth > 0 ? `${imageWidth} x ${imageHeight}` : undefined}
        right={<span className="text-xs text-fg-secondary">{inpaintModel} | {device}</span>}
      />

      {/* Content area */}
      <div className="flex-1 flex overflow-hidden min-h-0">
        {/* Canvas area */}
        <div
          className="flex-1 bg-[#1a1a1a] relative"
          onDrop={handleDrop}
          onDragOver={handleDragOver}
        >
          {!sourceImage ? (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 text-fg-secondary">
              <Upload size={48} strokeWidth={1} />
              <p className="text-sm">拖拽图片到此处，或点击打开文件</p>
              <Button onClick={handleOpenFile}>打开图片</Button>
              <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleFileInput} />
            </div>
          ) : showResult && resultImage && sourceImage ? (
            <ImageCompare beforeSrc={sourceImage} afterSrc={resultImage} beforeLabel="原图" afterLabel="处理后" />
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
              {/* Toolbar */}
              <div className="absolute bottom-3 left-1/2 -translate-x-1/2 flex gap-1 bg-bg-secondary rounded-lg p-1 border border-border-subtle shadow-lg">
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
            </>
          )}
          <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleFileInput} />
        </div>

        {/* Control panel */}
        <div className="w-[240px] bg-bg-secondary border-l border-border-subtle p-3 flex flex-col gap-3 overflow-y-auto min-h-0">
          {/* ROI list */}
          <section>
            <h3 className="text-xs uppercase tracking-wider text-fg-secondary mb-2">水印区域</h3>
            <div className="bg-bg-primary rounded border border-border-subtle p-1 min-h-[60px] max-h-[140px] overflow-y-auto">
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
              <Button size="sm" variant="primary" onClick={handleAutoDetect} icon={<Wand2 size={12} />} className="flex-1">
                自动检测
              </Button>
              {selectedROIs.length > 0 ? (
                <Button size="sm" variant="danger" onClick={handleDeleteSelected} className="flex-1">
                  删除选中 ({selectedROIs.length})
                </Button>
              ) : (
                <Button size="sm" variant="ghost" onClick={clearROIs} className="flex-1" disabled={rois.length === 0}>
                  清除全部
                </Button>
              )}
            </div>
          </section>

          {/* Sensitivity */}
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

          {/* Process button */}
          <div className="mt-auto pt-2 space-y-2">
            {(() => {
              const task = downloadTasks[inpaintModel]
              const isModelBusy = task?.status === 'downloading' || task?.status === 'queued'
              const isModelMissing = task?.status === 'missing' || task?.status === 'error'
              return (
                <Button
                  onClick={handleProcess}
                  disabled={isProcessing || rois.length === 0 || !sourceImage || isModelBusy}
                  loading={isProcessing}
                  className="w-full"
                  size="lg"
                >
                  {isProcessing
                    ? statusMessage
                    : task?.status === 'downloading'
                      ? '等待下载...'
                      : task?.status === 'queued'
                        ? `排队中 #${task.position}`
                        : isModelMissing
                          ? '点击下载并处理'
                          : (postprocessEnabled || upscaleEnabled)
                            ? '去水印 + 增强'
                            : '去除水印'}
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
                  icon={<RefreshCw size={14} />}
                  className="w-full"
                  size="sm"
                >
                  再次去水印
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
