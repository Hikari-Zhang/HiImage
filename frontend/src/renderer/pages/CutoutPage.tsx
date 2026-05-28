/**
 * 抠图页面 —— AI 智能抠图，支持画笔微调与边缘精修
 */
import { useState, useRef, useCallback, useEffect, useMemo } from 'react'
import {
  Upload, Download, Undo2, Eraser, Paintbrush, Eye, EyeOff,
  Scissors, Palette, RefreshCw,
} from 'lucide-react'
import { Stage, Layer, Image as KonvaImage, Line } from 'react-konva'
import Konva from 'konva'
import { Button, Slider, Progress, Select, showToast } from '../components/ui'
import ModelSelect from '../components/ModelSelect/ModelSelect'
import PageHeader from '../components/layout/PageHeader'
import { useCutoutStore } from '../stores/useCutoutStore'
import type { CutoutBrushMode } from '../stores/useCutoutStore'
import { useProcessStore } from '../stores/useProcessStore'
import { useSettingsStore } from '../stores/useSettingsStore'
import { useModelStore } from '../stores/useModelStore'
import { useBackendAPI } from '../hooks/useBackendAPI'
import { useDeviceOptions } from '../hooks/useDeviceOptions'
import { useFirstVisible } from '../hooks/useFirstVisible'
import { useDownloadStore } from '../stores/useDownloadStore'
import { useDownloadManager } from '../hooks/useDownloadManager'
import { DownloadStatus, ModelStatus } from '../constants'
import type { ModelGroup } from '../components/ModelSelect/ModelSelect'
import type { MaskStroke } from '../types/mask'
import { POINT_SAMPLE_RATIO } from '../types/mask'

// ── 棋盘格背景样式 ────────────────────────────────────────────────────────
const CHECKERBOARD_STYLE = {
  backgroundImage: `
    linear-gradient(45deg, #2a2a2a 25%, transparent 25%),
    linear-gradient(-45deg, #2a2a2a 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, #2a2a2a 75%),
    linear-gradient(-45deg, transparent 75%, #2a2a2a 75%)
  `,
  backgroundSize: '20px 20px',
  backgroundPosition: '0 0, 0 10px, 10px -10px, -10px 0px',
  backgroundColor: '#1a1a1a',
}

