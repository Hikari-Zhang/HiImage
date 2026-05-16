import { useRef, useState, useEffect, useCallback, useMemo } from 'react'
import { Stage, Layer, Image as KonvaImage, Line, Rect } from 'react-konva'
import Konva from 'konva'
import type { MaskStroke, BrushSettings, MaskTool } from '../types/mask'
import { POINT_SAMPLE_RATIO } from '../types/mask'

interface ROI {
  id: string
  x1: number
  y1: number
  x2: number
  y2: number
}

interface MaskCanvasProps {
  imageSrc: string | null
  strokes: MaskStroke[]
  brushSettings: BrushSettings
  canvasTool: MaskTool
  // ROI 相关 props
  rois?: ROI[]
  selectedROIs?: string[]
  onROISelect?: (id: string) => void
  onROIDrawn?: (roi: Omit<ROI, 'id'>) => void
  onROISelect?: (id: string) => void
  // 回调
  onStrokeComplete: (stroke: MaskStroke) => void
  onUndo: () => void
}

/**
 * 低 CPU 占用的遮罩绘制画布组件
 * 使用 Konva.js 实现分层渲染、点采样优化、requestAnimationFrame 节流
 * 同时支持显示 ROI 矩形和画笔遮罩
 */
export default function MaskCanvas({
  imageSrc,
  strokes,
  brushSettings,
  canvasTool,
  rois = [],
  selectedROIs = [],
  onROISelect,
  onROIDrawn,
  onStrokeComplete,
  onUndo,
}: MaskCanvasProps) {
  const stageRef = useRef<Konva.Stage>(null)
  const imageLayerRef = useRef<Konva.Layer>(null)
  const maskLayerRef = useRef<Konva.Layer>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [imageObj, setImageObj] = useState<HTMLImageElement | null>(null)
  const imageObjRef = useRef<HTMLImageElement | null>(null)

  // 视图状态
  const [scale, setScale] = useState(1)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const [containerSize, setContainerSize] = useState({ w: 0, h: 0 })
  const containerSizeRef = useRef({ w: 0, h: 0 })

  // 交互状态
  const [isPanning, setIsPanning] = useState(false)
  const [isDrawing, setIsDrawing] = useState(false)  // 画笔绘制
  const [currentPoints, setCurrentPoints] = useState<number[]>([])
  const [spaceDown, setSpaceDown] = useState(false)
  const [cursorPos, setCursorPos] = useState<{ x: number; y: number } | null>(null)
  const panStartRef = useRef({ x: 0, y: 0 })

  // ROI 绘制状态
  const [isDrawingROI, setIsDrawingROI] = useState(false)
  const [roiStartPos, setRoiStartPos] = useState<{ x: number; y: number } | null>(null)
  const [currentROIRect, setCurrentROIRect] = useState<{ x: number; y: number; width: number; height: number } | null>(null)

  // 绘制优化：rAF 节流
  const rafRef = useRef<number | null>(null)
  const pendingPointRef = useRef<{ x: number; y: number } | null>(null)

  // 获取当前工具模式
  const isBrushMode = canvasTool === 'brush'
  const isDrawingTool = brushSettings.tool === 'brush' || brushSettings.tool === 'eraser'

  // 加载图片
  useEffect(() => {
    if (!imageSrc) {
      imageObjRef.current = null
      setImageObj(null)
      return
    }

    const img = new window.Image()
    img.onload = () => {
      imageObjRef.current = img
      setImageObj(img)  // 触发重新渲染
      fitToView(img)
    }
    img.src = imageSrc
  }, [imageSrc])

  // 适配视图
  const fitToView = useCallback((img: HTMLImageElement) => {
    const cs = containerSizeRef.current
    if (cs.w === 0 || cs.h === 0) return
    const scaleX = cs.w / img.naturalWidth
    const scaleY = cs.h / img.naturalHeight
    const newScale = Math.min(scaleX, scaleY) * 0.9
    setScale(newScale)
    setOffset({
      x: (cs.w - img.naturalWidth * newScale) / 2,
      y: (cs.h - img.naturalHeight * newScale) / 2,
    })
  }, [])

  // 监听容器大小变化（直接监听根元素，不依赖 Stage）
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

  // 容器大小变化时重新适配
  useEffect(() => {
    if (imageObjRef.current && containerSize.w > 0) {
      fitToView(imageObjRef.current)
    }
  }, [containerSize, fitToView])

  // 空格键监听（临时平移模式）
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.code === 'Space' && !e.repeat) {
        e.preventDefault()
        setSpaceDown(true)
      }
      // Ctrl+Z 撤销
      if ((e.ctrlKey || e.metaKey) && e.code === 'KeyZ') {
        e.preventDefault()
        onUndo()
      }
    }
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code === 'Space') {
        setSpaceDown(false)
        setIsPanning(false)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('keyup', onKeyUp)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('keyup', onKeyUp)
    }
  }, [onUndo])

  // 点采样：判断是否应该添加新点
  const shouldAddPoint = useCallback(
    (x: number, y: number, points: number[]): boolean => {
      if (points.length < 2) return true
      const lastX = points[points.length - 2]
      const lastY = points[points.length - 1]
      const dist = Math.sqrt((x - lastX) ** 2 + (y - lastY) ** 2)
      return dist > brushSettings.size / POINT_SAMPLE_RATIO
    },
    [brushSettings.size]
  )

  // 获取舞台坐标
  const getStagePoint = useCallback(
    (e: Konva.KonvaEventObject<PointerEvent>): { x: number; y: number } | null => {
      const stage = stageRef.current
      if (!stage) return null
      const pointerPos = stage.getPointerPosition()
      if (!pointerPos) return null

      // 转换为图像坐标
      const x = (pointerPos.x - offset.x) / scale
      const y = (pointerPos.y - offset.y) / scale

      // 限制在图像范围内
      const img = imageObjRef.current
      if (!img) return null
      if (x < 0 || y < 0 || x > img.naturalWidth || y > img.naturalHeight) {
        return null
      }

      return { x, y }
    },
    [offset, scale]
  )

  // 处理绘制移动（rAF 节流）
  const processDrawMove = useCallback(
    (point: { x: number; y: number }) => {
      if (!isDrawing) return

      setCurrentPoints((prev) => {
        if (shouldAddPoint(point.x, point.y, prev)) {
          return [...prev, point.x, point.y]
        }
        return prev
      })
    },
    [isDrawing, shouldAddPoint]
  )

  // 鼠标/触摸事件处理
  const handleMouseDown = useCallback(
    (e: Konva.KonvaEventObject<PointerEvent>) => {
      const stage = stageRef.current
      if (!stage) return

      const pos = stage.getPointerPosition()
      if (!pos) return

      // 检查是否应该平移
      if (spaceDown || e.evt.button === 1) {
        setIsPanning(true)
        panStartRef.current = { x: pos.x - offset.x, y: pos.y - offset.y }
        return
      }

      // ROI 矩形绘制模式
      if (canvasTool === 'rectangle') {
        const imgPoint = getStagePoint(e)
        if (!imgPoint) return

        setIsDrawingROI(true)
        setRoiStartPos(imgPoint)
        setCurrentROIRect({
          x: imgPoint.x,
          y: imgPoint.y,
          width: 0,
          height: 0,
        })
        return
      }

      // 画笔/橡皮擦模式：开始绘制
      if (isBrushMode && isDrawingTool) {
        const imgPoint = getStagePoint(e)
        if (!imgPoint) return

        setIsDrawing(true)
        setCurrentPoints([imgPoint.x, imgPoint.y])
      }
    },
    [spaceDown, canvasTool, isBrushMode, isDrawingTool, offset, getStagePoint]
  )

  const handleMouseMove = useCallback(
    (e: Konva.KonvaEventObject<PointerEvent>) => {
      const stage = stageRef.current
      if (!stage) return
      const pos = stage.getPointerPosition()
      if (!pos) return

      // 更新光标位置（画笔模式）
      if (isBrushMode && isDrawingTool) {
        setCursorPos({ x: pos.x, y: pos.y })
      } else {
        setCursorPos(null)
      }

      if (isPanning) {
        const newX = pos.x - panStartRef.current.x
        const newY = pos.y - panStartRef.current.y

        // Clamp 逻辑
        const img = imageObjRef.current
        const cs = containerSizeRef.current
        let clampedX = newX
        let clampedY = newY

        if (img) {
          const imgW = img.naturalWidth * scale
          const imgH = img.naturalHeight * scale
          if (imgW > cs.w) {
            clampedX = Math.min(0, Math.max(cs.w - imgW, newX))
          } else {
            clampedX = (cs.w - imgW) / 2
          }
          if (imgH > cs.h) {
            clampedY = Math.min(0, Math.max(cs.h - imgH, newY))
          } else {
            clampedY = (cs.h - imgH) / 2
          }
        }

        setOffset({ x: clampedX, y: clampedY })
      } else if (isDrawingROI && roiStartPos) {
        // ROI 绘制模式：更新当前矩形
        const imgPoint = getStagePoint(e)
        if (!imgPoint) return

        const x = Math.min(roiStartPos.x, imgPoint.x)
        const y = Math.min(roiStartPos.y, imgPoint.y)
        const width = Math.abs(imgPoint.x - roiStartPos.x)
        const height = Math.abs(imgPoint.y - roiStartPos.y)

        setCurrentROIRect({ x, y, width, height })
      } else if (isDrawing) {
        // rAF 节流
        pendingPointRef.current = getStagePoint(e)
        if (rafRef.current === null) {
          rafRef.current = requestAnimationFrame(() => {
            const point = pendingPointRef.current
            if (point) processDrawMove(point)
            rafRef.current = null
          })
        }
      }
    },
    [isPanning, isDrawingROI, isDrawing, roiStartPos, scale, getStagePoint, processDrawMove, isBrushMode, isDrawingTool]
  )

  const handleMouseUp = useCallback(() => {
    // ROI 绘制完成
    if (isDrawingROI && currentROIRect) {
      setIsDrawingROI(false)
      setRoiStartPos(null)

      // 确认 ROI 大小有效
      if (currentROIRect.width > 5 && currentROIRect.height > 5 && onROIDrawn) {
        onROIDrawn({
          x1: Math.round(currentROIRect.x),
          y1: Math.round(currentROIRect.y),
          x2: Math.round(currentROIRect.x + currentROIRect.width),
          y2: Math.round(currentROIRect.y + currentROIRect.height),
        })
      }
      setCurrentROIRect(null)
    }

    // 画笔绘制完成
    if (isDrawing) {
      setIsDrawing(false)
      if (currentPoints.length >= 4) {
        // 完成一笔
        const stroke: MaskStroke = {
          id: `stroke_${Date.now()}`,
          points: [...currentPoints],
          size: brushSettings.size,
          hardness: brushSettings.hardness,
          shape: brushSettings.shape,
          tool: brushSettings.tool === 'eraser' ? 'eraser' : 'brush',
          timestamp: Date.now(),
        }
        onStrokeComplete(stroke)
      }
      setCurrentPoints([])
    }
    setIsPanning(false)
  }, [isDrawingROI, currentROIRect, isDrawing, currentPoints, brushSettings, onROIDrawn, onStrokeComplete])

  // 滚轮缩放
  const handleWheel = useCallback(
    (e: Konva.KonvaEventObject<WheelEvent>) => {
      e.evt.preventDefault()
      if (!imageObjRef.current) return

      const stage = stageRef.current
      if (!stage) return
      const pos = stage.getPointerPosition()
      if (!pos) return

      const factor = e.evt.deltaY < 0 ? 1.15 : 0.87
      const newScale = Math.max(0.05, Math.min(20, scale * factor))

      const img = imageObjRef.current
      const cs = containerSizeRef.current
      const imgW = img.naturalWidth * newScale
      const imgH = img.naturalHeight * newScale

      let newOffsetX: number
      let newOffsetY: number

      if (imgW <= cs.w && imgH <= cs.h) {
        newOffsetX = (cs.w - imgW) / 2
        newOffsetY = (cs.h - imgH) / 2
      } else {
        const dx = pos.x - offset.x
        const dy = pos.y - offset.y
        newOffsetX = pos.x - dx * (newScale / scale)
        newOffsetY = pos.y - dy * (newScale / scale)

        if (imgW > cs.w) {
          newOffsetX = Math.min(0, Math.max(cs.w - imgW, newOffsetX))
        } else {
          newOffsetX = (cs.w - imgW) / 2
        }
        if (imgH > cs.h) {
          newOffsetY = Math.min(0, Math.max(cs.h - imgH, newOffsetY))
        } else {
          newOffsetY = (cs.h - imgH) / 2
        }
      }

      setScale(newScale)
      setOffset({ x: newOffsetX, y: newOffsetY })
    },
    [scale, offset]
  )

  // 渲染笔划
  const renderStrokes = useMemo(() => {
    return strokes.map((stroke) => {
      const isEraser = stroke.tool === 'eraser'
      return (
        <Line
          key={stroke.id}
          points={stroke.points.map((p, i) => {
            // 转换为舞台坐标
            return i % 2 === 0
              ? p * scale + offset.x
              : p * scale + offset.y
          })}
          stroke={isEraser ? '#f44336' : '#4caf50'}
          strokeWidth={stroke.size * scale}
          lineCap="round"
          lineJoin="round"
          opacity={0.6}
          globalCompositeOperation={isEraser ? 'destination-out' : 'source-over'}
        />
      )
    })
  }, [strokes, scale, offset])

  // 渲染当前绘制中的笔划
  const renderCurrentStroke = useMemo(() => {
    if (!isDrawing || currentPoints.length < 2) return null

    return (
      <Line
        points={currentPoints.map((p, i) =>
          i % 2 === 0 ? p * scale + offset.x : p * scale + offset.y
        )}
        stroke={brushSettings.tool === 'eraser' ? '#f44336' : '#4caf50'}
        strokeWidth={brushSettings.size * scale}
        lineCap="round"
        lineJoin="round"
        opacity={0.6}
      />
    )
  }, [isDrawing, currentPoints, brushSettings.tool, brushSettings.size, scale, offset])

  // 光标样式
  const cursor = isPanning
    ? 'grabbing'
    : spaceDown
    ? 'grab'
    : isBrushMode && isDrawingTool
    ? 'crosshair'
    : 'default'

  // 清理 rAF
  useEffect(() => {
    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current)
      }
    }
  }, [])

  return (
    <div ref={containerRef} className="w-full h-full relative overflow-hidden bg-[#1a1a1a]">
      {containerSize.w > 0 && (
        <Stage
          ref={stageRef}
          width={containerSize.w}
          height={containerSize.h}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          onWheel={handleWheel}
          style={{ cursor }}
          className="absolute inset-0"
        >
          {/* 图像层：静态，仅图片变化时重绘 */}
          <Layer ref={imageLayerRef} imageSmoothingEnabled={true}>
            {imageObj && (
              <KonvaImage
                image={imageObj}
                x={offset.x}
                y={offset.y}
                width={imageObj.naturalWidth * scale}
                height={imageObj.naturalHeight * scale}
              />
            )}
          </Layer>

          {/* ROI 层：显示 ROI 矩形 */}
          <Layer>
            {/* 已存在的 ROI */}
            {rois.map((roi) => {
              const isSelected = selectedROIs.includes(roi.id)
              return (
                <Rect
                  key={roi.id}
                  x={roi.x1 * scale + offset.x}
                  y={roi.y1 * scale + offset.y}
                  width={(roi.x2 - roi.x1) * scale}
                  height={(roi.y2 - roi.y1) * scale}
                  stroke={isSelected ? '#00a8ff' : '#ff9800'}
                  strokeWidth={2}
                  fill={isSelected ? 'rgba(0, 168, 255, 0.2)' : 'rgba(255, 152, 0, 0.2)'}
                  onClick={() => onROISelect?.(roi.id)}
                  onTap={() => onROISelect?.(roi.id)}
                  listening={canvasTool === 'rectangle'}
                />
              )
            })}

            {/* 当前正在绘制的 ROI */}
            {currentROIRect && (
              <Rect
                x={currentROIRect.x * scale + offset.x}
                y={currentROIRect.y * scale + offset.y}
                width={currentROIRect.width * scale}
                height={currentROIRect.height * scale}
                stroke="#ff9800"
                strokeWidth={2}
                fill="rgba(255, 152, 0, 0.2)"
                listening={false}
              />
            )}
          </Layer>

          {/* 遮罩层：动态，仅笔划变化时重绘 */}
          <Layer ref={maskLayerRef}>
            {renderStrokes}
            {renderCurrentStroke}
          </Layer>
        </Stage>
      )}

      {/* 画笔光标预览 */}
      {isBrushMode && isDrawingTool && cursorPos && (
        <BrushCursor
          x={cursorPos.x}
          y={cursorPos.y}
          size={brushSettings.size * scale}
          shape={brushSettings.shape}
          isEraser={brushSettings.tool === 'eraser'}
        />
      )}
    </div>
  )
}

/** 画笔光标预览组件（CSS 实现，零 CPU 开销） */
function BrushCursor({
  x,
  y,
  size,
  shape,
  isEraser,
}: {
  x: number
  y: number
  size: number
  shape: 'circle' | 'square'
  isEraser: boolean
}) {
  if (size < 2) return null

  return (
    <div
      className="pointer-events-none absolute z-50"
      style={{
        left: x,
        top: y,
        width: size,
        height: size,
        borderRadius: shape === 'circle' ? '50%' : '2px',
        border: `2px solid ${isEraser ? '#f44336' : '#4caf50'}`,
        opacity: 0.6,
        transform: 'translate(-50%, -50%)',
        transition: 'width 0.1s, height 0.1s',
      }}
    />
  )
}
