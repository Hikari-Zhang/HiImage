import { useEffect, useState } from 'react'
import { HashRouter } from 'react-router-dom'
import AppRoutes from './AppRoutes'
import TaskRecoveryNotice from './components/TaskRecoveryNotice'
import { ToastContainer } from './components/ui'
import { useBackendStore } from './stores/useBackendStore'
import { useModelStore } from './stores/useModelStore'
import { useSettingsStore } from './stores/useSettingsStore'
import { showToast } from './components/ui'

export default function App() {
  const backendURL = useBackendStore((s) => s.backendURL)
  const loadModels = useModelStore((s) => s.loadModels)
  const loadSettings = useSettingsStore((s) => s.loadSettings)
  const [settingsHydrated, setSettingsHydrated] = useState(false)
  const [settingsError, setSettingsError] = useState('')

  useEffect(() => {
    loadModels(backendURL)
  }, [backendURL, loadModels])

  useEffect(() => {
    let active = true
    setSettingsHydrated(false)
    setSettingsError('')
    loadSettings(backendURL)
      .catch((error) => {
        if (!active) return
        const message = `读取应用设置失败：${error instanceof Error ? error.message : String(error)}`
        setSettingsError(message)
        showToast('error', message)
      })
      .finally(() => {
        if (active) setSettingsHydrated(true)
      })
    return () => {
      active = false
    }
  }, [backendURL, loadSettings])

  return (
    <HashRouter>
      <div className="h-screen w-screen flex flex-col overflow-hidden">
        <TaskRecoveryNotice backendURL={backendURL} />
        {!settingsHydrated ? (
          <main className="flex-1 min-h-0 bg-bg-primary text-fg-secondary flex items-center justify-center">
            <p className="text-sm">正在加载应用设置…</p>
          </main>
        ) : (
          <>
            {settingsError && (
              <div role="alert" className="bg-status-error/10 text-status-error px-3 py-2 text-xs">
                {settingsError}
              </div>
            )}
            <div className="flex-1 min-h-0"><AppRoutes /></div>
          </>
        )}
        <ToastContainer />
      </div>
    </HashRouter>
  )
}