export default function CutoutPage({ isVisible = false }: { isVisible?: boolean }) {
  // ── Store 状态 ──
  const {
    sourceImage, sourceFilePath, resultImage, resultMask,
    imageWidth, imageHeight,
    brushStrokes, brushMode, brushSize,
    feather, dilate,
    showMask, showCheckerboard,
    setSourceImage, setImageDimensions,
    setResult, setResultMask,
    addBrushStroke, undoBrushStroke, clearBrushStrokes,
    setBrushMode, setBrushSize,
    setFeather, setDilate,
    toggleMask, toggleCheckerboard,
    reset,
  } = useCutoutStore()

  const { isProcessing, progress, statusMessage, startProcess, finishProcess, setError, reset: resetProcess } = useProcessStore()
  const { cutout, cutoutRefine, cutoutComposite, getCutoutModels } = useBackendAPI()
  const { device, cutoutModel, setDevice, setCutoutModel } = useSettingsStore()
  const { options: deviceOptions } = useDeviceOptions()
  const downloadTasks = useDownloadStore((s) => s.tasks)
  const { startDownload } = useDownloadManager()

  // ── 本地状态 ──
  const [bgColor, setBgColor] = useState('#FFFFFF')
  const [compositeResult, setCompositeResult] = useState<string | null>(null)

  // ── Konva 画布状态 ──
  const stageRef = useRef<Konva.Stage>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [containerSize, setContainerSize] = useState({ w: 0, h: 0 })
  const containerSizeRef = useRef({ w: 0, h: 0 })
  const [canvasScale, setCanvasScale] = useState(1)
  const [canvasOffset, setCanvasOffset] = useState({ x: 0, y: 0 })
  const [imageObj, setImageObj] = useState<HTMLImageElement | null>(null)
  const imageObjRef = useRef<HTMLImageElement | null>(null)

  // 画笔绘制状态
  const [isDrawing, setIsDrawing] = useState(false)
  const [currentPoints, setCurrentPoints] = useState<number[]>([])
  const rafRef = useRef<number | null>(null)
  const pendingPointRef = useRef<{ x: number; y: number } | null>(null)

  // ── 模型列表 ──
  const ready = useFirstVisible(isVisible)
  const [cutoutModelGroups, setCutoutModelGroups] = useState<ModelGroup[]>([])
  useEffect(() => {
    if (!ready) return
    getCutoutModels()
      .then((data) => {
        if (data?.groups) {
          const groups: ModelGroup[] = data.groups.map((g: any) => ({
            label: g.label,
            options: g.models.map((m: any) => ({
              value: m.id,
              label: m.name,
              description: m.description,
            })),
          }))
          setCutoutModelGroups(groups)
        }
      })
      .catch(() => {})
  }, [ready])

  // ── 加载结果图到 Konva ──
  const displayImage = compositeResult || resultImage

  useEffect(() => {
    if (!displayImage) {
      imageObjRef.current = null
      setImageObj(null)
      return
    }
    const img = new window.Image()
    img.onload = () => {
      imageObjRef.current = img
      setImageObj(img)
    }
    const src = displayImage.startsWith('data:') ? displayImage : `data:image/png;base64,${displayImage}`
    img.src = src
  }, [displayImage])

  // ── 加载 mask 图到 Konva（独立加载，确保 onload 完成） ──
  const [maskImageObj, setMaskImageObj] = useState<HTMLImageElement | null>(null)

  useEffect(() => {
    if (!showMask || !resultMask || compositeResult) {
      setMaskImageObj(null)
      return
    }
    const img = new window.Image()
    img.onload = () => setMaskImageObj(img)
    const maskSrc = resultMask.startsWith('data:') ? resultMask : `data:image/png;base64,${resultMask}`
    img.src = maskSrc
  }, [showMask, resultMask, compositeResult])

  // ── 容器尺寸 ──
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const observer = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect
      setContainerSize({ w: width, h: height })
      containerSizeRef.current = { w: width, h: height }
    })
    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  // ── 自适应视图 ──
  useEffect(() => {
    const img = imageObjRef.current
    const cs = containerSizeRef.current
    if (!img || cs.w === 0) return
    const scaleX = cs.w / img.naturalWidth
    const scaleY = cs.h / img.naturalHeight
    const s = Math.min(scaleX, scaleY) * 0.9
    setCanvasScale(s)
    setCanvasOffset({
      x: (cs.w - img.naturalWidth * s) / 2,
      y: (cs.h - img.naturalHeight * s) / 2,
    })
  }, [containerSize, displayImage])

  // ── 坐标转换 ──
  const stageToImage = useCallback(
    (stagePos: { x: number; y: number }): { x: number; y: number } | null => {
      const img = imageObjRef.current
      if (!img) return null
      const x = (stagePos.x - canvasOffset.x) / canvasScale
      const y = (stagePos.y - canvasOffset.y) / canvasScale
      if (x < 0 || y < 0 || x > img.naturalWidth || y > img.naturalHeight) return null
      return { x, y }
    },
    [canvasScale, canvasOffset]
  )

  // ── 滚轮缩放 ──
  const handleWheel = useCallback(
    (e: Konva.KonvaEventObject<WheelEvent>) => {
      e.evt.preventDefault()
      const stage = stageRef.current
      if (!stage || !imageObjRef.current) return
      const pos = stage.getPointerPosition()
      if (!pos) return

      const factor = e.evt.deltaY < 0 ? 1.15 : 0.87
      const newScale = Math.max(0.05, Math.min(20, canvasScale * factor))
      const dx = pos.x - canvasOffset.x
      const dy = pos.y - canvasOffset.y
      const cs = containerSizeRef.current
      const img = imageObjRef.current
      const imgW = img.naturalWidth * newScale
      const imgH = img.naturalHeight * newScale

      let newX = pos.x - dx * (newScale / canvasScale)
      let newY = pos.y - dy * (newScale / canvasScale)
      if (imgW > cs.w) newX = Math.min(0, Math.max(cs.w - imgW, newX))
      else newX = (cs.w - imgW) / 2
      if (imgH > cs.h) newY = Math.min(0, Math.max(cs.h - imgH, newY))
      else newY = (cs.h - imgH) / 2

      setCanvasScale(newScale)
      setCanvasOffset({ x: newX, y: newY })
    },
    [canvasScale, canvasOffset]
  )

  // ── 画笔点采样 ──
  const shouldAddPoint = useCallback(
    (x: number, y: number, points: number[]): boolean => {
      if (points.length < 2) return true
      const lastX = points[points.length - 2]
      const lastY = points[points.length - 1]
      const dist = Math.sqrt((x - lastX) ** 2 + (y - lastY) ** 2)
      return dist > brushSize / POINT_SAMPLE_RATIO
    },
    [brushSize]
  )

  // ── 鼠标事件 ──
  const handleMouseDown = useCallback(
    (e: Konva.KonvaEventObject<PointerEvent>) => {
      if (!displayImage || !imageObjRef.current) return
      const stage = stageRef.current
      if (!stage) return
      const pointerPos = stage.getPointerPosition()
      if (!pointerPos) return

      if (e.evt.button === 1) return

      const imgPos = stageToImage(pointerPos)
      if (!imgPos) return

      setIsDrawing(true)
      setCurrentPoints([imgPos.x, imgPos.y])
    },
    [displayImage, stageToImage]
  )

  const handleMouseMove = useCallback(
    (e: Konva.KonvaEventObject<PointerEvent>) => {
      if (!isDrawing) return
      const stage = stageRef.current
      if (!stage) return
      const pointerPos = stage.getPointerPosition()
      if (!pointerPos) return

      const imgPos = stageToImage(pointerPos)
      if (!imgPos) return

      pendingPointRef.current = imgPos
      if (rafRef.current === null) {
        rafRef.current = requestAnimationFrame(() => {
          const point = pendingPointRef.current
          if (point) {
            setCurrentPoints((prev) => {
              if (shouldAddPoint(point.x, point.y, prev)) {
                return [...prev, point.x, point.y]
              }
              return prev
            })
          }
          rafRef.current = null
        })
      }
    },
    [isDrawing, stageToImage, shouldAddPoint]
  )

  const handleMouseUp = useCallback(() => {
    if (isDrawing) {
      setIsDrawing(false)
      if (currentPoints.length >= 4) {
        const stroke: MaskStroke = {
          id: `stroke_${Date.now()}`,
          points: [...currentPoints],
          size: brushSize,
          hardness: 80,
          shape: 'circle',
          tool: brushMode === 'add_fg' ? 'add_fg' : 'remove_fg',
          timestamp: Date.now(),
        }
        addBrushStroke(stroke)
      }
      setCurrentPoints([])
    }
  }, [isDrawing, currentPoints, brushSize, brushMode, addBrushStroke])

  // ── 键盘事件 ──
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.code === 'KeyZ') {
        e.preventDefault()
        undoBrushStroke()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [undoBrushStroke])

  // 清理 rAF
  useEffect(() => {
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
    }
  }, [])

  // ── 文件上传 ──
  const fileInputRef = useRef<HTMLInputElement>(null)

  const loadSource = useCallback((dataUrl: string, filePath?: string) => {
    setCompositeResult(null)
    resetProcess()
    setSourceImage(dataUrl, filePath)
    const img = new Image()
    img.onload = () => setImageDimensions(img.width, img.height)
    img.src = dataUrl
  }, [setSourceImage, setImageDimensions, resetProcess])

  const handleFileInput = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (window.electronAPI) {
      window.electronAPI.readImageFile(file.path).then((dataUrl: string) => {
        loadSource(dataUrl, file.path)
      })
    } else {
      const reader = new FileReader()
      reader.onload = () => loadSource(reader.result as string)
      reader.readAsDataURL(file)
    }
    e.target.value = ''
  }, [loadSource])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    const file = e.dataTransfer.files[0]
    if (!file || !file.type.startsWith('image/')) return
    const reader = new FileReader()
    reader.onload = () => loadSource(reader.result as string)
    reader.readAsDataURL(file)
  }, [loadSource])

  const handleOpenFile = useCallback(async () => {
    if (window.electronAPI) {
      const filePath = await window.electronAPI.openFile()
      if (filePath) {
        const dataUrl = await window.electronAPI.readImageFile(filePath)
        loadSource(dataUrl, filePath)
      }
    } else {
      fileInputRef.current?.click()
    }
  }, [loadSource])

  // ── 模型下载状态感知 ──
  const currentTask = downloadTasks[cutoutModel]
  const modelReady = !currentTask
    || currentTask.status === ModelStatus.OK
    || currentTask.status === DownloadStatus.DONE
    || currentTask.status === DownloadStatus.CANCELLED
  const modelDownloading = currentTask?.status === DownloadStatus.DOWNLOADING
  const modelQueued = currentTask?.status === DownloadStatus.QUEUED

  const getButtonText = () => {
    if (isProcessing) return '处理中...'
    if (modelDownloading) return '模型下载中...'
    if (modelQueued) return '排队等待下载...'
    if (!modelReady) return '点击下载并抠图'
    return '开始抠图'
  }

  // ── 执行抠图 ──
  const handleCutout = useCallback(async () => {
    if (!sourceImage) return
    if (!modelReady) {
      startDownload(cutoutModel)
      return
    }
    startProcess(cutoutModel)
    setCompositeResult(null)
    clearBrushStrokes()

    try {
      const result = await cutout({
        image: sourceImage,
        model: cutoutModel,
        device,
      })
      setResult(result.image, result.mask)
      finishProcess('抠图完成')
      showToast('success', '抠图完成！')
    } catch (err: any) {
      setError(err.message || '抠图失败')
      showToast('error', err.message || '抠图失败')
    }
  }, [sourceImage, cutoutModel, device, modelReady, startDownload, cutout, setResult, startProcess, finishProcess, setError, clearBrushStrokes])

  // ── 遮罩精修 ──
  const handleRefine = useCallback(async () => {
    if (!resultMask || !sourceImage) return

    startProcess('refine')
    try {
      const addMask = generateBrushMask(
        brushStrokes.filter(s => s.tool === 'add_fg'),
        imageWidth, imageHeight,
      )
      const removeMask = generateBrushMask(
        brushStrokes.filter(s => s.tool === 'remove_fg'),
        imageWidth, imageHeight,
      )

      const result = await cutoutRefine({
        mask: resultMask,
        original_image: sourceImage,
        feather,
        dilate,
        brush_add: addMask || undefined,
        brush_remove: removeMask || undefined,
      })
      setResult(result.image, result.mask)
      finishProcess('精修完成')
      showToast('success', '边缘精修完成！')
    } catch (err: any) {
      setError(err.message || '精修失败')
      showToast('error', err.message || '精修失败')
    }
  }, [resultMask, sourceImage, feather, dilate, brushStrokes, imageWidth, imageHeight, cutoutRefine, setResult, startProcess, finishProcess, setError])

  // ── 合成到背景色 ──
  const handleComposite = useCallback(async () => {
    if (!resultImage) return
    try {
      const result = await cutoutComposite({
        image: resultImage,
        background_color: bgColor,
      })
      setCompositeResult(result.image)
      showToast('success', '合成完成！')
    } catch (err: any) {
      showToast('error', err.message || '合成失败')
    }
  }, [resultImage, bgColor, cutoutComposite])

  // ── 保存图片 ──
  const handleSave = useCallback(async () => {
    const imageToSave = compositeResult || resultImage
    if (!imageToSave) return
    if (window.electronAPI) {
      const defaultName = compositeResult ? 'cutout_composite.png' : 'cutout_result.png'
      const filePath = await window.electronAPI.saveFile(defaultName)
      if (filePath) {
        await window.electronAPI.saveImageFile(filePath, imageToSave.startsWith('data:') ? imageToSave : `data:image/png;base64,${imageToSave}`)
        showToast('success', '图片已保存')
      }
    } else {
      const a = document.createElement('a')
      const dataUrl = imageToSave.startsWith('data:') ? imageToSave : `data:image/png;base64,${imageToSave}`
      a.href = dataUrl
      a.download = compositeResult ? 'cutout_composite.png' : 'cutout_result.png'
      a.click()
    }
  }, [resultImage, compositeResult])

  // ── 渲染画笔笔划 ──
  const renderedBrushStrokes = useMemo(() => {
    return brushStrokes.map((stroke) => {
      const isRemove = stroke.tool === 'remove_fg'
      return (
        <Line
          key={stroke.id}
          points={stroke.points.map((p, i) =>
            i % 2 === 0 ? p * canvasScale + canvasOffset.x : p * canvasScale + canvasOffset.y
          )}
          stroke={isRemove ? '#3b82f6' : '#ef4444'}
          strokeWidth={stroke.size * canvasScale}
          lineCap="round"
          lineJoin="round"
          opacity={0.5}
        />
      )
    })
  }, [brushStrokes, canvasScale, canvasOffset])

  // ── 渲染当前正在画的笔划 ──
  const renderedCurrentStroke = useMemo(() => {
    if (!isDrawing || currentPoints.length < 2) return null
    return (
      <Line
        points={currentPoints.map((p, i) =>
          i % 2 === 0 ? p * canvasScale + canvasOffset.x : p * canvasScale + canvasOffset.y
        )}
        stroke={brushMode === 'add_fg' ? '#ef4444' : '#3b82f6'}
        strokeWidth={brushSize * canvasScale}
        lineCap="round"
        lineJoin="round"
        opacity={0.5}
      />
    )
  }, [isDrawing, currentPoints, brushMode, brushSize, canvasScale, canvasOffset])

  // ── 渲染 ──────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full">
      <PageHeader title="抠图" />

      <div className="flex flex-1 min-h-0">
        {/* ── 左侧画布区域 ── */}
        <div
          ref={containerRef}
          className="flex-1 relative overflow-hidden"
          onDrop={handleDrop}
          onDragOver={(e) => e.preventDefault()}
        >
          {!sourceImage ? (
            /* 上传提示 */
            <div
              className="absolute inset-0 flex flex-col items-center justify-center cursor-pointer"
              onClick={handleOpenFile}
            >
              <div className="w-16 h-16 rounded-full bg-bg-hover flex items-center justify-center mb-4">
                <Upload size={28} className="text-fg-secondary" />
              </div>
              <p className="text-fg-secondary text-sm">拖拽或点击上传图片</p>
              <p className="text-fg-secondary/60 text-xs mt-1">支持 PNG / JPG / WebP</p>
            </div>
          ) : !resultImage ? (
            /* 原图预览 */
            <div className="absolute inset-0 flex items-center justify-center p-4">
              <img
                src={sourceImage}
                alt="原图"
                className="max-w-full max-h-full object-contain rounded"
                style={{ imageRendering: 'auto' }}
              />
            </div>
          ) : (
            /* 抠图结果 + Konva 交互画布 */
            <div
              className="absolute inset-0"
              style={showCheckerboard ? CHECKERBOARD_STYLE : undefined}
            >
              {containerSize.w > 0 && imageObj && (
                <Stage
                  ref={stageRef}
                  width={containerSize.w}
                  height={containerSize.h}
                  onMouseDown={handleMouseDown}
                  onMouseMove={handleMouseMove}
                  onMouseUp={handleMouseUp}
                  onMouseLeave={handleMouseUp}
                  onWheel={handleWheel}
                  style={{ cursor: 'crosshair' }}
                >
                  {/* 图像层 */}
                  <Layer imageSmoothingEnabled>
                    <KonvaImage
                      image={imageObj}
                      x={canvasOffset.x}
                      y={canvasOffset.y}
                      width={imageObj.naturalWidth * canvasScale}
                      height={imageObj.naturalHeight * canvasScale}
                    />
                    {/* Mask 叠加层 */}
                    {maskImageObj && (
                      <KonvaImage
                        image={maskImageObj}
                        x={canvasOffset.x}
                        y={canvasOffset.y}
                        width={imageObj.naturalWidth * canvasScale}
                        height={imageObj.naturalHeight * canvasScale}
                        opacity={0.3}
                      />
                    )}
                  </Layer>

                  {/* 画笔编辑层 */}
                  <Layer>
                    {renderedBrushStrokes}
                    {renderedCurrentStroke}
                  </Layer>
                </Stage>
              )}
            </div>
          )}

          {/* 进度条 */}
          {isProcessing && (
            <div className="absolute bottom-4 left-4 right-4">
              <Progress value={progress} label={statusMessage} />
            </div>
          )}

          {/* 隐藏文件输入 */}
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={handleFileInput}
          />
        </div>

        {/* ── 右侧控制面板 ── */}
        <div className="w-[240px] flex-shrink-0 border-l border-border-subtle bg-bg-secondary overflow-y-auto p-3 flex flex-col gap-3">
          {/* 模型选择 */}
          <ModelSelect
            label="抠图模型"
            value={cutoutModel}
            onChange={setCutoutModel}
            groups={cutoutModelGroups.length > 0 ? cutoutModelGroups : undefined}
          />

          {/* 设备选择 */}
          <Select
            label="计算设备"
            value={device}
            onChange={(e) => setDevice(e.target.value)}
            options={deviceOptions.map((d) => ({
              value: d.value,
              label: d.label,
              disabled: d.disabled,
            }))}
          />

          {/* ── 边缘精修 ── */}
          {resultImage && (
            <div className="border border-border-subtle rounded p-2.5 space-y-3">
              <h3 className="text-xs font-medium text-fg-primary">边缘精修</h3>

              {/* 画笔模式 */}
              <div className="flex gap-1">
                <Button
                  size="sm"
                  variant={brushMode === 'add_fg' ? 'primary' : 'secondary'}
                  onClick={() => setBrushMode('add_fg')}
                  icon={<Paintbrush size={12} />}
                  className="flex-1 text-[10px]"
                >
                  添加前景
                </Button>
                <Button
                  size="sm"
                  variant={brushMode === 'remove_fg' ? 'primary' : 'secondary'}
                  onClick={() => setBrushMode('remove_fg')}
                  icon={<Eraser size={12} />}
                  className="flex-1 text-[10px]"
                >
                  擦除前景
                </Button>
              </div>

              <Slider
                label="画笔大小"
                min={5}
                max={100}
                value={brushSize}
                onChange={(e) => setBrushSize(Number(e.target.value))}
                unit="px"
              />

              <div className="flex gap-1">
                <Button size="sm" variant="ghost" onClick={undoBrushStroke} className="flex-1 text-[10px]" icon={<Undo2 size={10} />}>
                  撤销
                </Button>
                <Button size="sm" variant="ghost" onClick={clearBrushStrokes} className="flex-1 text-[10px]">
                  清除画笔
                </Button>
              </div>

              {/* 参数滑块 */}
              <Slider
                label="羽化"
                min={0}
                max={30}
                value={feather}
                onChange={(e) => setFeather(Number(e.target.value))}
                unit="px"
              />

              <Slider
                label="膨胀/腐蚀"
                min={-20}
                max={20}
                value={dilate}
                onChange={(e) => setDilate(Number(e.target.value))}
                unit="px"
              />

              <Button
                size="sm"
                variant="secondary"
                onClick={handleRefine}
                loading={isProcessing}
                disabled={isProcessing}
                icon={<RefreshCw size={12} />}
                className="w-full"
              >
                应用精修
              </Button>
            </div>
          )}

          {/* ── 视图控制 ── */}
          {resultImage && (
            <div className="space-y-1.5">
              <Button size="sm" variant="ghost" onClick={toggleMask} className="w-full justify-start text-xs" icon={showMask ? <Eye size={12} /> : <EyeOff size={12} />}>
                {showMask ? '隐藏 Mask' : '显示 Mask'}
              </Button>
              <Button size="sm" variant="ghost" onClick={toggleCheckerboard} className="w-full justify-start text-xs" icon={showCheckerboard ? <Eye size={12} /> : <EyeOff size={12} />}>
                {showCheckerboard ? '隐藏棋盘格' : '显示棋盘格'}
              </Button>
            </div>
          )}

          {/* ── 换背景色 ── */}
          {resultImage && (
            <div className="border border-border-subtle rounded p-2.5 space-y-2">
              <h3 className="text-xs font-medium text-fg-primary flex items-center gap-1.5">
                <Palette size={12} /> 换背景色
              </h3>
              <div className="flex items-center gap-2">
                <input
                  type="color"
                  value={bgColor}
                  onChange={(e) => setBgColor(e.target.value)}
                  className="w-8 h-8 rounded border border-border-subtle cursor-pointer"
                />
                <span className="text-xs text-fg-secondary font-mono">{bgColor}</span>
                <Button size="sm" variant="secondary" onClick={handleComposite} className="ml-auto text-[10px]">
                  合成
                </Button>
              </div>
              {compositeResult && (
                <Button size="sm" variant="ghost" onClick={() => setCompositeResult(null)} className="w-full text-[10px]">
                  返回透明背景
                </Button>
              )}
            </div>
          )}

          {/* ── 操作按钮 ── */}
          <div className="mt-auto space-y-2">
            <Button
              variant="primary"
              onClick={handleCutout}
              loading={isProcessing}
              disabled={!sourceImage || isProcessing}
              icon={<Scissors size={14} />}
              className="w-full"
            >
              {getButtonText()}
            </Button>

            {resultImage && (
              <Button
                variant="secondary"
                onClick={handleSave}
                icon={<Download size={14} />}
                className="w-full"
              >
                保存图片
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════
// 工具函数：画笔笔划转 base64 mask
// ═══════════════════════════════════════════════════════════════════════════

/** 将画笔笔划绘制到 canvas 上，返回 base64 PNG mask */
function generateBrushMask(
  strokes: MaskStroke[],
  width: number,
  height: number,
): string | null {
  if (strokes.length === 0) return null

  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')!
  ctx.fillStyle = '#ffffff'
  ctx.strokeStyle = '#ffffff'

  for (const stroke of strokes) {
    ctx.lineWidth = stroke.size
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    ctx.beginPath()
    for (let i = 0; i < stroke.points.length; i += 2) {
      const x = stroke.points[i]
      const y = stroke.points[i + 1]
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    }
    ctx.stroke()
  }

  return canvas.toDataURL('image/png')
}
