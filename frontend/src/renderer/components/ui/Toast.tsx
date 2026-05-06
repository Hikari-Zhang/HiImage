import { useEffect, useState } from 'react'
import { clsx } from 'clsx'
import { CheckCircle2, XCircle, AlertTriangle, Info, X } from 'lucide-react'

export type ToastType = 'success' | 'error' | 'warning' | 'info'

interface ToastItem {
  id: string
  type: ToastType
  message: string
  duration?: number
}

// Global toast state
let toastListeners: Array<(toasts: ToastItem[]) => void> = []
let toasts: ToastItem[] = []

function notify(listeners: typeof toastListeners) {
  listeners.forEach((fn) => fn([...toasts]))
}

export function showToast(type: ToastType, message: string, duration = 4000) {
  const id = `toast_${Date.now()}_${Math.random().toString(36).slice(2)}`
  toasts = [...toasts, { id, type, message, duration }]
  notify(toastListeners)

  if (duration > 0) {
    setTimeout(() => {
      toasts = toasts.filter((t) => t.id !== id)
      notify(toastListeners)
    }, duration)
  }
}

export function dismissToast(id: string) {
  toasts = toasts.filter((t) => t.id !== id)
  notify(toastListeners)
}

const ICONS = {
  success: CheckCircle2,
  error: XCircle,
  warning: AlertTriangle,
  info: Info,
}

/**
 * 放在 App.tsx 中的全局 Toast 容器
 */
export default function ToastContainer() {
  const [items, setItems] = useState<ToastItem[]>([])

  useEffect(() => {
    toastListeners.push(setItems)
    return () => {
      toastListeners = toastListeners.filter((fn) => fn !== setItems)
    }
  }, [])

  if (items.length === 0) return null

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-[360px]">
      {items.map((toast) => {
        const Icon = ICONS[toast.type]
        return (
          <div
            key={toast.id}
            className={clsx(
              'flex items-start gap-2 px-3 py-2.5 rounded-lg border shadow-lg animate-in slide-in-from-right',
              'bg-bg-secondary',
              toast.type === 'success' && 'border-status-success/30',
              toast.type === 'error' && 'border-status-error/30',
              toast.type === 'warning' && 'border-status-warning/30',
              toast.type === 'info' && 'border-border-focus/30'
            )}
          >
            <Icon
              size={16}
              className={clsx(
                'flex-shrink-0 mt-0.5',
                toast.type === 'success' && 'text-status-success',
                toast.type === 'error' && 'text-status-error',
                toast.type === 'warning' && 'text-status-warning',
                toast.type === 'info' && 'text-border-focus'
              )}
            />
            <p className="text-xs text-fg-primary flex-1 leading-relaxed">{toast.message}</p>
            <button
              onClick={() => dismissToast(toast.id)}
              className="text-fg-secondary hover:text-fg-primary flex-shrink-0"
            >
              <X size={14} />
            </button>
          </div>
        )
      })}
    </div>
  )
}
