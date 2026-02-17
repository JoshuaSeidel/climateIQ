import { Outlet } from '@tanstack/react-router'
import { Header } from '@/components/common/Header'
import { Sidebar } from '@/components/common/Sidebar'
import { useUIStore } from '@/stores/uiStore'

export const Layout = () => {
  const { sidebarOpen } = useUIStore()

  return (
    <div className="flex h-screen bg-background">
      {/* Spacer to reserve width for the fixed sidebar on desktop */}
      <div className="hidden lg:block lg:w-72 lg:flex-shrink-0" />

      {/* Single Sidebar instance — handles its own fixed positioning and mobile toggle */}
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
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
