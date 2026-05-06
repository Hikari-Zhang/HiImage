import type { ReactNode } from 'react'

interface PageHeaderProps {
  title: string
  subtitle?: string
  right?: ReactNode
}

/**
 * 页面顶部标题栏
 * - macOS: 包含红绿灯安全区域，整个区域可拖拽
 * - Windows: 无安全区，右上角显示窗口控制按钮
 */
export default function PageHeader({ title, subtitle, right }: PageHeaderProps) {
  const isMac = typeof window !== 'undefined' && (window as any).electronAPI?.platform === 'darwin'

  return (
    <div className="bg-bg-secondary border-b border-border-subtle flex-shrink-0 drag-region select-none">
      {/* macOS 红绿灯安全区 */}
      {isMac && <div className="h-[28px]" />}
      {/* 内容行 */}
      <div className={`flex items-center px-3 ${isMac ? 'h-9' : 'h-[37px]'}`}>
        <span className="text-sm font-medium no-drag">{title}</span>
        {subtitle && <span className="text-xs text-fg-secondary no-drag ml-3">{subtitle}</span>}
        <div className="flex-1" />
        {/* 右侧自定义内容 */}
        {right && <div className="no-drag flex items-center gap-2 mr-2">{right}</div>}
        {/* Windows 窗口控制按钮 */}
        {!isMac && (
          <div className="no-drag flex items-center -my-2 -mr-3">
            {/* 最小化 */}
            <button
              onClick={() => (window as any).electronAPI?.windowMinimize?.()}
              className="w-12 h-[37px] flex items-center justify-center text-fg-secondary hover:bg-white/10 transition-colors"
              aria-label="最小化"
            >
              <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
                <rect x="1" y="5.25" width="10" height="1.5" rx="0.75" />
              </svg>
            </button>
            {/* 最大化/还原 */}
            <button
              onClick={() => (window as any).electronAPI?.windowMaximize?.()}
              className="w-12 h-[37px] flex items-center justify-center text-fg-secondary hover:bg-white/10 transition-colors"
              aria-label="最大化"
            >
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
                <rect x="1" y="1" width="10" height="10" rx="1" />
              </svg>
            </button>
            {/* 关闭 */}
            <button
              onClick={() => (window as any).electronAPI?.windowClose?.()}
              className="w-12 h-[37px] flex items-center justify-center text-fg-secondary hover:bg-[#c42b1c] hover:text-white transition-colors"
              aria-label="关闭"
            >
              <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
                <path d="M6 4.879L1.707.586.293 2l4.293 4.293L.293 10.586 1.707 12l4.293-4.293L10.293 12l1.414-1.414-4.293-4.293L11.707 2 10.293.586z" />
              </svg>
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
