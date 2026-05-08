import { useState, useRef } from 'react'
import { Upload, Download, RefreshCw } from 'lucide-react'
import { Button, Select, Progress, showToast } from '../components/ui'
import ImageCompare from '../components/ImageCompare'
import PageHeader from '../components/layout/PageHeader'
import { useProcessStore } from '../stores/useProcessStore'
import { useSettingsStore } from '../stores/useSettingsStore'
import { useModelStore } from '../stores/useModelStore'
import { useBackendAPI } from '../hooks/useBackendAPI'
import { useDeviceOptions } from '../hooks/useDeviceOptions'

export default function SuperResolution() {
  const { isProcessing, progress, statusMessage, startProcess, finishProcess, setError, reset } = useProcessStore()
  const { upscale } = useBackendAPI()
  const { device, upscaleModel, setDevice, setUpscaleModel } = useSettingsStore()
  const { upscaleGroups } = useModelStore()
  const { options: deviceOptions } = useDeviceOptions()

  const [sourceImage, setSourceImage] = useState<string | null>(null)
  const [resultImage, setResultImage] = useState<string | null>(null)
  const [inputSize, setInputSize] = useState({ w: 0, h: 0 })
  const [outputSize, setOutputSize] = useState({ w: 0, h: 0 })

  const fileInputRef = useRef<HTMLInputElement>(null)

  const scaleMap: Record<string, number> = {
    RealESRGAN_x4plus: 4,
    RealESRGAN_x4plus_anime_6B: 4,
    RealESRGAN_x2plus: 2,
    'realesr-general-x4v3': 4,
    RealESRNet_x4plus: 4,
    'realesr-animevideov3': 4,
  }

  const currentScale = scaleMap[upscaleModel] || 4

  // Open file
  const handleOpenFile = async () => {
    if (window.electronAPI) {
      const filePath = await window.electronAPI.openFile()
      if (filePath) {
        try {
          const dataUrl = await window.electronAPI.readImageFile(filePath)
          setSourceImage(dataUrl)
          setResultImage(null)
          reset()
          loadImageSize(dataUrl)
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
      window.electronAPI.readImageFile(electronFile.path).then((dataUrl) => {
        setSourceImage(dataUrl)
        setResultImage(null)
        reset()
        loadImageSize(dataUrl)
      }).catch((err: any) => showToast('error', `读取图片失败: ${err.message}`))
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = reader.result as string
      setSourceImage(dataUrl)
      setResultImage(null)
      reset()
      loadImageSize(dataUrl)
    }
    reader.readAsDataURL(file)
  }

  const loadImageSize = (src: string) => {
    const img = new Image()
    img.onload = () => {
      setInputSize({ w: img.naturalWidth, h: img.naturalHeight })
      setOutputSize({ w: img.naturalWidth * currentScale, h: img.naturalHeight * currentScale })
    }
    img.src = src
  }

  // Update output size when model changes
  const handleModelChange = (newModel: string) => {
    setUpscaleModel(newModel)
    const scale = scaleMap[newModel] || 4
    if (inputSize.w > 0) {
      setOutputSize({ w: inputSize.w * scale, h: inputSize.h * scale })
    }
  }

  // Drag & drop
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const file = e.dataTransfer.files[0]
    if (!file || !file.type.startsWith('image/')) return
    const electronFile = file as File & { path?: string }
    if (window.electronAPI && electronFile.path) {
      window.electronAPI.readImageFile(electronFile.path).then((dataUrl) => {
        setSourceImage(dataUrl)
        setResultImage(null)
        reset()
        loadImageSize(dataUrl)
      }).catch((err: any) => showToast('error', `读取图片失败: ${err.message}`))
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = reader.result as string
      setSourceImage(dataUrl)
      setResultImage(null)
      reset()
      loadImageSize(dataUrl)
    }
    reader.readAsDataURL(file)
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }

  // Process
  const handleUpscale = async () => {
    if (!sourceImage) return
    try {
      startProcess(upscaleModel)
      const result = await upscale({ image: sourceImage, model: upscaleModel, device })
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
    if (window.electronAPI) {
      const filePath = await window.electronAPI.saveFile('upscaled.png')
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
      a.download = 'upscaled.png'
      a.click()
    }
  }

  // 将处理结果作为新的源图，进行再次超分
  const handleUseResultAsSource = () => {
    if (!resultImage) return
    const newInputSize = { ...outputSize }
    setSourceImage(resultImage)
    setResultImage(null)
    setInputSize(newInputSize)
    setOutputSize({ w: newInputSize.w * currentScale, h: newInputSize.h * currentScale })
    reset()
    showToast('success', '已将处理结果设为新源图，可继续超分辨率')
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <PageHeader
        title="超分辨率"
        subtitle={inputSize.w > 0 ? `${inputSize.w}x${inputSize.h} → ${outputSize.w}x${outputSize.h}` : undefined}
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
                beforeLabel={`原图 (${inputSize.w}x${inputSize.h})`}
                afterLabel={`${currentScale}x (${outputSize.w}x${outputSize.h})`}
              />
            </div>
          ) : (
            <div className="relative max-w-[85%] max-h-[85%]">
              <img
                src={sourceImage}
                alt="原图"
                className="max-w-full max-h-full object-contain rounded border border-border-subtle"
              />
              {inputSize.w > 0 && (
                <div className="absolute bottom-2 left-2 bg-black/70 px-2 py-1 rounded text-xs text-white">
                  {inputSize.w} x {inputSize.h}
                </div>
              )}
            </div>
          )}
          <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleFileInput} />
        </div>

        {/* Control panel */}
        <div className="w-[240px] bg-bg-secondary border-l border-border-subtle p-3 flex flex-col gap-4 overflow-y-auto min-h-0">
          {/* Model */}
          <Select
            label="模型"
            value={upscaleModel}
            onChange={(e) => handleModelChange(e.target.value)}
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

          {/* Image info */}
          <section>
            <h3 className="text-xs uppercase tracking-wider text-fg-secondary mb-2">图像信息</h3>
            <div className="bg-bg-primary rounded border border-border-subtle p-2.5 space-y-1.5">
              <div className="flex justify-between text-xs">
                <span className="text-fg-secondary">输入尺寸</span>
                <span className="text-fg-primary">{inputSize.w > 0 ? `${inputSize.w} x ${inputSize.h}` : '-'}</span>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-fg-secondary">输出尺寸</span>
                <span className="text-status-success">{outputSize.w > 0 ? `${outputSize.w} x ${outputSize.h}` : '-'}</span>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-fg-secondary">放大倍率</span>
                <span className="text-fg-accent">{currentScale}x</span>
              </div>
              {inputSize.w > 0 && (
                <div className="flex justify-between text-xs">
                  <span className="text-fg-secondary">预计大小</span>
                  <span className="text-fg-primary">~{Math.round(outputSize.w * outputSize.h * 3 / 1024 / 1024)} MB</span>
                </div>
              )}
            </div>
          </section>

          {/* Action */}
          <div className="mt-auto pt-2 space-y-2">
            <Button
              onClick={handleUpscale}
              disabled={!sourceImage || isProcessing}
              loading={isProcessing}
              className="w-full"
              size="lg"
            >
              {isProcessing ? statusMessage : '开始超分'}
            </Button>

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
                icon={<RefreshCw size={14} />}
                className="w-full"
                size="sm"
              >
                再次超分
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
