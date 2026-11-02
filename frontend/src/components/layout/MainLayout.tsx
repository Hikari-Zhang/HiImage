import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar'

export default function MainLayout() {
  return (
    <div className="app-shell flex h-full w-full">
      <Sidebar />
      <main className="min-w-0 flex-1 flex flex-col overflow-hidden">
        <Outlet />
      </main>
    </div>
  )
}
