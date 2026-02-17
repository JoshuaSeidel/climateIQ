import { Link, useRouterState } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { cn } from '@/lib/utils'
import { api } from '@/lib/api'
import { useUIStore } from '@/stores/uiStore'
import {
  Home,
  Settings as SettingsIcon,
  ThermometerSun,
  MessageCircle,
  PanelLeft,
  BarChart3,
} from 'lucide-react'

const links = [
  { to: '/', label: 'Dashboard', icon: Home },
  { to: '/zones', label: 'Zones', icon: ThermometerSun },
  { to: '/analytics', label: 'Analytics', icon: BarChart3 },
  { to: '/chat', label: 'Chat', icon: MessageCircle },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
]

export const Sidebar = () => {
  const { sidebarOpen, toggleSidebar, setSidebarOpen } = useUIStore()
  const routerState = useRouterState()
  const { data: versionData } = useQuery({
    queryKey: ['system-version'],
    queryFn: () => api.get<{ name: string; version: string }>('/system/version'),
    staleTime: Infinity,
  })

  // Close sidebar on mobile when a nav link is clicked
  const handleNavClick = () => {
    if (typeof window !== 'undefined' && window.innerWidth < 1024) {
      setSidebarOpen(false)
    }
  }

  return (
    <aside
      className={cn(
        'fixed inset-y-0 left-0 z-40 flex w-72 flex-col border-r border-border/60 bg-card transition-transform lg:static lg:bg-card/70 lg:backdrop-blur',
        sidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0',
      )}
    >
      <div className="flex items-center justify-between px-6 py-5">
        <div>
          <p className="text-xs uppercase tracking-widest text-muted-foreground">ClimateIQ</p>
          <p className="text-lg font-semibold text-foreground">Home Climate</p>
        </div>
        <button className="rounded-xl border border-border p-2.5 lg:hidden" onClick={toggleSidebar}>
          <PanelLeft className="h-5 w-5" />
        </button>
      </div>
      <nav className="flex flex-1 flex-col gap-1 px-3">
        {links.map((link) => {
          const isActive = routerState.location.pathname === link.to
          return (
            <Link
              key={link.to}
              to={link.to}
              onClick={handleNavClick}
              className={cn(
                'flex items-center gap-3 rounded-xl px-4 py-3 text-sm font-medium transition-colors',
                isActive
                  ? 'bg-primary text-primary-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              <link.icon className="h-5 w-5" />
              {link.label}
            </Link>
          )
        })}
      </nav>
      <div className="px-6 pb-6 text-xs text-muted-foreground">
        <div>&copy; {new Date().getFullYear()} ClimateIQ</div>
        <div className="mt-1 opacity-60">{versionData?.version ? `v${versionData.version}` : 'v...'}</div>
      </div>
    </aside>
  )
}
