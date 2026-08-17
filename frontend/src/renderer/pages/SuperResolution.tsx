import { useState, useRef, useCallback, useEffect } from 'react'
import { Upload, Download, Images, List, CheckCircle2 } from 'lucide-react'
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
  selectWorkingWidth,
  selectWorkingHeight,
} from '../stores/useWorkspaceStore'
import { useProcessStore } from '../stores/useProcessStore'
import { useSettingsStore } from '../stores/useSettingsStore'
import { useModelStore } from '../stores/useModelStore'
import { useBackendAPI } from '../hooks/useBackendAPI'
import { useDeviceOptions } from '../hooks/useDeviceOptions'
import { useDownloadStore } from '../stores/useDownloadStore'
import { useDownloadManager } from '../hooks/useDownloadManager'
import { DownloadStatus, ModelStatus } from '../constants'

export default function SuperResolution() {
  const sourceImage = useWorkspaceStore((s) => s.sourceImage)
  const sourceFilePath = useWorkspaceStore((s) => s.sourceFilePath)
  const loadImage = useWorkspaceStore((s) => s.loadImage)
  const setHandoffResult = useWorkspaceStore((s) => s.setHandoffResult)
  const workingImage = useWorkspaceStore(selectWorkingImage)
  const workingWidth = useWorkspaceStore(selectWorkingWidth)
  const workingHeight = useWorkspaceStore(selectWorkingHeight)
  const { isProcessing, progress, statusMessage, startProcess, finishProcess, setError, reset } = useProcessStore()
  const { upscale } = useBackendAPI()
  const { device, upscaleModel, upscaleTile, setDevice, setUpscaleModel } = useSettingsStore()
  const { upscaleGroups, upscaleModelMeta } = useModelStore()
  const { options: deviceOptions } = useDeviceOptions()
  const downloadTasks = useDownloadStore((s) => s.tasks)
  const { startDownload } = useDownloadManager()

  // 模式：'single' | 'batch'
  const [mode, setMode] = useState<'single' | 'batch'>('single')

  // 批量模式状态
  const [batchItems, setBatchItems] = useState<ImageQueueItem[]>([])
  const [batchCurrentIndex, setBatchCurrentIndex] = useState<number | null>(null)
  const [batchResultDir, setBatchResultDir] = useState<string | null>(null)
  const [isBatchProcessing, setIsBatchProcessing] = useState(false)

  const [resultImage, setResultImage] = useState<string | null>(null)
  const [compareBefore, setCompareBefore] = useState<string | null>(null)
  const [inputSize, setInputSize] = useState({ w: 0, h: 0 })
  const [outputSize, setOutputSize] = useState({ w: 0, h: 0 })
  // outscale: null 表示跟随模型默认值，用户可手动覆盖
  const [outscale, setOutscale] = useState<number | null>(null)
  // tile: null 表示使用默认配置，用户可手动覆盖
  const [tile, setTile] = useState<number | null>(null)

  const fileInputRef = useRef<HTMLInputElement>(null)

  // 当前模型的默认 outscale（从 store 读，fallback 4）
  const defaultOutscale = upscaleModelMeta[upscaleModel]?.defaultOutscale ?? 4
  // 实际使用的倍率（用户覆盖优先）
  const currentScale = outscale ?? defaultOutscale
  // 当前模型是否支持自定义输出倍率
  const supportsCustomOutscale = upscaleModelMeta[upscaleModel]?.supportsCustomOutscale ?? false

  // 加载原图（写入工作区；批量模式加入队列）
  const loadSource = (dataUrl: string, filePath?: string) => {
    if (mode === 'batch') {
      // 批量模式：添加到队列
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
    // 单图模式
    setResultImage(null)
    setCompareBefore(null)
    reset()
    loadImage(dataUrl, filePath)
    const img = new Image()
    img.onload = () => {
      setInputSize({ w: img.naturalWidth, h: img.naturalHeight })
      setOutputSize({ w: img.naturalWidth * currentScale, h: img.naturalHeight * currentScale })
    }
    img.src = dataUrl
  }

  useEffect(() => {
    if (!workingImage || mode === 'batch' || resultImage) return
    const img = new Image()
    img.onload = () => {
      setInputSize({ w: img.naturalWidth, h: img.naturalHeight })
      setOutputSize({ w: img.naturalWidth * currentScale, h: img.naturalHeight * currentScale })
    }
    img.src = workingImage
  }, [workingImage, currentScale, mode, resultImage])

  // Open file
  const handleOpenFile = async () => {
    if (window.electronAPI) {
      const handler = mode === 'batch' ? window.electronAPI.openFiles?.() : window.electronAPI.openFile()
      const result = await handler
      if (result) {
        if (mode === 'batch') {
          // 批量模式：只存路径，不读文件（懒加载缩略图）
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
          // 单图模式：读文件
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
      // 批量模式：只存 File 引用，不读文件（懒加载缩略图）
      const newItems: ImageQueueItem[] = Array.from(files).map(file => ({
        id: `img_${Date.now()}_${Math.random().toString(36).substr(1, 5)}`,
        fileName: file.name,
        file,
        status: 'pending' as const,
      }))
      setBatchItems(prev => [...prev, ...newItems])
      showToast('success', `已添加 ${newItems.length} 张图片到队列`)
    } else {
      // 单图模式：只处理第一个文件
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

  // Update output size when model changes
  const handleModelChange = (newModel: string) => {
    setUpscaleModel(newModel)
    setOutscale(null)  // 重置为新模型的默认倍率
    const scale = upscaleModelMeta[newModel]?.defaultOutscale ?? 4
    if (workingWidth > 0) {
      setOutputSize({ w: workingWidth * scale, h: workingHeight * scale })
    }
  }

  // Drag & drop
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const files = e.dataTransfer.files
    if (!files || files.length === 0) return

    if (mode === 'batch') {
      // 批量模式：只存 File 引用，不读文件（懒加载缩略图）
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
      // 单图模式：只处理第一个
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

  // 批量处理：递归逐张处理，每轮独立 task 确保 UI 及时刷新
  const processBatchItem = async (
    index: number,
    items: ImageQueueItem[],
    outputDir: string | undefined,
    isElectron: boolean,
    currentScale: number,
  ) => {
    if (index >= items.length) {
      setIsBatchProcessing(false)
      setBatchCurrentIndex(null)
      const successCount = items.filter(i => i.status === 'success').length
      const failedCount = items.filter(i => i.status === 'error').length
      showToast('success', `批量处理完成: ${successCount} 成功${failedCount > 0 ? `, ${failedCount} 失败` : ''}`)
      // ★ 内存清理：释放所有 base64 数据（结果路径已保存在 resultPath）
      setBatchItems(prev => prev.map(i => {
        const { dataUrl, resultDataUrl, ...rest } = i as any
        return rest as ImageQueueItem
      }))
      return
    }

    // 更新当前索引和状态，然后等下一个 task 再继续（让 React 刷新 UI）
    setBatchCurrentIndex(index)
    items[index] = { ...items[index], status: 'processing' }
    setBatchItems([...items])

    // 用 setTimeout 拆到下一个 task，React 会在 task 边界刷新 UI
    await new Promise(resolve => setTimeout(resolve, 0))

    try {
      // 获取图片数据
      let imageBase64: string
      const item = items[index]
      const { filePath, dataUrl, file } = item
      if (isElectron && filePath) {
        imageBase64 = await window.electronAPI.readImageFile(filePath)
      } else if (dataUrl) {
        imageBase64 = dataUrl
      } else if (file) {
        imageBase64 = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => resolve(reader.result as string)
          reader.onerror = () => reject(reader.error)
          reader.readAsDataURL(file)
        })
      } else {
        throw new Error('无图片数据')
      }

      // Electron 模式下传 output_path，让后端直接保存文件不返回 base64
      const outPath = (isElectron && outputDir)
        ? `${outputDir}/${item.fileName.replace(/\.[^.]+$/, '')}_upscaled.png`
        : undefined

      const result = await upscale({
        image: imageBase64,
        model: upscaleModel,
        device,
        outscale: currentScale,
        tile: tile ?? (upscaleTile || 0),
        output_path: outPath,
      })

      // 保存结果
      if (result.outputPath) {
        // Electron 模式：后端已直接保存，只返回文件路径
        items[index] = { ...items[index], status: 'success', resultPath: result.outputPath }
      } else if (result.image) {
        // Web 模式：存 base64
        items[index] = { ...items[index], status: 'success', resultDataUrl: `data:image/png;base64,${result.image}` }
      } else {
        throw new Error('处理失败：无返回结果')
      }
    } catch (err: any) {
      items[index] = { ...items[index], status: 'error', error: err.message }
    }

    setBatchItems([...items])
    // 递归处理下一张（下一个 task）
    await processBatchItem(index + 1, items, outputDir, isElectron, currentScale)
  }

  // Process - Batch mode
  const handleBatchUpscale = async () => {
    if (batchItems.length === 0) return

    // 检查模型状态
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

    setIsBatchProcessing(true)
    const isElectron = !!window.electronAPI
    const newItems = batchItems.map(item => ({ ...item, status: 'pending' as ImageQueueItem['status'] })) as ImageQueueItem[]
    setBatchItems(newItems)

    // Electron 模式：选择输出目录（仅一次）
    let outputDir: string | undefined = undefined
    if (isElectron) {
      const dir = await window.electronAPI.openDirectory?.()
      if (!dir) {
        setIsBatchProcessing(false)
        return
      }
      outputDir = dir
    }

    const currentScale = outscale ?? 4

    try {
      await processBatchItem(0, newItems, outputDir, isElectron, currentScale)
    } catch (err: any) {
      showToast('error', err.message)
      setIsBatchProcessing(false)
      setBatchCurrentIndex(null)
    }
  }

  // Process - Single mode
  const handleUpscale = async () => {
    if (!workingImage) return

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

    // Electron 模式下构造 output_path，让后端直接写文件，避免 base64 撑爆内存
    let outputPath: string | undefined = undefined
    if (window.electronAPI && sourceFilePath) {
      outputPath = sourceFilePath.replace(/\.[^.]+$/, '') + '_upscaled.png'
    }

    try {
      startProcess(upscaleModel)
      setCompareBefore(workingImage)
      const result = await upscale({
        image: workingImage,
        model: upscaleModel,
        device,
        outscale: currentScale,
        tile: tile ?? (upscaleTile || 0),
        output_path: outputPath,
      })

      // 后端直接保存了文件（Electron 模式），读文件并显示
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
      finishProcess('超分辨率处理完成')
      showToast('success', `超分辨率完成: ${result.width}x${result.height}`)
    } catch (err: any) {
      setError(err.message)
      showToast('error', err.message)
    }
  }

  // Save single image
  const handleSave = async () => {
    if (!resultImage) return
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
    loadSource(resultImage, sourceFilePath ?? undefined)
    showToast('success', '已将处理结果设为原图')
  }

  // 批量模式：移除单张图片
  const handleRemoveBatchItem = (id: string) => {
    setBatchItems(prev => prev.filter(item => item.id !== id))
  }

  // 批量模式：清空队列
  const handleClearBatch = () => {
    setBatchItems([])
    setBatchCurrentIndex(null)
  }

  // 批量模式：添加图片到队列（只存路径/引用，不读文件）
  const handleAddToBatch = (files: FileList | string[]) => {
    if (Array.isArray(files)) {
      // 文件路径数组（Electron 模式）：只存路径，不读文件
      const newItems: ImageQueueItem[] = (files as string[]).map(filePath => ({
        id: `img_${Date.now()}_${Math.random().toString(36).substr(1, 5)}`,
        fileName: filePath.split(/[\\/]/).pop() || 'unknown.png',
        filePath,
        status: 'pending' as const,
      }))
      setBatchItems(prev => [...prev, ...newItems])
      showToast('success', `已添加 ${newItems.length} 张图片到队列`)
    } else {
      // FileList（Web 模式）：只存 File 引用，不读文件
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

  // 懒加载单张缩略图（进入视口时由 BatchImageQueue 触发）
  const loadBatchThumbnail = useCallback(async (id: string) => {
    const item = batchItems.find(i => i.id === id)
    if (!item || item.dataUrl) return

    try {
      let dataUrl: string
      if (item.filePath && window.electronAPI) {
        dataUrl = await window.electronAPI.readImageFile(item.filePath)
      } else if (item.file) {
        dataUrl = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => resolve(reader.result as string)
          reader.onerror = () => reject(reader.error)
          reader.readAsDataURL(item.file!)
        })
      } else {
        return
      }
      setBatchItems(prev => prev.map(i => i.id === id ? { ...i, dataUrl } : i))
    } catch (err: any) {
      console.error('缩略图加载失败:', err)
    }
  }, [batchItems, setBatchItems])

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <PageHeader
        title="超分辨率"
        subtitle={
          mode === 'batch'
            ? `${batchItems.length} 张图片 | 输出: ${currentScale}x | ${upscaleModel.replace('RealESRGAN_', '')}`
            : inputSize.w > 0 ? `${inputSize.w}x${inputSize.h} → ${outputSize.w}x${outputSize.h}` : undefined
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

      {/* Content */}
      <div className="flex-1 flex overflow-hidden min-h-0">
        {/* Image area */}
        <div
          className="flex-1 bg-[#1a1a1a] flex items-center justify-center relative"
          onDrop={handleDrop}
          onDragOver={handleDragOver}
        >
          {mode === 'batch' ? (
            /* 批量模式：显示图片队列 */
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
                afterLabel={`${currentScale}x (${outputSize.w}x${outputSize.h})`}
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
              onImageLoad={(w, h) => setInputSize({ w, h })}
            />
          )}
          <input ref={fileInputRef} type="file" accept="image/*" multiple={mode === 'batch'} className="hidden" onChange={handleFileInput} />
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
                if (workingWidth > 0) {
                  setOutputSize({ w: workingWidth * val, h: workingHeight * val })
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

          {/* Tile 设置 */}
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

          {/* Image info - Single mode only */}
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
          )}

          {/* Batch Progress */}
          {mode === 'batch' && isBatchProcessing && (
            <BatchProgress
              current={batchCurrentIndex || 0}
              total={batchItems.length}
            />
          )}

          {/* Action */}
          <div className="mt-auto pt-2 space-y-2">
            {mode === 'batch' ? (
              <>
                <Button
                  onClick={handleBatchUpscale}
                  disabled={batchItems.length === 0 || isBatchProcessing}
                  loading={isBatchProcessing}
                  className="w-full"
                  size="lg"
                >
                  {isBatchProcessing ? '处理中...' : `开始批量处理 (${batchItems.length} 张)`}
                </Button>

                {batchItems.some(i => i.status === 'success') && (
                  <Button
                    variant="ghost"
                    onClick={async () => {
                      if (window.electronAPI) {
                        const dir = await window.electronAPI.openDirectory?.()
                        if (dir) {
                          showToast('success', `结果已保存至: ${dir}`)
                        }
                      } else {
                        showToast('info', 'Web 模式下结果已在队列中显示')
                      }
                    }}
                    className="w-full"
                    size="sm"
                  >
                    保存全部结果
                  </Button>
                )}
              </>
            ) : (
              <>
                {(() => {
                  const task = downloadTasks[upscaleModel]
                  const isModelBusy = task?.status === DownloadStatus.DOWNLOADING || task?.status === DownloadStatus.QUEUED
                  return (
                    <Button
                      onClick={handleUpscale}
                      disabled={!workingImage || isProcessing || isModelBusy}
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
              </>
            )}

            {/* 选择图片按钮（批量模式） */}
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
