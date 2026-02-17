import { Outlet } from '@tanstack/react-router'
import { Header } from '@/components/common/Header'
import { Sidebar } from '@/components/common/Sidebar'
import { useUIStore } from '@/stores/uiStore'

export const Layout = () => {
  const { sidebarOpen } = useUIStore()

  return (
    <div className="flex h-screen bg-background">
      {/* Sidebar is position:fixed on mobile, lg:static on desktop (takes its own space) */}
      <Sidebar />

      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-background/80 backdrop-blur-sm lg:hidden"
          onClick={() => useUIStore.getState().setSidebarOpen(false)}
        />
      )}

      {/* Main content */}
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-3 sm:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
