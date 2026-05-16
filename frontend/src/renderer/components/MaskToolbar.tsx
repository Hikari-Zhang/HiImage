import { useCallback, useMemo } from 'react'
import { Square, PenTool, Eraser, Wand2, Undo2, Circle } from 'lucide-react'
import Slider from './ui/Slider'
import type { BrushSettings, MaskTool } from '../types/mask'

interface MaskToolbarProps {
  brushSettings: BrushSettings
  canvasTool: MaskTool
  canUndo: boolean
  onBrushSettingsChange: (settings: Partial<BrushSettings>) => void
  onCanvasToolChange: (tool: MaskTool) => void
  onUndo: () => void
}

/**
 * 画笔工具栏组件（React.memo 优化，避免无关重渲染）
 */
export default function MaskToolbar({
  brushSettings,
  canvasTool,
  canUndo,
  onBrushSettingsChange,
  onCanvasToolChange,
  onUndo,
}: MaskToolbarProps) {
  const isBrushMode = canvasTool === 'brush'

  // 工具按钮点击处理
  const handleToolClick = useCallback(
    (tool: 'rectangle' | 'brush') => {
      onCanvasToolChange(tool)
      if (tool === 'brush' && brushSettings.tool === 'eraser') {
        onBrushSettingsChange({ tool: 'brush' })
      }
    },
    [brushSettings.tool, onCanvasToolChange, onBrushSettingsChange]
  )

  // 画笔/橡皮擦切换
  const handleBrushEraserToggle = useCallback(
    (tool: 'brush' | 'eraser') => {
      onBrushSettingsChange({ tool })
    },
    [onBrushSettingsChange]
  )

  // 笔尖形状切换
  const handleShapeToggle = useCallback(() => {
    onBrushSettingsChange({
      shape: brushSettings.shape === 'circle' ? 'square' : 'circle',
    })
  }, [brushSettings.shape, onBrushSettingsChange])

  // 魔术棒点击
  const handleMagicWand = useCallback(() => {
    onBrushSettingsChange({ tool: 'magicWand' })
  }, [onBrushSettingsChange])

  // 工具栏按钮样式
  const buttonClass = useMemo(
    () =>
      'p-2 rounded-md transition-colors hover:bg-bg-hover text-fg-secondary hover:text-fg-primary',
    []
  )
  const activeClass = useMemo(
    () => 'bg-bg-active text-fg-accent',
    []
  )

  if (!isBrushMode) {
    // 非画笔模式：只显示切换到画笔模式的按钮
    return (
      <div className="flex items-center gap-1 bg-[rgb(30,30,30,0.95)] backdrop-blur-sm rounded-lg shadow-lg p-1.5">
        <button
          className={`${buttonClass} ${canvasTool === 'rectangle' ? activeClass : ''}`}
          onClick={() => handleToolClick('rectangle')}
          title="矩形选择 (M)"
        >
          <Square size={18} />
        </button>
        <button
          className={`${buttonClass} ${canvasTool === 'brush' ? activeClass : ''}`}
          onClick={() => handleToolClick('brush')}
          title="画笔绘制 (B)"
        >
          <PenTool size={18} />
        </button>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-1 bg-[rgb(30,30,30,0.95)] backdrop-blur-sm rounded-lg shadow-lg p-1.5 text-xs">
      {/* 工具切换 */}
      <button
        className={`${buttonClass} ${canvasTool === 'rectangle' ? activeClass : ''}`}
        onClick={() => handleToolClick('rectangle')}
        title="矩形选择 (M)"
      >
        <Square size={18} />
      </button>
      <button
        className={`${buttonClass} ${canvasTool === 'brush' && brushSettings.tool === 'brush' ? activeClass : ''}`}
        onClick={() => handleBrushEraserToggle('brush')}
        title="画笔 (B)"
      >
        <PenTool size={18} />
      </button>
      <button
        className={`${buttonClass} ${brushSettings.tool === 'eraser' ? activeClass : ''}`}
        onClick={() => handleBrushEraserToggle('eraser')}
        title="橡皮擦 (E)"
      >
        <Eraser size={18} />
      </button>

      <div className="w-px h-5 bg-border-subtle mx-0.5" />

      {/* 画笔大小滑块 */}
      <div className="flex items-center gap-1.5 min-w-[120px]">
        <span className="text-fg-secondary whitespace-nowrap">大小</span>
        <Slider
          min={1}
          max={100}
          value={brushSettings.size}
          onChange={(e) => onBrushSettingsChange({ size: Number(e.target.value) })}
          className="w-full"
        />
        <span className="text-fg-primary font-medium w-8 text-right tabular-nums">
          {brushSettings.size}
        </span>
      </div>

      {/* 硬度滑块 */}
      <div className="flex items-center gap-1.5 min-w-[120px]">
        <span className="text-fg-secondary whitespace-nowrap">硬度</span>
        <Slider
          min={0}
          max={100}
          value={brushSettings.hardness}
          onChange={(e) => onBrushSettingsChange({ hardness: Number(e.target.value) })}
          className="w-full"
        />
        <span className="text-fg-primary font-medium w-8 text-right tabular-nums">
          {brushSettings.hardness}
        </span>
      </div>

      <div className="w-px h-5 bg-border-subtle mx-0.5" />

      {/* 笔尖形状切换 */}
      <button
        className={buttonClass}
        onClick={handleShapeToggle}
        title={`笔尖形状: ${brushSettings.shape === 'circle' ? '圆形' : '方形'} (Shift+C)`}
      >
        {brushSettings.shape === 'circle' ? <Circle size={18} /> : <Square size={18} />}
      </button>

      {/* 魔术棒 */}
      <button
        className={`${buttonClass} ${brushSettings.tool === 'magicWand' ? activeClass : ''}`}
        onClick={handleMagicWand}
        title="魔术棒 (W)"
      >
        <Wand2 size={18} />
      </button>

      <div className="w-px h-5 bg-border-subtle mx-0.5" />

      {/* 撤销 */}
      <button
        className={`${buttonClass} ${!canUndo ? 'opacity-50 cursor-not-allowed' : ''}`}
        onClick={onUndo}
        disabled={!canUndo}
        title="撤销 (Ctrl+Z)"
      >
        <Undo2 size={18} />
      </button>
    </div>
  )
}
