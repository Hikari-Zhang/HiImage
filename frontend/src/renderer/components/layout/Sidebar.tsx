import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Eraser, Maximize, Settings, ChevronLeft, ChevronRight, Layers, ScrollText, Sparkles } from 'lucide-react'
import { clsx } from 'clsx'
import SidebarItem from './SidebarItem'

const navItems = [
  { path: '/watermark', label: '去水印', icon: Eraser },
  { path: '/upscale', label: '超分辨率', icon: Maximize },
  { path: '/synthesis', label: '智能合成', icon: Sparkles },
  { path: '/logs', label: '日志', icon: ScrollText },
  { path: '/settings', label: '设置', icon: Settings },
]

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false)
  const location = useLocation()
  const navigate = useNavigate()

  return (
    <aside
      className={clsx(
        'flex flex-col bg-[#181818] border-r border-border-subtle transition-all duration-200',
        collapsed ? 'w-[56px]' : 'w-[200px]'
      )}
    >
      {/* Traffic light spacer (macOS) */}
      <div className="h-[52px] flex-shrink-0 drag-region" />

      {/* Header */}
      <div className="flex items-center h-[36px] px-3 gap-2">
        <div className="w-7 h-7 rounded-md bg-border-focus flex items-center justify-center flex-shrink-0">
          <Layers size={16} className="text-white" />
        </div>
        {!collapsed && (
          <span className="text-sm font-medium text-fg-primary truncate">
            ClearWaterMark
          </span>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 flex flex-col gap-1 px-2 py-3">
        {navItems.map((item) => (
          <SidebarItem
            key={item.path}
            icon={item.icon}
            label={item.label}
            active={location.pathname === item.path}
            collapsed={collapsed}
            onClick={() => navigate(item.path)}
          />
        ))}
      </nav>

      {/* Footer */}
      <div className="px-2 py-3 border-t border-border-subtle">
        {/* Backend status */}
        <div className={clsx('flex items-center gap-2 px-2 py-1', collapsed && 'justify-center')}>
          <div className="w-2 h-2 rounded-full bg-status-success flex-shrink-0" />
          {!collapsed && <span className="text-xs text-fg-secondary">Backend online</span>}
        </div>

        {/* Collapse toggle */}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="w-full mt-2 flex items-center justify-center py-1 rounded hover:bg-bg-hover transition-colors"
        >
          {collapsed ? (
            <ChevronRight size={16} className="text-fg-secondary" />
          ) : (
            <ChevronLeft size={16} className="text-fg-secondary" />
          )}
        </button>
      </div>
    </aside>
  )
}
