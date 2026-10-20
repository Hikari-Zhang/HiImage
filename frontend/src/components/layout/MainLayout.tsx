import { useLocation } from 'react-router-dom'
import type { ComponentType } from 'react'
import Sidebar from './Sidebar'
import WatermarkRemoval from '../../pages/WatermarkRemoval'
import CutoutPage from '../../pages/CutoutPage'
import SuperResolution from '../../pages/SuperResolution'
import SmartSynthesis from '../../pages/SmartSynthesis'
import Settings from '../../pages/Settings'
import Logs from '../../pages/Logs'

// 所有页面常驻挂载，通过 CSS hidden 切换显隐，避免路由切换时卸载组件导致状态丢失。
// isVisible 传入页面组件，用于懒初始化（useFirstVisible），非当前页面延迟到首次可见时再发起网络请求。
const PAGES: Array<{ path: string; Element: ComponentType<{ isVisible?: boolean }> }> = [
  { path: '/watermark',  Element: WatermarkRemoval },
  { path: '/cutout',     Element: CutoutPage },
  { path: '/upscale',    Element: SuperResolution },
  { path: '/synthesis',  Element: SmartSynthesis },
  { path: '/logs',       Element: Logs },
  { path: '/settings',   Element: Settings },
]

export default function MainLayout() {
  const location = useLocation()
  const currentPath = location.pathname === '/' ? '/watermark' : location.pathname

  return (
    <div className="flex h-screen w-screen">
      <Sidebar />
      <main className="flex-1 flex flex-col overflow-hidden">
        {PAGES.map(({ path, Element }) => (
          <div
            key={path}
            className={currentPath === path ? 'flex flex-col flex-1 min-h-0 overflow-hidden' : 'hidden'}
          >
            <Element isVisible={currentPath === path} />
          </div>
        ))}
      </main>
    </div>
  )
}
