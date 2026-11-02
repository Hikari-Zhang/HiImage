import { useRef, useCallback } from 'react'
import { useUpscaleBatchStore } from '../stores/useUpscaleBatchStore'
import { Upload, Download, Images, List, CheckCircle2 } from 'lucide-react'
import { Button, Select, Progress, showToast } from '../components/ui'
import ImageCanvas from '../components/ImageCanvas'
import ImageCompare from '../components/ImageCompare'
import PageHeader from '../components/layout/PageHeader'
import SendToEditor from '../components/SendToEditor'
import ModelSelect from '../components/ModelSelect/ModelSelect'
import BatchImageQueue, { type ImageQueueItem } from '../components/BatchImageQueue'
import BatchProgress from '../components/BatchProgress'
import { useUpscaleImageStore } from '../stores/useImageStore'
import { isCurrentTask, useSessionProcess } from '../stores/useProcessStore'
import { useEditorSettings } from '../stores/useEditorSettings'
import { useModelStore } from '../stores/useModelStore'
import { useBackendAPI } from '../hooks/useBackendAPI'
import { useDeviceOptions } from '../hooks/useDeviceOptions'
import { useDownloadStore } from '../stores/useDownloadStore'
import { useDownloadManager } from '../hooks/useDownloadManager'
import { DownloadStatus, ModelStatus } from '../constants'
import { fileToDataUrl, downloadDataUrl } from '../services/fileService'
import { downloadBlob, dataUrlToBlob } from '../services/fileService'
import JSZip from 'jszip'
import { resolveOutscale } from '../services/upscale'
import {
  MAX_BATCH_FILES,
  MAX_RESULT_BYTES,
  applyBatchResultBudget,
  dataUrlByteLength,
  validateBatchFiles,
  estimateBatchMemory,
  resultDataUrlsByteLength,
  validateBatchBudget,
  validateZipBudgetBytes,
} from '../services/batchLimits'
import {
  MAX_OUTPUT_RGBA_BYTES,
  estimateOutputRgbaBytes,
  readImageDimensions,
  validateOutputRgbaBudget,
} from '../services/imageDimensions'
import { uniqueOutputName } from '../services/batchExport'
import { batchFileKey, prepareBatchFiles } from '../services/batchImport'
import { createTaskId } from '../services/taskId'
import { createTaskProgressSession } from '../services/taskProgress'

