import { clsx } from 'clsx'
import type { LucideIcon } from 'lucide-react'

interface SidebarItemProps {
  icon: LucideIcon
  label: string
  active: boolean
  collapsed: boolean
  onClick: () => void
}

export default function SidebarItem({ icon: Icon, label, active, collapsed, onClick }: SidebarItemProps) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      aria-current={active ? 'page' : undefined}
      title={collapsed ? label : undefined}
      className={clsx(
        'flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors text-sm',
        active
          ? 'bg-bg-active text-fg-accent font-medium'
          : 'text-fg-secondary hover:bg-bg-hover hover:text-fg-primary',
        collapsed && 'justify-center px-0'
      )}
    >
      <Icon size={18} className="flex-shrink-0" />
      {!collapsed && <span className="truncate">{label}</span>}
    </button>
  )
}
