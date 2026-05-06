import { clsx } from 'clsx'
import type { InputHTMLAttributes } from 'react'

interface SliderProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'size'> {
  label?: string
  showValue?: boolean
  unit?: string
}

export default function Slider({ label, showValue = true, unit = '', value, className, ...props }: SliderProps) {
  return (
    <div>
      {label && (
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-xs text-fg-secondary">{label}</label>
          {showValue && (
            <span className="text-xs text-fg-primary font-medium">
              {value}{unit}
            </span>
          )}
        </div>
      )}
      <input
        type="range"
        value={value}
        className={clsx('w-full accent-border-focus h-1 cursor-pointer', className)}
        {...props}
      />
    </div>
  )
}
