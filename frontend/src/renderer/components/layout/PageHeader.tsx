import type { ReactNode } from 'react'

interface PageHeaderProps {
  title: string
  subtitle?: string
  right?: ReactNode
}

/**
 * 页面顶部标题栏 - 包含 macOS titlebar 安全区域
 * 整个区域支持拖动窗口
 */
export default function PageHeader({ title, subtitle, right }: PageHeaderProps) {
  return (
    <div className="bg-bg-secondary border-b border-border-subtle flex-shrink-0 drag-region">
      {/* macOS traffic light safe area */}
      <div className="h-[28px]" />
      {/* Content row */}
      <div className="h-9 flex items-center px-3 gap-3">
        <span className="text-sm font-medium no-drag">{title}</span>
        {subtitle && <span className="text-xs text-fg-secondary no-drag">{subtitle}</span>}
        <div className="flex-1" />
        {right && <div className="no-drag flex items-center gap-2">{right}</div>}
      </div>
    </div>
  )
}
