import { Navigate, Route, Routes } from 'react-router-dom'
import MainLayout from './components/layout/MainLayout'
import LazyPage from './components/layout/PageBoundary'

const pages = [
  { path: 'watermark', load: () => import('./pages/WatermarkRemoval') },
  { path: 'cutout', load: () => import('./pages/CutoutPage') },
  { path: 'upscale', load: () => import('./pages/SuperResolution') },
  { path: 'synthesis', load: () => import('./pages/SmartSynthesis') },
  { path: 'logs', load: () => import('./pages/Logs') },
  { path: 'settings', load: () => import('./pages/Settings') },
]

export default function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<MainLayout />}>
        <Route index element={<Navigate to="/watermark" replace />} />
        {pages.map(({ path, load }) => (
          <Route key={path} path={path} element={<LazyPage key={path} load={load} />} />
        ))}
        <Route path="*" element={<Navigate to="/watermark" replace />} />
      </Route>
    </Routes>
  )
}
