import { clsx } from 'clsx'
import type { SelectHTMLAttributes } from 'react'

interface SelectOption {
  value: string
  label: string
  disabled?: boolean
  description?: string
}

interface SelectGroup {
  label: string
  options: SelectOption[]
}

interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'size'> {
  label?: string
  options?: SelectOption[]
  groups?: SelectGroup[]
  size?: 'sm' | 'md'
}

export default function Select({ label, options, groups, size = 'md', className, ...props }: SelectProps) {
  return (
    <div>
      {label && <label className="text-xs text-fg-secondary mb-1.5 block">{label}</label>}
      <select
        className={clsx(
          'w-full bg-bg-primary border border-border-subtle text-fg-primary rounded',
          'focus:border-border-focus focus:outline-none transition-colors',
          'appearance-none bg-no-repeat bg-[length:16px] bg-[right_8px_center]',
          size === 'sm' && 'text-xs px-2 py-1.5',
          size === 'md' && 'text-sm px-3 py-2',
          className
        )}
        style={{
          backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%23858585' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E")`,
        }}
        {...props}
      >
        {options?.map((opt) => (
          <option key={opt.value} value={opt.value} disabled={opt.disabled} title={opt.description}>
            {opt.label}
          </option>
        ))}
        {groups?.map((group) => (
          <optgroup key={group.label} label={group.label}>
            {group.options.map((opt) => (
              <option key={opt.value} value={opt.value} disabled={opt.disabled} title={opt.description}>
                {opt.label}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
    </div>
  )
}
