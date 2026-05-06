import { useRef, useState, useEffect } from 'react'
import { ZoomIn, ZoomOut, RotateCcw } from 'lucide-react'

interface ImageCompareProps {
  beforeSrc: string
  afterSrc: string
  beforeLabel?: string
  afterLabel?: string
}

/**
 * Before/After 对比滑块组件
 * 支持：滑块对比 + 滚轮缩放 + Alt+拖拽平移
 * 滑块和图片在同一变换空间中，缩放后对比位置精确。
 */
export default function ImageCompare({
  beforeSrc,
  afterSrc,
  beforeLabel = '原图',
  afterLabel = '处理后',
}: ImageCompareProps) {
  const containerRef = useRef<HTMLDivElement>(null)

  // Compare slider position (0-100, in image-local percentage)
  const [position, setPosition] = useState(50)
  const [isDraggingSlider, setIsDraggingSlider] = useState(false)

  // Zoom & Pan
  const [scale, setScale] = useState(1)
  const [translate, setTranslate] = useState({ x: 0, y: 0 })
  const [isPanning, setIsPanning] = useState(false)
  const [panStart, setPanStart] = useState({ x: 0, y: 0 })

  /**
   * 将屏幕坐标转换为容器内变换后的"图片本地"百分比
   */
  const screenToLocalPercent = (clientX: number): number => {
    const container = containerRef.current
    if (!container) return 50
    const rect = container.getBoundingClientRect()
    // 屏幕坐标 → 容器内坐标
    const containerX = clientX - rect.left
    // 容器内坐标 → 变换前坐标（反向 transform）
    // transform: translate(tx, ty) scale(s), origin = center
    const cx = rect.width / 2
    const localX = (containerX - cx - translate.x) / scale + cx
    // 转为百分比
    return Math.max(0, Math.min(100, (localX / rect.width) * 100))
  }

  // Slider mouse down
  const handleSliderMouseDown = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDraggingSlider(true)
  }

  // Container mouse down
  const handleMouseDown = (e: React.MouseEvent) => {
    if (e.button === 1 || (e.button === 0 && e.altKey)) {
      // Middle button or Alt+Left: pan
      e.preventDefault()
      setIsPanning(true)
      setPanStart({ x: e.clientX - translate.x, y: e.clientY - translate.y })
    } else if (e.button === 0 && !e.altKey) {
      // Left click: move slider
      e.preventDefault()
      setIsDraggingSlider(true)
      setPosition(screenToLocalPercent(e.clientX))
    }
  }

  // Wheel zoom (anchor at mouse)
  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    const factor = e.deltaY < 0 ? 1.15 : 0.87
    const newScale = Math.max(0.5, Math.min(10, scale * factor))

    const container = containerRef.current
    if (!container) return
    const rect = container.getBoundingClientRect()
    const mx = e.clientX - rect.left - rect.width / 2
    const my = e.clientY - rect.top - rect.height / 2

    const ratio = newScale / scale
    setTranslate({
      x: mx - (mx - translate.x) * ratio,
      y: my - (my - translate.y) * ratio,
    })
    setScale(newScale)
  }

  // Global drag listeners
  useEffect(() => {
    if (!isDraggingSlider && !isPanning) return

    const handleMouseMove = (e: MouseEvent) => {
      if (isDraggingSlider) {
        setPosition(screenToLocalPercent(e.clientX))
      }
      if (isPanning) {
        setTranslate({
          x: e.clientX - panStart.x,
          y: e.clientY - panStart.y,
        })
      }
    }

    const handleMouseUp = () => {
      setIsDraggingSlider(false)
      setIsPanning(false)
    }

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [isDraggingSlider, isPanning, panStart, scale, translate])

  const handleResetZoom = (e: React.MouseEvent) => {
    e.stopPropagation()
    setScale(1)
    setTranslate({ x: 0, y: 0 })
  }

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full overflow-hidden rounded-md border border-border-subtle select-none"
      style={{ cursor: isPanning ? 'grabbing' : 'col-resize' }}
      onMouseDown={handleMouseDown}
      onWheel={handleWheel}
      onContextMenu={(e) => e.preventDefault()}
    >
      {/* Transformed layer: images + slider all in same coordinate space */}
      <div
        className="absolute inset-0"
        style={{
          transform: `translate(${translate.x}px, ${translate.y}px) scale(${scale})`,
          transformOrigin: 'center center',
        }}
      >
        {/* After image (full background) */}
        <img
          src={afterSrc}
          alt={afterLabel}
          className="absolute inset-0 w-full h-full object-contain pointer-events-none"
          draggable={false}
        />

        {/* Before image (clipped) */}
        <img
          src={beforeSrc}
          alt={beforeLabel}
          className="absolute inset-0 w-full h-full object-contain pointer-events-none"
          draggable={false}
          style={{ clipPath: `inset(0 ${100 - position}% 0 0)` }}
        />

        {/* Divider line (inside transform space, so it matches clip-path exactly) */}
        <div
          className="absolute top-0 bottom-0 w-[2px] bg-white/90 z-10"
          style={{ left: `${position}%`, transform: 'translateX(-50%)' }}
          onMouseDown={handleSliderMouseDown}
        >
          {/* Handle circle (scaled inversely to keep constant screen size) */}
          <div
            className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-white rounded-full shadow-lg flex items-center justify-center border border-black/10"
            style={{ width: `${36 / scale}px`, height: `${36 / scale}px` }}
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="#333"
              strokeWidth="2.5"
              style={{ width: `${16 / scale}px`, height: `${16 / scale}px` }}
            >
              <path d="M18 8L22 12L18 16" />
              <path d="M6 8L2 12L6 16" />
            </svg>
          </div>
        </div>
      </div>

      {/* UI overlays (screen space, not affected by zoom) */}
      {/* Labels */}
      <div className="absolute top-3 left-3 bg-black/70 px-2.5 py-1 rounded text-xs text-white pointer-events-none z-20">
        {beforeLabel}
      </div>
      <div className="absolute top-3 right-3 bg-black/70 px-2.5 py-1 rounded text-xs text-status-success pointer-events-none z-20">
        {afterLabel}
      </div>

      {/* Zoom controls */}
      <div className="absolute bottom-3 right-3 flex gap-1 z-20">
        <button
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); setScale(Math.min(10, scale * 1.3)) }}
          className="w-7 h-7 rounded bg-black/70 flex items-center justify-center hover:bg-black/90 transition-colors"
          title="放大"
        >
          <ZoomIn size={14} className="text-white" />
        </button>
        <button
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); setScale(Math.max(0.5, scale * 0.77)) }}
          className="w-7 h-7 rounded bg-black/70 flex items-center justify-center hover:bg-black/90 transition-colors"
          title="缩小"
        >
          <ZoomOut size={14} className="text-white" />
        </button>
        {scale !== 1 && (
          <button
            onMouseDown={(e) => e.stopPropagation()}
            onClick={handleResetZoom}
            className="w-7 h-7 rounded bg-black/70 flex items-center justify-center hover:bg-black/90 transition-colors"
            title="重置缩放"
          >
            <RotateCcw size={14} className="text-white" />
          </button>
        )}
        {scale !== 1 && (
          <div className="h-7 px-2 rounded bg-black/70 flex items-center pointer-events-none">
            <span className="text-white text-[11px]">{Math.round(scale * 100)}%</span>
          </div>
        )}
      </div>

      {/* Hint */}
      {scale > 1 && (
        <div className="absolute bottom-3 left-3 bg-black/70 px-2 py-1 rounded text-[11px] text-fg-secondary pointer-events-none z-20">
          Alt+拖拽 平移 | 滚轮 缩放
        </div>
      )}
    </div>
  )
}
