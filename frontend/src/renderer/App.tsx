import { useEffect } from 'react'
import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import MainLayout from './components/layout/MainLayout'
import { ToastContainer } from './components/ui'
import { useBackendStore } from './stores/useBackendStore'
import { useModelStore } from './stores/useModelStore'

export default function App() {
  const backendURL = useBackendStore((s) => s.backendURL)
  const loadModels = useModelStore((s) => s.loadModels)

  useEffect(() => {
    loadModels(backendURL)
  }, [backendURL, loadModels])

  return (
    <HashRouter>
      <Routes>
        <Route path="/" element={<MainLayout />}>
          <Route index element={<Navigate to="/watermark" replace />} />
          {/* 路径注册，实际渲染由 MainLayout 内的常驻组件处理 */}
          <Route path="watermark" element={null} />
          <Route path="upscale"   element={null} />
          <Route path="synthesis" element={null} />
          <Route path="logs"      element={null} />
          <Route path="settings"  element={null} />
        </Route>
      </Routes>
      <ToastContainer />
    </HashRouter>
  )
}
