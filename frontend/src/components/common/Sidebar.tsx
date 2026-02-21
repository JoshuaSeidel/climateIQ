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
  CalendarClock,
} from 'lucide-react'

const links = [
  { to: '/', label: 'Dashboard', icon: Home },
  { to: '/zones', label: 'Zones', icon: ThermometerSun },
  { to: '/schedules', label: 'Schedules', icon: CalendarClock },
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
        'fixed inset-y-0 left-0 z-40 flex w-72 flex-col border-r transition-transform',
        'border-border/40 bg-card',
        'dark:border-[rgba(148,163,184,0.12)] dark:bg-[rgba(10,12,16,0.78)] dark:backdrop-blur-xl',
        sidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0',
        'lg:static',
      )}
    >
      {/* Brand */}
      <div className="flex items-center justify-between px-6 py-5">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">
            ClimateIQ
          </p>
          <p className="text-lg font-black tracking-tight text-foreground">Home Climate</p>
        </div>
        <button
          className="rounded-xl border border-border/60 p-2.5 text-muted-foreground hover:text-foreground dark:border-[rgba(148,163,184,0.2)] lg:hidden"
          onClick={toggleSidebar}
        >
          <PanelLeft className="h-5 w-5" />
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex flex-1 flex-col gap-1 px-3">
        {links.map((link) => {
          const isActive = routerState.location.pathname === link.to
          return (
            <Link
              key={link.to}
              to={link.to}
              onClick={handleNavClick}
              className={cn(
                'flex items-center gap-3 rounded-xl px-4 py-3 text-sm font-semibold transition-all',
                isActive
                  ? [
                      'bg-primary text-primary-foreground shadow-sm',
                      'dark:bg-gradient-to-r dark:from-primary/80 dark:to-primary/50',
                      'dark:border dark:border-primary/40',
                      'dark:shadow-[0_0_18px_rgba(56,189,248,0.2)]',
                    ]
                  : [
                      'text-muted-foreground hover:text-foreground hover:bg-muted/60',
                      'dark:hover:bg-white/5',
                    ],
              )}
            >
              <link.icon className="h-5 w-5" />
              {link.label}
            </Link>
          )
        })}
      </nav>

      {/* Footer */}
      <div className="px-6 pb-6">
        <div className="text-[11px] font-medium text-muted-foreground/70">
          &copy; {new Date().getFullYear()} ClimateIQ
        </div>
        <div className="mt-0.5 text-[11px] font-medium text-muted-foreground/50">
          {versionData?.version ? `v${versionData.version}` : 'v...'}
        </div>
      </div>
    </aside>
  )
}
