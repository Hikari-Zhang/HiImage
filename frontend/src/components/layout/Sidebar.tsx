import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Eraser, Maximize, Settings, ChevronLeft, ChevronRight, Layers, ScrollText, Sparkles, Scissors } from 'lucide-react'
import { clsx } from 'clsx'
import SidebarItem from './SidebarItem'
import { useBackendStore } from '../../stores/useBackendStore'

const navItems = [
  { path: '/watermark', label: '去水印', icon: Eraser },
  { path: '/cutout', label: '抠图', icon: Scissors },
  { path: '/upscale', label: '超分辨率', icon: Maximize },
  { path: '/synthesis', label: '智能合成', icon: Sparkles },
  { path: '/logs', label: '日志', icon: ScrollText },
  { path: '/settings', label: '设置', icon: Settings },
]

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false)
  const location = useLocation()
  const navigate = useNavigate()
  const isConnected = useBackendStore((state) => state.isConnected)

  return (
    <aside
      className={clsx(
        'app-sidebar flex flex-shrink-0 flex-col border-r border-border-subtle transition-all duration-200',
        collapsed ? 'w-[72px]' : 'w-[224px]'
      )}
    >
      {/* Traffic light spacer (macOS only) */}
      <div className="h-5 flex-shrink-0" />

      {/* Header */}
      <div className={clsx('flex items-center h-12 gap-3', collapsed ? 'justify-center' : 'px-5')}>
        <div className="w-8 h-8 rounded-xl bg-border-focus shadow-sm flex items-center justify-center flex-shrink-0">
          <Layers size={16} className="text-white" />
        </div>
        {!collapsed && (
          <span className="text-base font-semibold tracking-tight text-fg-primary truncate">
            HiImage
          </span>
        )}
      </div>

      {/* Navigation */}
      <nav aria-label="主导航" className="flex-1 flex flex-col gap-1 px-3 py-6">
        {!collapsed && <p className="px-3 pb-2 text-xs font-medium text-fg-secondary">图像工作台</p>}
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
          <div className={clsx('w-2 h-2 rounded-full flex-shrink-0', isConnected ? 'bg-status-success' : 'bg-status-warning')} />
          {!collapsed && <span className="text-xs text-fg-secondary">{isConnected ? '服务已连接' : '服务未连接'}</span>}
        </div>

        {/* Collapse toggle */}
        <button
          aria-label={collapsed ? '展开侧栏' : '收起侧栏'}
          aria-expanded={!collapsed}
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
