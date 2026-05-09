import { useLocation } from 'react-router-dom'
import Sidebar from './Sidebar'
import WatermarkRemoval from '../../pages/WatermarkRemoval'
import SuperResolution from '../../pages/SuperResolution'
import SmartSynthesis from '../../pages/SmartSynthesis'
import Settings from '../../pages/Settings'
import Logs from '../../pages/Logs'

// 所有页面常驻挂载，通过 CSS hidden 切换显隐，避免路由切换时卸载组件导致状态丢失
const PAGES = [
  { path: '/watermark',  element: <WatermarkRemoval /> },
  { path: '/upscale',    element: <SuperResolution /> },
  { path: '/synthesis',  element: <SmartSynthesis /> },
  { path: '/logs',       element: <Logs /> },
  { path: '/settings',   element: <Settings /> },
]

export default function MainLayout() {
  const location = useLocation()

  return (
    <div className="flex h-screen w-screen">
      <Sidebar />
      <main className="flex-1 flex flex-col overflow-hidden">
        {PAGES.map(({ path, element }) => (
          <div
            key={path}
            className={location.pathname === path ? 'flex flex-col flex-1 min-h-0 overflow-hidden' : 'hidden'}
          >
            {element}
          </div>
        ))}
      </main>
    </div>
  )
}
