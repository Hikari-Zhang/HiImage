import { useState, useRef, useCallback, useEffect } from 'react'
import { Upload, Download, Images } from 'lucide-react'
import { Button, Select, Progress, showToast } from '../components/ui'
import ImageCanvas from '../components/ImageCanvas'
import ImageCompare from '../components/ImageCompare'
import PageHeader from '../components/layout/PageHeader'
import ModelSelect from '../components/ModelSelect/ModelSelect'
import BatchImageQueue, { type ImageQueueItem } from '../components/BatchImageQueue'
import BatchProgress from '../components/BatchProgress'
import WorkspaceInputBar from '../components/WorkspaceInputBar'
import {
  useWorkspaceStore,
  selectWorkingImage,
} from '../stores/useWorkspaceStore'
import { useProcessStore } from '../stores/useProcessStore'
import { useSettingsStore } from '../stores/useSettingsStore'
import { useModelStore } from '../stores/useModelStore'
import { useBackendAPI } from '../hooks/useBackendAPI'
import { useDeviceOptions } from '../hooks/useDeviceOptions'
import { useDownloadStore } from '../stores/useDownloadStore'
import { useDownloadManager } from '../hooks/useDownloadManager'
import { DownloadStatus, ModelStatus } from '../constants'

async function readItemImage(item: ImageQueueItem): Promise<string> {
  if (item.filePath && window.electronAPI) {
    return window.electronAPI.readImageFile(item.filePath)
  }
  if (item.dataUrl) return item.dataUrl
  if (item.file) {
    return new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result as string)
      reader.onerror = () => reject(reader.error)
      reader.readAsDataURL(item.file!)
    })
  }
  throw new Error('无图片数据')
}

