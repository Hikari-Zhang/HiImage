import { clsx } from 'clsx'

interface ProgressProps {
  value: number // 0-100, -1 for indeterminate
  label?: string
  variant?: 'default' | 'success' | 'error'
  size?: 'sm' | 'md'
}

export default function Progress({ value, label, variant = 'default', size = 'sm' }: ProgressProps) {
  const isIndeterminate = value < 0

  return (
    <div>
      <div
        className={clsx(
          'bg-bg-primary rounded-full overflow-hidden',
          size === 'sm' && 'h-1',
          size === 'md' && 'h-2'
        )}
      >
        <div
          className={clsx(
            'h-full rounded-full transition-all duration-300',
            isIndeterminate && 'animate-pulse w-full',
            variant === 'default' && 'bg-border-focus',
            variant === 'success' && 'bg-status-success',
            variant === 'error' && 'bg-status-error'
          )}
          style={!isIndeterminate ? { width: `${Math.min(100, Math.max(0, value))}%` } : undefined}
        />
      </div>
      {label && (
        <p
          className={clsx(
            'text-xs mt-1',
            variant === 'default' && 'text-fg-secondary',
            variant === 'success' && 'text-status-success',
            variant === 'error' && 'text-status-error'
          )}
        >
          {label}
        </p>
      )}
    </div>
  )
}
