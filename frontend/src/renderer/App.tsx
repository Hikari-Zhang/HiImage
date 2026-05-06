import { useEffect } from 'react'
import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import MainLayout from './components/layout/MainLayout'
import WatermarkRemoval from './pages/WatermarkRemoval'
import SuperResolution from './pages/SuperResolution'
import SmartSynthesis from './pages/SmartSynthesis'
import Settings from './pages/Settings'
import Logs from './pages/Logs'
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
          <Route path="watermark" element={<WatermarkRemoval />} />
          <Route path="upscale" element={<SuperResolution />} />
          <Route path="synthesis" element={<SmartSynthesis />} />
          <Route path="logs" element={<Logs />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
      <ToastContainer />
    </HashRouter>
  )
}
