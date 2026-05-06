import { useRef, useState, useEffect, useCallback } from 'react'
import { clsx } from 'clsx'
import type { ROI } from '../stores/useImageStore'

interface ImageCanvasProps {
  imageSrc: string | null
  rois: ROI[]
  selectedROIs: string[]
  tool: 'draw' | 'pan'
  onROIDrawn: (roi: Omit<ROI, 'id'>) => void
  onROISelect: (id: string) => void
  onImageLoad: (width: number, height: number) => void
}

export default function ImageCanvas({
  imageSrc,
  rois,
  selectedROIs,
  tool,
  onROIDrawn,
  onROISelect,
  onImageLoad,
}: ImageCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const imageRef = useRef<HTMLImageElement | null>(null)

  // View state
  const [scale, setScale] = useState(1)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const [containerSize, setContainerSize] = useState({ w: 0, h: 0 })
  const containerSizeRef = useRef({ w: 0, h: 0 })

  // Interaction state
  const [isPanning, setIsPanning] = useState(false)
  const [isDrawing, setIsDrawing] = useState(false)
  const [drawStart, setDrawStart] = useState({ x: 0, y: 0 })
  const [drawCurrent, setDrawCurrent] = useState({ x: 0, y: 0 })
  const [panStart, setPanStart] = useState({ x: 0, y: 0 })

  // Observe container size
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

  // Fit image to container (uses ref to avoid stale closure)
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

  // Load image
  useEffect(() => {
    if (!imageSrc) {
      imageRef.current = null
      return
    }

    const img = new Image()
    img.onload = () => {
      imageRef.current = img
      onImageLoad(img.naturalWidth, img.naturalHeight)
      fitToView(img)
    }
    img.src = imageSrc
  }, [imageSrc, fitToView])

  // Re-fit when container size changes
  useEffect(() => {
    if (imageRef.current && containerSize.w > 0) {
      fitToView(imageRef.current)
    }
  }, [containerSize, fitToView])

  // Render canvas
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    canvas.width = containerSize.w * window.devicePixelRatio
    canvas.height = containerSize.h * window.devicePixelRatio
    canvas.style.width = `${containerSize.w}px`
    canvas.style.height = `${containerSize.h}px`
    ctx.scale(window.devicePixelRatio, window.devicePixelRatio)

    // Clear
    ctx.fillStyle = '#1a1a1a'
    ctx.fillRect(0, 0, containerSize.w, containerSize.h)

    // Draw image
    if (imageRef.current) {
      const img = imageRef.current
      ctx.drawImage(
        img,
        offset.x,
        offset.y,
        img.naturalWidth * scale,
        img.naturalHeight * scale
      )
    }

    // Draw existing ROIs
    rois.forEach((roi) => {
      const x = roi.x1 * scale + offset.x
      const y = roi.y1 * scale + offset.y
      const w = (roi.x2 - roi.x1) * scale
      const h = (roi.y2 - roi.y1) * scale

      const isSelected = selectedROIs.includes(roi.id)

      // Fill
      ctx.fillStyle = isSelected ? 'rgba(0, 122, 204, 0.15)' : 'rgba(0, 122, 204, 0.08)'
      ctx.fillRect(x, y, w, h)

      // Border
      ctx.strokeStyle = isSelected ? '#007acc' : '#569cd6'
      ctx.lineWidth = isSelected ? 2 : 1.5
      ctx.setLineDash(isSelected ? [] : [4, 3])
      ctx.strokeRect(x, y, w, h)
      ctx.setLineDash([])
    })

    // Draw current drawing rect
    if (isDrawing) {
      const x = Math.min(drawStart.x, drawCurrent.x)
      const y = Math.min(drawStart.y, drawCurrent.y)
      const w = Math.abs(drawCurrent.x - drawStart.x)
      const h = Math.abs(drawCurrent.y - drawStart.y)

      ctx.fillStyle = 'rgba(76, 175, 80, 0.1)'
      ctx.fillRect(x, y, w, h)
      ctx.strokeStyle = '#4caf50'
      ctx.lineWidth = 2
      ctx.setLineDash([6, 3])
      ctx.strokeRect(x, y, w, h)
      ctx.setLineDash([])
    }
  }, [containerSize, scale, offset, rois, selectedROIs, isDrawing, drawStart, drawCurrent])

  // Convert screen coords to image coords
  const screenToImage = (sx: number, sy: number) => ({
    x: (sx - offset.x) / scale,
    y: (sy - offset.y) / scale,
  })

  // Mouse handlers
  const handleMouseDown = (e: React.MouseEvent) => {
    const rect = canvasRef.current?.getBoundingClientRect()
    if (!rect) return
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top

    if (tool === 'pan' || e.button === 1) {
      setIsPanning(true)
      setPanStart({ x: mx - offset.x, y: my - offset.y })
    } else if (tool === 'draw' && imageRef.current) {
      // Check if clicking on an existing ROI
      const imgCoords = screenToImage(mx, my)
      const clickedROI = rois.find(
        (r) => imgCoords.x >= r.x1 && imgCoords.x <= r.x2 && imgCoords.y >= r.y1 && imgCoords.y <= r.y2
      )
      if (clickedROI) {
        onROISelect(clickedROI.id)
        return
      }

      setIsDrawing(true)
      setDrawStart({ x: mx, y: my })
      setDrawCurrent({ x: mx, y: my })
    }
  }

  const handleMouseMove = (e: React.MouseEvent) => {
    const rect = canvasRef.current?.getBoundingClientRect()
    if (!rect) return
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top

    if (isPanning) {
      setOffset({ x: mx - panStart.x, y: my - panStart.y })
    } else if (isDrawing) {
      setDrawCurrent({ x: mx, y: my })
    }
  }

  const handleMouseUp = () => {
    if (isDrawing) {
      setIsDrawing(false)
      // Minimum size check (at least 10px on screen)
      const w = Math.abs(drawCurrent.x - drawStart.x)
      const h = Math.abs(drawCurrent.y - drawStart.y)
      if (w > 10 && h > 10) {
        const p1 = screenToImage(
          Math.min(drawStart.x, drawCurrent.x),
          Math.min(drawStart.y, drawCurrent.y)
        )
        const p2 = screenToImage(
          Math.max(drawStart.x, drawCurrent.x),
          Math.max(drawStart.y, drawCurrent.y)
        )
        // Clamp to image bounds
        if (imageRef.current) {
          const iw = imageRef.current.naturalWidth
          const ih = imageRef.current.naturalHeight
          onROIDrawn({
            x1: Math.max(0, Math.round(p1.x)),
            y1: Math.max(0, Math.round(p1.y)),
            x2: Math.min(iw, Math.round(p2.x)),
            y2: Math.min(ih, Math.round(p2.y)),
          })
        }
      }
    }
    setIsPanning(false)
  }

  // Wheel zoom
  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    if (!imageRef.current) return

    const rect = canvasRef.current?.getBoundingClientRect()
    if (!rect) return
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top

    const factor = e.deltaY < 0 ? 1.15 : 0.87
    const newScale = Math.max(0.05, Math.min(20, scale * factor))

    const img = imageRef.current
    const cs = containerSizeRef.current
    const imgW = img.naturalWidth * newScale
    const imgH = img.naturalHeight * newScale

    // 图片能完全放入容器时居中，否则保持以鼠标为中心缩放
    if (imgW <= cs.w && imgH <= cs.h) {
      setOffset({
        x: (cs.w - imgW) / 2,
        y: (cs.h - imgH) / 2,
      })
    } else {
      // Zoom towards mouse position
      const dx = mx - offset.x
      const dy = my - offset.y
      setOffset({
        x: mx - dx * (newScale / scale),
        y: my - dy * (newScale / scale),
      })
    }
    setScale(newScale)
  }

  return (
    <div
      ref={containerRef}
      className="w-full h-full relative overflow-hidden"
      style={{ cursor: tool === 'pan' ? 'grab' : 'crosshair' }}
    >
      <canvas
        ref={canvasRef}
        className="absolute inset-0"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onWheel={handleWheel}
      />
    </div>
  )
}
