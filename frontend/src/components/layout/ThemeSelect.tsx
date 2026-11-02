import { useLayoutEffect, useState } from 'react'
import { Sun, Moon, Monitor } from 'lucide-react'

type Theme = 'light' | 'dark' | 'system'

function readTheme(): Theme {
  try {
    const saved = localStorage.getItem('hiimage-theme')
    if (saved === 'dark' || saved === 'system') return saved
  } catch { /* Use the default when storage is unavailable. */ }
  return 'light'
}

export default function ThemeSelect({ collapsed }: { collapsed: boolean }) {
  const [theme, setTheme] = useState<Theme>(readTheme)

  useLayoutEffect(() => {
    const media = window.matchMedia?.('(prefers-color-scheme: dark)')
    const applyTheme = () => {
      document.documentElement.dataset.theme = theme === 'system'
        ? (media?.matches ? 'dark' : 'light') : theme
    }
    applyTheme()
    try { localStorage.setItem('hiimage-theme', theme) } catch { /* Keep the selection for this session. */ }
    media?.addEventListener('change', applyTheme)
    return () => media?.removeEventListener('change', applyTheme)
  }, [theme])

  const Icon = theme === 'system' ? Monitor : theme === 'dark' ? Moon : Sun

  return (
    <div className={`theme-select relative flex items-center gap-2 rounded-lg hover:bg-bg-hover ${collapsed ? 'justify-center p-2' : 'px-2 py-1.5'}`}>
      <Icon size={16} className="flex-shrink-0 text-fg-secondary" aria-hidden="true" />
      <select
        aria-label="界面外观"
        title="界面外观"
        value={theme}
        onChange={(event) => setTheme(event.target.value as Theme)}
        className={collapsed
          ? 'absolute inset-0 w-full opacity-0 cursor-pointer'
          : 'min-w-0 flex-1 bg-transparent text-xs text-fg-primary py-1'}
      >
        <option value="light">浅色</option>
        <option value="dark">深色</option>
        <option value="system">跟随系统</option>
      </select>
    </div>
  )
}
