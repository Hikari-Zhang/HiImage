import { useEffect, useState } from 'react'
import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import MainLayout from './components/layout/MainLayout'
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
      {!settingsHydrated ? (
        <main className="min-h-screen bg-bg-primary text-fg-secondary flex items-center justify-center">
          <p className="text-sm">正在加载应用设置…</p>
        </main>
      ) : (
        <>
          {settingsError && (
            <div role="alert" className="bg-status-error/10 text-status-error px-3 py-2 text-xs">
              {settingsError}
            </div>
          )}
          <Routes>
            <Route path="/" element={<MainLayout />}>
              <Route index element={<Navigate to="/watermark" replace />} />
              {/* 路径注册，实际渲染由 MainLayout 内的常驻组件处理 */}
              <Route path="watermark" element={null} />
              <Route path="cutout"    element={null} />
              <Route path="upscale"   element={null} />
              <Route path="synthesis" element={null} />
              <Route path="logs"      element={null} />
              <Route path="settings"  element={null} />
            </Route>
          </Routes>
        </>
      )}
      <ToastContainer />
    </HashRouter>
  )
}