export default function Denoise() {
  const sourceImage = useWorkspaceStore((s) => s.sourceImage)
  const sourceFilePath = useWorkspaceStore((s) => s.sourceFilePath)
  const loadImage = useWorkspaceStore((s) => s.loadImage)
  const setHandoffResult = useWorkspaceStore((s) => s.setHandoffResult)
  const workingImage = useWorkspaceStore(selectWorkingImage)
  const { isProcessing, progress, statusMessage, startProcess, finishProcess, setError, reset } = useProcessStore()
  const { denoise, denoiseBatch } = useBackendAPI()
  const { device, denoiseModel, upscaleTile, setDevice, setDenoiseModel } = useSettingsStore()
  const { denoiseGroups } = useModelStore()
  const { options: deviceOptions } = useDeviceOptions()
  const downloadTasks = useDownloadStore((s) => s.tasks)
  const { startDownload } = useDownloadManager()

  const [mode, setMode] = useState<'single' | 'batch'>('single')
  const [batchItems, setBatchItems] = useState<ImageQueueItem[]>([])
  const [batchCurrentIndex, setBatchCurrentIndex] = useState<number | null>(null)
  const [isBatchProcessing, setIsBatchProcessing] = useState(false)
  const [resultImage, setResultImage] = useState<string | null>(null)
  const [compareBefore, setCompareBefore] = useState<string | null>(null)
  const [inputSize, setInputSize] = useState({ w: 0, h: 0 })
  const [outputSize, setOutputSize] = useState({ w: 0, h: 0 })
  const [tile, setTile] = useState<number | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const loadSource = (dataUrl: string, filePath?: string) => {
    if (mode === 'batch') {
      const img = new Image()
      img.onload = () => {
        const newItem: ImageQueueItem = {
          id: `img_${Date.now()}_${Math.random().toString(36).substr(1, 5)}`,
          fileName: filePath ? filePath.split(/[\\/]/).pop() || 'unknown.png' : `image_${batchItems.length + 1}.png`,
          filePath: filePath,
          dataUrl: dataUrl,
          status: 'pending',
        }
        setBatchItems(prev => [...prev, newItem])
        showToast('success', `已添加到队列: ${newItem.fileName}`)
      }
      img.src = dataUrl
      return
    }
    setResultImage(null)
    setCompareBefore(null)
    reset()
    loadImage(dataUrl, filePath)
    const img = new Image()
    img.onload = () => {
      setInputSize({ w: img.naturalWidth, h: img.naturalHeight })
      setOutputSize({ w: img.naturalWidth, h: img.naturalHeight })
    }
    img.src = dataUrl
  }

  useEffect(() => {
    if (!workingImage || mode === 'batch' || resultImage) return
    const img = new Image()
    img.onload = () => {
      setInputSize({ w: img.naturalWidth, h: img.naturalHeight })
      setOutputSize({ w: img.naturalWidth, h: img.naturalHeight })
    }
    img.src = workingImage
  }, [workingImage, mode, resultImage])

  const handleOpenFile = async () => {
    if (window.electronAPI) {
      const handler = mode === 'batch' ? window.electronAPI.openFiles?.() : window.electronAPI.openFile()
      const result = await handler
      if (result) {
        if (mode === 'batch') {
          const paths = Array.isArray(result) ? result : [result]
          const newItems: ImageQueueItem[] = paths.map(filePath => ({
            id: `img_${Date.now()}_${Math.random().toString(36).substr(1, 5)}`,
            fileName: filePath.split(/[\\/]/).pop() || 'unknown.png',
            filePath,
            status: 'pending' as const,
          }))
          setBatchItems(prev => [...prev, ...newItems])
          showToast('success', `已添加 ${newItems.length} 张图片到队列`)
        } else {
          const filePath = Array.isArray(result) ? result[0] : result
          try {
            const dataUrl = await window.electronAPI.readImageFile(filePath)
            loadSource(dataUrl, filePath)
          } catch (err: any) {
            showToast('error', `读取图片失败: ${err.message}`)
          }
        }
      }
    } else {
      fileInputRef.current?.click()
    }
  }

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files || files.length === 0) return
    e.target.value = ''
    if (mode === 'batch') {
      const newItems: ImageQueueItem[] = Array.from(files).map(file => ({
        id: `img_${Date.now()}_${Math.random().toString(36).substr(1, 5)}`,
        fileName: file.name,
        file,
        status: 'pending' as const,
      }))
      setBatchItems(prev => [...prev, ...newItems])
      showToast('success', `已添加 ${newItems.length} 张图片到队列`)
    } else {
      const file = files[0]
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
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const files = e.dataTransfer.files
    if (!files || files.length === 0) return
    if (mode === 'batch') {
      const newItems: ImageQueueItem[] = Array.from(files)
        .filter(file => file.type.startsWith('image/'))
        .map(file => ({
          id: `img_${Date.now()}_${Math.random().toString(36).substr(1, 5)}`,
          fileName: file.name,
          file,
          status: 'pending' as const,
        }))
      if (newItems.length > 0) {
        setBatchItems(prev => [...prev, ...newItems])
        showToast('success', `已添加 ${newItems.length} 张图片到队列`)
      }
    } else {
      const file = files[0]
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
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }

  const ensureModelReady = () => {
    const task = downloadTasks[denoiseModel]
    if (task?.status === DownloadStatus.DOWNLOADING || task?.status === DownloadStatus.QUEUED) {
      showToast('info', '模型下载中，请稍候...')
      return false
    }
    if (task?.status === ModelStatus.MISSING || task?.status === DownloadStatus.ERROR) {
      showToast('info', '模型未下载，已自动加入下载队列，完成后可继续操作')
      startDownload(denoiseModel)
      return false
    }
    return true
  }

  const handleBatchDenoise = async () => {
    if (batchItems.length === 0) return
    if (!ensureModelReady()) return

    setIsBatchProcessing(true)
    const isElectron = !!window.electronAPI
    const items = batchItems.map(item => ({ ...item, status: 'pending' as ImageQueueItem['status'] })) as ImageQueueItem[]
    setBatchItems(items)
    setBatchCurrentIndex(0)

    let outputDir: string | undefined
    if (isElectron) {
      const dir = await window.electronAPI.openDirectory?.()
      if (!dir) {
        setIsBatchProcessing(false)
        setBatchCurrentIndex(null)
        return
      }
      outputDir = dir
    }

    const useFilePath = isElectron && items.every(i => !!i.filePath)
    try {
      startProcess(denoiseModel)
      setBatchItems(items.map(i => ({ ...i, status: 'processing' as const })))
      let images: string[]
      if (useFilePath) {
        images = items.map(i => i.filePath as string)
      } else {
        images = await Promise.all(items.map(readItemImage))
      }
      const result = await denoiseBatch({
        images,
        model: denoiseModel,
        device,
        tile: tile ?? (upscaleTile || 0),
        output_dir: outputDir,
        is_file_path: useFilePath,
      })
      const next = items.map((item, idx) => {
        const r = result.results.find(x => x.index === idx)
        if (r?.status === 'success') {
          return {
            ...item,
            status: 'success' as const,
            resultPath: r.outputPath,
            resultDataUrl: r.image ? `data:image/png;base64,${r.image}` : undefined,
          }
        }
        return { ...item, status: 'error' as const, error: r?.error || '处理失败' }
      })
      setBatchItems(next.map(({ dataUrl, resultDataUrl, ...rest }) => {
        if (rest.resultPath) return rest as ImageQueueItem
        return { ...rest, resultDataUrl } as ImageQueueItem
      }))
      const { success, failed } = result.summary
      finishProcess('批量降噪完成')
      showToast('success', `批量降噪完成: ${success} 成功${failed > 0 ? `, ${failed} 失败` : ''}`)
    } catch (err: any) {
      setError(err.message)
      showToast('error', err.message)
      setBatchItems(items)
    } finally {
      setIsBatchProcessing(false)
      setBatchCurrentIndex(null)
    }
  }

  const handleDenoise = async () => {
    if (!workingImage) return
    if (!ensureModelReady()) return

    let outputPath: string | undefined
    if (window.electronAPI && sourceFilePath) {
      outputPath = sourceFilePath.replace(/\.[^.]+$/, '') + '_denoised.png'
    }

    try {
      startProcess(denoiseModel)
      setCompareBefore(workingImage)
      const result = await denoise({
        image: workingImage,
        model: denoiseModel,
        device,
        tile: tile ?? (upscaleTile || 0),
        output_path: outputPath,
      })
      if (result.outputPath) {
        const dataUrl = await window.electronAPI.readImageFile(result.outputPath)
        setResultImage(dataUrl)
        setHandoffResult(dataUrl, { width: result.width, height: result.height })
      } else {
        const dataUrl = `data:image/png;base64,${result.image}`
        setResultImage(dataUrl)
        setHandoffResult(dataUrl, { width: result.width, height: result.height })
      }
      setOutputSize({ w: result.width, h: result.height })
      finishProcess('降噪处理完成')
      showToast('success', `降噪完成: ${result.width}x${result.height}`)
    } catch (err: any) {
      setError(err.message)
      showToast('error', err.message)
    }
  }

  const handleSave = async () => {
    if (!resultImage) return
    const baseName = sourceFilePath
      ? sourceFilePath.split(/[\\/]/).pop()?.replace(/\.[^.]+$/, '') || 'image'
      : 'image'
    const defaultName = `${baseName}_denoised.png`
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

  const handleUseResultAsSource = () => {
    if (!resultImage) return
    loadSource(resultImage, sourceFilePath ?? undefined)
    showToast('success', '已将处理结果设为原图')
  }

  const handleRemoveBatchItem = (id: string) => {
    setBatchItems(prev => prev.filter(item => item.id !== id))
  }

  const handleClearBatch = () => {
    setBatchItems([])
    setBatchCurrentIndex(null)
  }

  const handleAddToBatch = (files: FileList | string[]) => {
    if (Array.isArray(files)) {
      const newItems: ImageQueueItem[] = (files as string[]).map(filePath => ({
        id: `img_${Date.now()}_${Math.random().toString(36).substr(1, 5)}`,
        fileName: filePath.split(/[\\/]/).pop() || 'unknown.png',
        filePath,
        status: 'pending' as const,
      }))
      setBatchItems(prev => [...prev, ...newItems])
      showToast('success', `已添加 ${newItems.length} 张图片到队列`)
    } else {
      const newItems: ImageQueueItem[] = Array.from(files as FileList).map(file => ({
        id: `img_${Date.now()}_${Math.random().toString(36).substr(1, 5)}`,
        fileName: file.name,
        file,
        status: 'pending' as const,
      }))
      setBatchItems(prev => [...prev, ...newItems])
      showToast('success', `已添加 ${newItems.length} 张图片到队列`)
    }
  }

  const loadBatchThumbnail = useCallback(async (id: string) => {
    const item = batchItems.find(i => i.id === id)
    if (!item || item.dataUrl) return
    try {
      const dataUrl = await readItemImage(item)
      setBatchItems(prev => prev.map(i => i.id === id ? { ...i, dataUrl } : i))
    } catch (err: any) {
      console.error('缩略图加载失败:', err)
    }
  }, [batchItems])

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <PageHeader
        title="图片降噪"
        subtitle={
          mode === 'batch'
            ? `${batchItems.length} 张图片 | 保持原分辨率 | ${denoiseModel}`
            : inputSize.w > 0 ? `${inputSize.w}x${inputSize.h}（保持分辨率）` : undefined
        }
        right={
          <div className="flex items-center gap-2">
            {mode === 'single' && (
              <WorkspaceInputBar
                onChange={() => {
                  setResultImage(null)
                  setCompareBefore(null)
                }}
              />
            )}
            <div className="flex items-center gap-1">
            <button
              onClick={() => setMode('single')}
              className={`px-2 py-1 rounded text-xs ${
                mode === 'single'
                  ? 'bg-blue-500 text-white'
                  : 'bg-bg-primary text-fg-secondary hover:text-fg-primary'
              }`}
            >
              <Upload size={12} className="inline mr-1" />
              单图
            </button>
            <button
              onClick={() => setMode('batch')}
              className={`px-2 py-1 rounded text-xs ${
                mode === 'batch'
                  ? 'bg-blue-500 text-white'
                  : 'bg-bg-primary text-fg-secondary hover:text-fg-primary'
              }`}
            >
              <Images size={12} className="inline mr-1" />
              批量
            </button>
            </div>
          </div>
        }
      />

      <div className="flex-1 flex overflow-hidden min-h-0">
        <div
          className="flex-1 bg-[#1a1a1a] flex items-center justify-center relative"
          onDrop={handleDrop}
          onDragOver={handleDragOver}
        >
          {mode === 'batch' ? (
            <div className="w-full h-full p-4">
              <BatchImageQueue
                items={batchItems}
                currentIndex={batchCurrentIndex}
                onRemove={handleRemoveBatchItem}
                onClear={handleClearBatch}
                onAdd={handleAddToBatch}
                onLoadThumbnail={loadBatchThumbnail}
                disabled={isBatchProcessing}
              />
            </div>
          ) : !sourceImage ? (
            <div className="flex flex-col items-center gap-4 text-fg-secondary">
              <Upload size={48} strokeWidth={1} />
              <p className="text-sm">拖拽图片到此处，或点击打开文件</p>
              <Button onClick={handleOpenFile}>打开图片</Button>
              <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleFileInput} />
            </div>
          ) : resultImage ? (
            <div className="w-full h-full p-4">
              <ImageCompare
                beforeSrc={compareBefore || workingImage || sourceImage}
                afterSrc={resultImage}
                beforeLabel={`输入 (${inputSize.w}x${inputSize.h})`}
                afterLabel={`降噪 (${outputSize.w}x${outputSize.h})`}
              />
            </div>
          ) : (
            <ImageCanvas
              imageSrc={workingImage}
              rois={[]}
              selectedROIs={[]}
              tool="pan"
              onROIDrawn={() => {}}
              onROISelect={() => {}}
              onImageLoad={(w, h) => {
                setInputSize({ w, h })
                setOutputSize({ w, h })
              }}
            />
          )}
          <input ref={fileInputRef} type="file" accept="image/*" multiple={mode === 'batch'} className="hidden" onChange={handleFileInput} />
        </div>

        <div className="w-[240px] bg-bg-secondary border-l border-border-subtle p-3 flex flex-col gap-4 overflow-y-auto min-h-0">
          <ModelSelect
            label="模型"
            value={denoiseModel}
            onChange={(v) => setDenoiseModel(v)}
            size="sm"
            groups={denoiseGroups}
          />
          <Select
            label="设备"
            value={device}
            onChange={(e) => setDevice(e.target.value)}
            size="sm"
            options={deviceOptions}
          />
          <div>
            <label className="block text-xs font-medium text-fg-secondary mb-1">
              分块大小 (Tile)
              <span className="ml-1 text-[10px] text-fg-tertiary">（0 = 自动）</span>
            </label>
            <input
              type="number"
              min={0}
              max={2048}
              step={64}
              value={tile ?? upscaleTile ?? 0}
              onChange={(e) => setTile(e.target.value ? Number(e.target.value) : null)}
              className="w-full bg-bg-primary border border-border-subtle text-fg-primary text-xs px-2 py-1.5 rounded focus:border-border-focus focus:outline-none"
            />
            <p className="text-[11px] text-fg-tertiary mt-1">
              分块处理大小：0 = 自动计算；建议值：256 / 512 / 1024。降低此值可节省显存。
            </p>
          </div>
          {mode === 'single' && (
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
                  <span className="text-fg-secondary">分辨率</span>
                  <span className="text-fg-accent">保持原尺寸</span>
                </div>
              </div>
            </section>
          )}
          {mode === 'batch' && isBatchProcessing && (
            <BatchProgress
              current={batchCurrentIndex || 0}
              total={batchItems.length}
            />
          )}
          <div className="mt-auto pt-2 space-y-2">
            {mode === 'batch' ? (
              <>
                <Button
                  onClick={handleBatchDenoise}
                  disabled={batchItems.length === 0 || isBatchProcessing}
                  loading={isBatchProcessing}
                  className="w-full"
                  size="lg"
                >
                  {isBatchProcessing ? '处理中...' : `开始批量降噪 (${batchItems.length} 张)`}
                </Button>
              </>
            ) : (
              <>
                {(() => {
                  const task = downloadTasks[denoiseModel]
                  const isModelBusy = task?.status === DownloadStatus.DOWNLOADING || task?.status === DownloadStatus.QUEUED
                  return (
                    <Button
                      onClick={handleDenoise}
                      disabled={!workingImage || isProcessing || isModelBusy}
                      loading={isProcessing}
                      className="w-full"
                      size="lg"
                    >
                      {isProcessing
                        ? statusMessage
                        : (() => {
                            const t = downloadTasks[denoiseModel]
                            if (t?.status === DownloadStatus.DOWNLOADING) return '等待下载...'
                            if (t?.status === DownloadStatus.QUEUED) return `排队中 #${t.position}`
                            if (t?.status === ModelStatus.MISSING || t?.status === DownloadStatus.ERROR) return '点击下载并处理'
                            return '开始降噪'
                          })()}
                    </Button>
                  )
                })()}
                {isProcessing && (
                  <Progress value={progress} label={`${statusMessage} ${progress > 0 ? progress + '%' : ''}`} />
                )}
                {resultImage && (
                  <Button variant="ghost" onClick={handleSave} icon={<Download size={14} />} className="w-full" size="sm">
                    保存结果
                  </Button>
                )}
                {resultImage && (
                  <Button variant="ghost" onClick={handleUseResultAsSource} icon={<Upload size={14} />} className="w-full" size="sm">
                    将结果设为原图
                  </Button>
                )}
                {sourceImage && !resultImage && (
                  <Button variant="ghost" onClick={handleOpenFile} icon={<Upload size={14} />} className="w-full" size="sm">
                    更换图片
                  </Button>
                )}
              </>
            )}
            {mode === 'batch' && (
              <Button
                variant="ghost"
                onClick={handleOpenFile}
                disabled={isBatchProcessing}
                icon={<Images size={14} />}
                className="w-full"
                size="sm"
              >
                选择图片
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
