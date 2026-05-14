import { useState, useRef } from 'react'
import { Upload, Download } from 'lucide-react'
import { Button, Select, Progress, showToast } from '../components/ui'
import ImageCanvas from '../components/ImageCanvas'
import ImageCompare from '../components/ImageCompare'
import PageHeader from '../components/layout/PageHeader'
import ModelSelect from '../components/ModelSelect/ModelSelect'
import { useImageStore } from '../stores/useImageStore'
import { useProcessStore } from '../stores/useProcessStore'
import { useSettingsStore } from '../stores/useSettingsStore'
import { useModelStore } from '../stores/useModelStore'
import { useBackendAPI } from '../hooks/useBackendAPI'
import { useDeviceOptions } from '../hooks/useDeviceOptions'
import { useDownloadStore } from '../stores/useDownloadStore'
import { useDownloadManager } from '../hooks/useDownloadManager'
import { DownloadStatus, ModelStatus } from '../constants'

export default function SuperResolution() {
  const {
    sourceImage, imageWidth, imageHeight,
    setSourceImage, setImageDimensions,
  } = useImageStore()
  const { isProcessing, progress, statusMessage, startProcess, finishProcess, setError, reset } = useProcessStore()
  const { upscale } = useBackendAPI()
  const { device, upscaleModel, setDevice, setUpscaleModel } = useSettingsStore()
  const { upscaleGroups, upscaleModelMeta } = useModelStore()
  const { options: deviceOptions } = useDeviceOptions()
  const downloadTasks = useDownloadStore((s) => s.tasks)
  const { startDownload } = useDownloadManager()

  const [resultImage, setResultImage] = useState<string | null>(null)
  const [outputSize, setOutputSize] = useState({ w: 0, h: 0 })
  // outscale: null 表示跟随模型默认值，用户可手动覆盖
  const [outscale, setOutscale] = useState<number | null>(null)

  const fileInputRef = useRef<HTMLInputElement>(null)

  // 当前模型的默认 outscale（从 store 读，fallback 4）
  const defaultOutscale = upscaleModelMeta[upscaleModel]?.defaultOutscale ?? 4
  // 实际使用的倍率（用户覆盖优先）
  const currentScale = outscale ?? defaultOutscale
  // 当前模型是否支持自定义输出倍率
  const supportsCustomOutscale = upscaleModelMeta[upscaleModel]?.supportsCustomOutscale ?? false

  // 加载原图（写入共享 useImageStore）
  const loadSource = (dataUrl: string, filePath?: string) => {
    setResultImage(null)
    reset()
    setSourceImage(dataUrl, filePath)
    const img = new Image()
    img.onload = () => {
      setImageDimensions(img.naturalWidth, img.naturalHeight)
      setOutputSize({ w: img.naturalWidth * currentScale, h: img.naturalHeight * currentScale })
    }
    img.src = dataUrl
  }

  // Open file
  const handleOpenFile = async () => {
    if (window.electronAPI) {
      const filePath = await window.electronAPI.openFile()
      if (filePath) {
        try {
          const dataUrl = await window.electronAPI.readImageFile(filePath)
          loadSource(dataUrl, filePath)
        } catch (err: any) {
          showToast('error', `读取图片失败: ${err.message}`)
        }
      }
    } else {
      fileInputRef.current?.click()
    }
  }

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const electronFile = file as File & { path?: string }
    if (window.electronAPI && electronFile.path) {
      window.electronAPI.readImageFile(electronFile.path)
        .then((dataUrl) => loadSource(dataUrl, electronFile.path))
        .catch((err: any) => showToast('error', `读取图片失败: ${err.message}`))
      return
    }
    const reader = new FileReader()
    reader.onload = () => loadSource(reader.result as string)
    reader.readAsDataURL(file)
  }

  // Update output size when model changes
  const handleModelChange = (newModel: string) => {
    setUpscaleModel(newModel)
    setOutscale(null)  // 重置为新模型的默认倍率
    const scale = upscaleModelMeta[newModel]?.defaultOutscale ?? 4
    if (imageWidth > 0) {
      setOutputSize({ w: imageWidth * scale, h: imageHeight * scale })
    }
  }

  // Drag & drop
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const file = e.dataTransfer.files[0]
    if (!file || !file.type.startsWith('image/')) return
    const electronFile = file as File & { path?: string }
    if (window.electronAPI && electronFile.path) {
      window.electronAPI.readImageFile(electronFile.path)
        .then((dataUrl) => loadSource(dataUrl, electronFile.path))
        .catch((err: any) => showToast('error', `读取图片失败: ${err.message}`))
      return
    }
    const reader = new FileReader()
    reader.onload = () => loadSource(reader.result as string)
    reader.readAsDataURL(file)
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }

  // Process
  const handleUpscale = async () => {
    if (!sourceImage) return

    const task = downloadTasks[upscaleModel]
    if (task?.status === DownloadStatus.DOWNLOADING || task?.status === DownloadStatus.QUEUED) {
      showToast('info', '模型下载中，请稍候...')
      return
    }
    if (task?.status === ModelStatus.MISSING || task?.status === DownloadStatus.ERROR) {
      showToast('info', '模型未下载，已自动加入下载队列，完成后可继续操作')
      startDownload(upscaleModel)
      return
    }

    try {
      startProcess(upscaleModel)
      const result = await upscale({ image: sourceImage, model: upscaleModel, device, outscale: currentScale })
      setResultImage(`data:image/png;base64,${result.image}`)
      setOutputSize({ w: result.width, h: result.height })
      finishProcess('超分辨率处理完成')
      showToast('success', `超分辨率完成: ${result.width}x${result.height}`)
    } catch (err: any) {
      setError(err.message)
      showToast('error', err.message)
    }
  }

  // Save
  const handleSave = async () => {
    if (!resultImage) return
    const { sourceFilePath } = useImageStore.getState()
    const baseName = sourceFilePath
      ? sourceFilePath.split(/[\\/]/).pop()?.replace(/\.[^.]+$/, '') || 'image'
      : 'image'
    const defaultName = `${baseName}_upscaled.png`
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

  // 将处理结果设为原图
  const handleUseResultAsSource = () => {
    if (!resultImage) return
    // 保留原始文件路径，保存时文件名仍以原图名为准
    const { sourceFilePath } = useImageStore.getState()
    loadSource(resultImage, sourceFilePath ?? undefined)
    showToast('success', '已将处理结果设为原图')
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <PageHeader
        title="超分辨率"
        subtitle={imageWidth > 0 ? `${imageWidth}x${imageHeight} → ${outputSize.w}x${outputSize.h}` : undefined}
        right={<span className="text-xs text-fg-secondary">{upscaleModel.replace('RealESRGAN_', '')} | {device}</span>}
      />

      {/* Content */}
      <div className="flex-1 flex overflow-hidden min-h-0">
        {/* Image area */}
        <div
          className="flex-1 bg-[#1a1a1a] flex items-center justify-center relative"
          onDrop={handleDrop}
          onDragOver={handleDragOver}
        >
          {!sourceImage ? (
            <div className="flex flex-col items-center gap-4 text-fg-secondary">
              <Upload size={48} strokeWidth={1} />
              <p className="text-sm">拖拽图片到此处，或点击打开文件</p>
              <Button onClick={handleOpenFile}>打开图片</Button>
              <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleFileInput} />
            </div>
          ) : resultImage ? (
            <div className="w-full h-full p-4">
              <ImageCompare
                beforeSrc={sourceImage}
                afterSrc={resultImage}
                beforeLabel={`原图 (${imageWidth}x${imageHeight})`}
                afterLabel={`${currentScale}x (${outputSize.w}x${outputSize.h})`}
              />
            </div>
          ) : (
            <ImageCanvas
              imageSrc={sourceImage}
              rois={[]}
              selectedROIs={[]}
              tool="pan"
              onROIDrawn={() => {}}
              onROISelect={() => {}}
              onImageLoad={(w, h) => setImageDimensions(w, h)}
            />
          )}
          <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleFileInput} />
        </div>

        {/* Control panel */}
        <div className="w-[240px] bg-bg-secondary border-l border-border-subtle p-3 flex flex-col gap-4 overflow-y-auto min-h-0">
          {/* Model */}
          <ModelSelect
            label="模型"
            value={upscaleModel}
            onChange={(v) => handleModelChange(v)}
            size="sm"
            groups={upscaleGroups}
          />

          {/* Device */}
          <Select
            label="设备"
            value={device}
            onChange={(e) => setDevice(e.target.value)}
            size="sm"
            options={deviceOptions}
          />

          {/* Output scale - 仅支持自定义倍率的模型显示 */}
          {supportsCustomOutscale && (
            <Select
              label="输出倍率"
              value={String(currentScale)}
              onChange={(e) => {
                const val = Number(e.target.value)
                setOutscale(val)
                if (imageWidth > 0) {
                  setOutputSize({ w: imageWidth * val, h: imageHeight * val })
                }
              }}
              size="sm"
              options={[
                { value: '1', label: '1x（清晰化，不放大）' },
                { value: '2', label: '2x' },
                { value: '4', label: '4x' },
                { value: '8', label: '8x' },
              ]}
            />
          )}

          {/* 固定倍率模型的倍率展示（不可修改） */}
          {!supportsCustomOutscale && (
            <div>
              <label className="block text-xs font-medium text-fg-secondary mb-1">输出倍率</label>
              <div className="px-3 py-1.5 bg-bg-primary rounded border border-border-subtle text-sm text-fg-primary">
                {defaultOutscale}x（固定）
              </div>
            </div>
          )}

          {/* Image info */}
          <section>
            <h3 className="text-xs uppercase tracking-wider text-fg-secondary mb-2">图像信息</h3>
            <div className="bg-bg-primary rounded border border-border-subtle p-2.5 space-y-1.5">
              <div className="flex justify-between text-xs">
                <span className="text-fg-secondary">输入尺寸</span>
                <span className="text-fg-primary">{imageWidth > 0 ? `${imageWidth} x ${imageHeight}` : '-'}</span>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-fg-secondary">输出尺寸</span>
                <span className="text-status-success">{outputSize.w > 0 ? `${outputSize.w} x ${outputSize.h}` : '-'}</span>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-fg-secondary">放大倍率</span>
                <span className="text-fg-accent">{currentScale}x</span>
              </div>
              {imageWidth > 0 && (
                <div className="flex justify-between text-xs">
                  <span className="text-fg-secondary">预计大小</span>
                  <span className="text-fg-primary">~{Math.round(outputSize.w * outputSize.h * 3 / 1024 / 1024)} MB</span>
                </div>
              )}
            </div>
          </section>

          {/* Action */}
          <div className="mt-auto pt-2 space-y-2">
            {(() => {
              const task = downloadTasks[upscaleModel]
              const isModelBusy = task?.status === DownloadStatus.DOWNLOADING || task?.status === DownloadStatus.QUEUED
              return (
                <Button
                  onClick={handleUpscale}
                  disabled={!sourceImage || isProcessing || isModelBusy}
                  loading={isProcessing}
                  className="w-full"
                  size="lg"
                >
                  {isProcessing
                    ? statusMessage
                    : (() => {
                        const t = downloadTasks[upscaleModel]
                        if (t?.status === DownloadStatus.DOWNLOADING) return '等待下载...'
                        if (t?.status === DownloadStatus.QUEUED) return `排队中 #${t.position}`
                        if (t?.status === ModelStatus.MISSING || t?.status === DownloadStatus.ERROR) return '点击下载并处理'
                        return '开始超分'
                      })()}
                </Button>
              )
            })()}

            {isProcessing && (
              <Progress value={progress} label={`${statusMessage} ${progress > 0 ? progress + '%' : ''}`} />
            )}

            {resultImage && (
              <Button
                variant="ghost"
                onClick={handleSave}
                icon={<Download size={14} />}
                className="w-full"
                size="sm"
              >
                保存结果
              </Button>
            )}

            {resultImage && (
              <Button
                variant="ghost"
                onClick={handleUseResultAsSource}
                icon={<Upload size={14} />}
                className="w-full"
                size="sm"
              >
                将结果设为原图
              </Button>
            )}

            {sourceImage && !resultImage && (
              <Button
                variant="ghost"
                onClick={handleOpenFile}
                icon={<Upload size={14} />}
                className="w-full"
                size="sm"
              >
                更换图片
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
