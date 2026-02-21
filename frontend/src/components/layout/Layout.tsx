import { Outlet } from '@tanstack/react-router'
import { Header } from '@/components/common/Header'
import { Sidebar } from '@/components/common/Sidebar'
import { useUIStore } from '@/stores/uiStore'

export const Layout = () => {
  const { sidebarOpen } = useUIStore()

  return (
    <div className="relative flex h-screen bg-background">
      {/* Ambient gradient overlays — dark mode only */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden dark:block hidden">
        <div className="absolute -top-32 -left-32 h-[500px] w-[500px] rounded-full bg-[radial-gradient(circle,rgba(56,189,248,0.08)_0%,transparent_70%)]" />
        <div className="absolute -top-24 -right-24 h-[400px] w-[400px] rounded-full bg-[radial-gradient(circle,rgba(250,204,21,0.05)_0%,transparent_70%)]" />
      </div>

      <Sidebar />

      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-background/80 backdrop-blur-sm lg:hidden"
          onClick={() => useUIStore.getState().setSidebarOpen(false)}
        />
      )}

      {/* Main content */}
      <div className="relative flex flex-1 flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-3 sm:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
