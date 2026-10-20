import { clsx } from 'clsx'
import type { ButtonHTMLAttributes, ReactNode } from 'react'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost'
  size?: 'sm' | 'md' | 'lg'
  icon?: ReactNode
  loading?: boolean
}

export default function Button({
  variant = 'primary',
  size = 'md',
  icon,
  loading,
  children,
  className,
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      disabled={disabled || loading}
      className={clsx(
        'inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        // Variants
        variant === 'primary' && 'bg-border-focus text-white hover:bg-blue-600',
        variant === 'secondary' && 'bg-bg-hover text-fg-primary border border-border-subtle hover:bg-[#4a4a4a]',
        variant === 'danger' && 'bg-status-error text-white hover:bg-red-600',
        variant === 'ghost' && 'bg-transparent text-fg-secondary hover:text-fg-primary hover:bg-bg-hover',
        // Sizes
        size === 'sm' && 'text-xs px-2.5 py-1.5',
        size === 'md' && 'text-sm px-4 py-2',
        size === 'lg' && 'text-sm px-5 py-2.5',
        className
      )}
      {...props}
    >
      {loading ? (
        <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      ) : icon ? (
        <span className="flex-shrink-0">{icon}</span>
      ) : null}
      {children}
    </button>
  )
}
