import type { ReactNode } from 'react'

interface PageHeaderProps {
  title: string
  subtitle?: string
  right?: ReactNode
}

export default function PageHeader({ title, subtitle, right }: PageHeaderProps) {
  return (
    <header className="page-header">
      <div className="flex items-baseline gap-3 min-w-0">
        <h1 className="text-xl font-semibold tracking-tight text-fg-primary">{title}</h1>
        {subtitle && <span className="text-xs text-fg-secondary">{subtitle}</span>}
      </div>
      {right && <div className="page-header-actions flex items-center gap-2 min-w-0">{right}</div>}
    </header>
  )
}