export default function SuperResolution() {
  const {
    sourceRevision, sourceImage, imageWidth, imageHeight, resultImage, resultWidth, resultHeight,
    setSourceImage, setImageDimensions, setResultImage,
  } = useUpscaleImageStore()
  const {
    mode, setMode, batchItems, setBatchItems, batchCurrentIndex, setBatchCurrentIndex,
    isBatchProcessing, setIsBatchProcessing,
  } = useUpscaleBatchStore()
  const { isProcessing, progress, statusMessage, startProcess, finishProcess, setError } = useSessionProcess(mode === 'batch' ? 'upscale-batch' : 'upscale')
  const { upscale } = useBackendAPI()
  const { device, upscaleModel, upscaleTile, setDevice, setUpscaleModel, outscale, setOutscale, tile, setTile } = useEditorSettings('upscale')
  const { upscaleGroups, upscaleModelMeta } = useModelStore()
  const { options: deviceOptions } = useDeviceOptions({ device, setDevice })
  const downloadTasks = useDownloadStore((s) => s.tasks)
  const { startDownload } = useDownloadManager()

  const fileInputRef = useRef<HTMLInputElement>(null)

  // 当前模型的默认 outscale（从 store 读，fallback 4）
  const defaultOutscale = upscaleModelMeta[upscaleModel]?.defaultOutscale ?? 4
  // 实际使用的倍率（用户覆盖优先）
  const currentScale = outscale ?? defaultOutscale
  const outputSize = resultImage
    ? { w: resultWidth, h: resultHeight }
    : { w: imageWidth * currentScale, h: imageHeight * currentScale }
  // 当前模型是否支持自定义输出倍率
  const supportsCustomOutscale = upscaleModelMeta[upscaleModel]?.supportsCustomOutscale ?? false
  const zipResultBytes = resultDataUrlsByteLength(
    batchItems.flatMap((item) => item.resultDataUrl ? [item.resultDataUrl] : []),
  )
  const zipBudgetError = validateZipBudgetBytes(zipResultBytes)

  // 加载当前超分编辑会话的原图。
  const loadSource = (dataUrl: string) => {
    if (mode === 'batch') {
      // 批量模式：添加到队列
      const img = new Image()
      img.onload = () => {
        const newItem: ImageQueueItem = {
          id: `img_${Date.now()}_${Math.random().toString(36).substr(1, 5)}`,
          fileName: `image_${batchItems.length + 1}.png`,
          dataUrl: dataUrl,
          status: 'pending',
        }
        setBatchItems(prev => [...prev, newItem])
        showToast('success', `已添加到队列: ${newItem.fileName}`)
      }
      img.src = dataUrl
      return
    }
    // 单图模式：原有逻辑
    setSourceImage(dataUrl)
    const revision = useUpscaleImageStore.getState().sourceRevision
    const img = new Image()
    img.onload = () => {
      setImageDimensions(img.naturalWidth, img.naturalHeight, revision)
    }
    img.src = dataUrl
  }

  // Open file
  const handleOpenFile = () => fileInputRef.current?.click()

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    // FileList is live: copy it before clearing the input for same-file re-selection.
    const files = Array.from(e.target.files ?? [])
    if (files.length === 0) return
    e.target.value = ''

    if (mode === 'batch') {
      // 批量模式：只存 File 引用，不读文件（懒加载缩略图）
      const validation = validateBatchFiles(Array.from(files))
      let accepted = validation.accepted.slice(0, Math.max(0, MAX_BATCH_FILES - batchItems.length))
      const budgetError = validateBatchBudget(
        [...batchItems.flatMap((item) => item.file ? [item.file] : []), ...accepted],
        currentScale,
        0,
        resultDataUrlsByteLength(batchItems.flatMap((item) => item.resultDataUrl ? [item.resultDataUrl] : [])),
      )
      if (budgetError) {
        showToast('error', budgetError)
        accepted = []
      }
      if (validation.rejected.length || accepted.length < validation.accepted.length) {
        showToast('warning', [...validation.rejected, accepted.length < validation.accepted.length ? `队列最多 ${MAX_BATCH_FILES} 张图片` : ''].filter(Boolean).join('；'))
      }
      const newItems: ImageQueueItem[] = accepted.map(file => ({
        id: `img_${Date.now()}_${Math.random().toString(36).substr(1, 5)}`,
        fileName: file.name,
        file,
        status: 'pending' as const,
      }))
      setBatchItems(prev => [...prev, ...newItems])
      if (newItems.length > 0) showToast('success', `已添加 ${newItems.length} 张图片到队列`)
    } else {
      // 单图模式：只处理第一个文件
      const file = files[0]
      fileToDataUrl(file).then(loadSource).catch((err) => showToast('error', `读取图片失败: ${err.message}`))
    }
  }

  // Update output size when model changes
  const handleModelChange = (newModel: string) => {
    setUpscaleModel(newModel)
    setOutscale(null)  // 重置为新模型的默认倍率
  }

  // Drag & drop
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const files = e.dataTransfer.files
    if (!files || files.length === 0) return

    if (mode === 'batch') {
      // 批量模式：只存 File 引用，不读文件（懒加载缩略图）
      const validation = validateBatchFiles(Array.from(files))
      let accepted = validation.accepted.slice(0, Math.max(0, MAX_BATCH_FILES - batchItems.length))
      const budgetError = validateBatchBudget(
        [...batchItems.flatMap((item) => item.file ? [item.file] : []), ...accepted],
        currentScale,
        0,
        resultDataUrlsByteLength(batchItems.flatMap((item) => item.resultDataUrl ? [item.resultDataUrl] : [])),
      )
      if (budgetError) {
        showToast('error', budgetError)
        accepted = []
      }
      if (validation.rejected.length || accepted.length < validation.accepted.length) {
        showToast('warning', [...validation.rejected, accepted.length < validation.accepted.length ? `队列最多 ${MAX_BATCH_FILES} 张图片` : ''].filter(Boolean).join('；'))
      }
      const newItems: ImageQueueItem[] = accepted
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
      fileToDataUrl(file).then(loadSource).catch((err) => showToast('error', `读取图片失败: ${err.message}`))
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
    currentScale: number,
  ) => {
    if (index >= items.length) {
      setIsBatchProcessing(false)
      setBatchCurrentIndex(null)
      const successCount = items.filter(i => i.status === 'success').length
      const failedCount = items.filter(i => i.status === 'error').length
      showToast('success', `批量处理完成: ${successCount} 成功${failedCount > 0 ? `, ${failedCount} 失败` : ''}`)
      return
    }
    if (items[index].status === 'success' && items[index].resultDataUrl) {
      await processBatchItem(index + 1, items, currentScale)
      return
    }

    // 更新当前索引和状态，然后等下一个 task 再继续（让 React 刷新 UI）
    setBatchCurrentIndex(index)
    items[index] = { ...items[index], status: 'processing' }
    setBatchItems([...items])

    // 用 setTimeout 拆到下一个 task，React 会在 task 边界刷新 UI
    await new Promise(resolve => setTimeout(resolve, 0))

    const taskId = createTaskId()
    const session = createTaskProgressSession(taskId, { isCurrent: () => isCurrentTask(taskId) })
    startProcess(upscaleModel, taskId)
    const stopStaleBatch = () => {
      setIsBatchProcessing(false)
      setBatchCurrentIndex(null)
    }

    try {
      const result = await session.run(async (requestTaskId) => {
        let imageBase64: string
        const item = items[index]
        const { dataUrl, file } = item
        if (dataUrl) {
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
        if (!isCurrentTask(requestTaskId)) throw new Error('任务已被新任务替代')
        return upscale({
          image: imageBase64,
          model: upscaleModel,
          device,
          outscale: currentScale,
          tile: tile ?? (upscaleTile || 0),
          task_id: requestTaskId,
        })
      })
      if (!isCurrentTask(taskId)) {
        stopStaleBatch()
        return
      }

      // 保存结果
      if (result.image) {
        const resultDataUrl = `data:image/png;base64,${result.image}`
        const retainedResultBytes = resultDataUrlsByteLength(
          items.flatMap((candidate, candidateIndex) =>
            candidateIndex !== index && candidate.resultDataUrl ? [candidate.resultDataUrl] : [],
          ),
        )
        if (retainedResultBytes + dataUrlByteLength(resultDataUrl) > MAX_RESULT_BYTES) {
          const decision = applyBatchResultBudget(items[index], resultDataUrl, retainedResultBytes)
          items[index] = decision.item
          setBatchItems([...items])
          setIsBatchProcessing(false)
          setBatchCurrentIndex(null)
          setError(taskId, items[index].error!)
          showToast('error', items[index].error!)
          return
        }
        items[index] = applyBatchResultBudget(
          items[index],
          resultDataUrl,
          retainedResultBytes,
        ).item
      } else {
        throw new Error('处理失败：无返回结果')
      }
      finishProcess(taskId, `第 ${index + 1} 张处理完成`)
    } catch (err: any) {
      if (!isCurrentTask(taskId)) {
        stopStaleBatch()
        return
      }
      items[index] = { ...items[index], status: 'error', error: err.message }
      setError(taskId, err.message)
    }

    setBatchItems([...items])
    // 递归处理下一张（下一个 task）
    await processBatchItem(index + 1, items, currentScale)
  }

  // Process - Batch mode
  const handleBatchUpscale = async () => {
    if (batchItems.length === 0 || useUpscaleBatchStore.getState().isBatchProcessing) return

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

    const newItems = batchItems.map((item) =>
      item.status === 'success' && item.resultDataUrl
        ? item
        : { ...item, status: 'pending' as ImageQueueItem['status'] },
    ) as ImageQueueItem[]
    const currentScale = resolveOutscale(outscale, defaultOutscale)
    const budgetError = validateBatchBudget(
      newItems.flatMap((item) => item.file ? [item.file] : []),
      currentScale,
      0,
      resultDataUrlsByteLength(newItems.flatMap((item) => item.resultDataUrl ? [item.resultDataUrl] : [])),
    )
    if (budgetError) {
      setIsBatchProcessing(false)
      showToast('error', budgetError)
      return
    }

    setIsBatchProcessing(true)
    try {
      const sources = newItems.flatMap((item) => {
        if (item.status === 'success' && item.resultDataUrl) return []
        const source = item.file ?? item.dataUrl
        return source ? [source] : []
      })
      const dimensions = await Promise.all(sources.map(readImageDimensions))
      const outputBudgetError = validateOutputRgbaBudget(dimensions, currentScale)
      if (outputBudgetError) {
        setIsBatchProcessing(false)
        showToast('error', outputBudgetError)
        return
      }
      setIsBatchProcessing(true)
      setBatchItems(newItems)
      await processBatchItem(0, newItems, currentScale)
    } catch (err: any) {
      showToast('error', err.message)
      setIsBatchProcessing(false)
      setBatchCurrentIndex(null)
    }
  }

  // Process - Single mode
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

    const taskId = createTaskId()
    const session = createTaskProgressSession(taskId, { isCurrent: () => isCurrentTask(taskId) })
    try {
      startProcess(upscaleModel, taskId)
      const dimensions = imageWidth > 0 && imageHeight > 0
        ? { width: imageWidth, height: imageHeight }
        : await readImageDimensions(sourceImage)
      if (estimateOutputRgbaBytes(dimensions, currentScale) > MAX_OUTPUT_RGBA_BYTES) {
        const message = '预计输出 RGBA 超过 256 MB，请降低倍率或使用更小的图片'
        setError(taskId, message)
        showToast('error', message)
        return
      }
      const result = await session.run((requestTaskId) => upscale({
        image: sourceImage,
        model: upscaleModel,
        device,
        outscale: currentScale,
        tile: tile ?? (upscaleTile || 0),
        task_id: requestTaskId,
      }))

      if (!isCurrentTask(taskId)) return
      setResultImage(`data:image/png;base64,${result.image}`, result.width, result.height)
      finishProcess(taskId, '超分辨率处理完成')
      showToast('success', `超分辨率完成: ${result.width}x${result.height}`)
    } catch (err: any) {
      if (!isCurrentTask(taskId)) return
      setError(taskId, err.message)
      showToast('error', err.message)
    }
  }

  // Save single image
  const handleSave = async () => {
    if (!resultImage) return
    const defaultName = 'image_upscaled.png'
    await downloadDataUrl(resultImage, defaultName)
    showToast('success', '结果已下载')
  }

  // 将处理结果设为原图
  const handleUseResultAsSource = () => {
    if (!resultImage) return
    // 保留原始文件路径，保存时文件名仍以原图名为准
    loadSource(resultImage)
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
    const validation = validateBatchFiles(Array.from(files as FileList))
    const existingKeys = new Set(batchItems.filter((item) => item.file).map((item) => batchFileKey(item.file!)))
    const prepared = prepareBatchFiles(validation.accepted, existingKeys, Math.max(0, MAX_BATCH_FILES - batchItems.length))
    const budgetError = validateBatchBudget(
      [
        ...batchItems.flatMap((item) => item.file ? [item.file] : []),
        ...prepared.accepted,
      ],
      currentScale,
      0,
      resultDataUrlsByteLength(batchItems.flatMap((item) => item.resultDataUrl ? [item.resultDataUrl] : [])),
    )
    if (budgetError) {
      showToast('error', budgetError)
      prepared.accepted.length = 0
    }
    if (validation.rejected.length || prepared.accepted.length < validation.accepted.length) {
      showToast('warning', [...validation.rejected, prepared.accepted.length < validation.accepted.length ? `队列最多 ${MAX_BATCH_FILES} 张图片或存在重复文件` : ''].filter(Boolean).join('；'))
    }
    const newItems: ImageQueueItem[] = prepared.accepted.map(file => ({
        id: `img_${Date.now()}_${Math.random().toString(36).substr(1, 5)}`,
        fileName: file.name,
        file,
        status: 'pending' as const,
      }))
    if (newItems.length > 0) {
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
      if (item.file) {
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
            : imageWidth > 0 ? `${imageWidth}x${imageHeight} → ${outputSize.w}x${outputSize.h}` : undefined
        }
        right={
          <div className="flex items-center gap-1">
            {mode === 'single' && <SendToEditor image={resultImage} current="upscale" />}
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
        }
      />

      {/* Content */}
      <div className="editor-workspace flex-1 flex overflow-hidden min-h-0">
        {/* Image area */}
        <div
          className="flex-1 min-w-0 bg-workspace flex items-center justify-center relative"
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
                onDownload={(id) => {
                  const item = batchItems.find((candidate) => candidate.id === id)
                  if (!item?.resultDataUrl) return
                  void downloadDataUrl(
                    item.resultDataUrl,
                    uniqueOutputName(item.fileName, new Set<string>()),
                  )
                }}
                disabled={isBatchProcessing}
              />
            </div>
          ) : !sourceImage ? (
            <div className="empty-canvas flex flex-col items-center gap-4 text-fg-secondary">
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
                  key={sourceRevision}
              imageSrc={sourceImage}
              rois={[]}
              selectedROIs={[]}
              tool="pan"
              onROIDrawn={() => {}}
              onROISelect={() => {}}
              onImageLoad={(w, h) => setImageDimensions(w, h, sourceRevision)}
            />
          )}
          <input ref={fileInputRef} type="file" accept="image/*" multiple={mode === 'batch'} className="hidden" onChange={handleFileInput} />
        </div>

        {/* Control panel */}
        <div className="editor-inspector w-[280px] bg-bg-secondary border-l border-border-subtle p-3 flex flex-col gap-4 overflow-y-auto min-h-0">
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
          )}

          {/* Batch Progress */}
          {mode === 'batch' && isBatchProcessing && (
            <BatchProgress
              current={batchCurrentIndex || 0}
              total={batchItems.length}
            />
          )}
          {mode === 'batch' && batchItems.length > 0 && (
            <p className="text-[10px] text-fg-tertiary">
              批量上限 {MAX_BATCH_FILES} 张；预计峰值内存约 {Math.ceil(
                estimateBatchMemory(batchItems.flatMap((item) => item.file ? [item.file] : []), currentScale) / 1024 / 1024
              )} MB。单文件上限 20 MB；ZIP 会额外占用内存，超限时请分批或逐张下载。
            </p>
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
                      try {
                        const results = batchItems.filter((item) => item.status === 'success' && item.resultDataUrl)
                        const resultBytes = resultDataUrlsByteLength(
                          results.map((item) => item.resultDataUrl!),
                        )
                        const currentZipBudgetError = validateZipBudgetBytes(resultBytes)
                        if (currentZipBudgetError) {
                          showToast('error', currentZipBudgetError)
                          return
                        }
                        const zip = new JSZip()
                        const usedNames = new Set<string>()
                        for (const item of results) {
                          zip.file(uniqueOutputName(item.fileName, usedNames), await dataUrlToBlob(item.resultDataUrl!))
                        }
                        await downloadBlob(await zip.generateAsync({ type: 'blob' }), 'upscaled-results.zip')
                        showToast('success', `已下载 ${results.length} 个结果`)
                      } catch (err) {
                        showToast('error', `保存全部结果失败: ${err instanceof Error ? err.message : String(err)}`)
                      }
                    }}
                    className="w-full"
                    size="sm"
                    disabled={Boolean(zipBudgetError)}
                    title={zipBudgetError ?? undefined}
                  >
                    {zipBudgetError ? 'ZIP 超过 64 MB，请逐张下载' : '保存全部结果'}
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
