import { useCallback, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ThemeToggle } from '@/components/common/ThemeToggle'
import { useSettingsStore } from '@/stores/settingsStore'
import { useUIStore } from '@/stores/uiStore'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { SystemMode, SystemSettings } from '@/types'
import { MonitorSmartphone, AlertCircle } from 'lucide-react'

const MODES: { id: SystemMode; label: string; description: string }[] = [
  { id: 'learn', label: 'Learn', description: 'Observing patterns' },
  { id: 'scheduled', label: 'Scheduled', description: 'Following schedules' },
  { id: 'follow_me', label: 'Follow Me', description: 'Tracking occupancy' },
  { id: 'active', label: 'Active', description: 'AI-driven control' },
]

export const Header = () => {
  const { toggleSidebar } = useUIStore()
  const { temperatureUnit } = useSettingsStore()
  const queryClient = useQueryClient()
  const [modeError, setModeError] = useState<string | null>(null)

  const { data: settings } = useQuery<SystemSettings>({
    queryKey: ['settings'],
    queryFn: () => api.get<SystemSettings>('/settings'),
  })

  const currentMode = settings?.current_mode ?? 'learn'

  const handleModeChange = useCallback(
    async (mode: SystemMode) => {
      try {
        setModeError(null)
        await api.post('/system/mode', { mode })
        queryClient.invalidateQueries({ queryKey: ['settings'] })
      } catch (err) {
        console.error('Failed to change system mode', err)
        setModeError('Failed to change mode. Please try again.')
        setTimeout(() => setModeError(null), 5000)
      }
    },
    [queryClient],
  )

  return (
    <header
      className={cn(
        'flex flex-col gap-4 border-b px-3 py-3 sm:px-6 sm:py-4 lg:flex-row lg:items-center lg:justify-between',
        'border-border/40 bg-background/90 backdrop-blur-sm',
        'dark:border-[rgba(148,163,184,0.12)] dark:bg-[rgba(10,12,16,0.5)] dark:backdrop-blur-xl',
      )}
    >
      <div className="flex items-center gap-3">
        <button
          className="rounded-xl border border-border/60 p-2.5 text-muted-foreground hover:text-foreground dark:border-[rgba(148,163,184,0.2)] lg:hidden"
          onClick={toggleSidebar}
        >
          <MonitorSmartphone className="h-5 w-5" />
        </button>
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">
            Smart Control
          </p>
          <h1 className="text-xl font-black tracking-tight text-foreground sm:text-2xl">
            Climate Overview
          </h1>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        {/* Mode switcher — lane buttons */}
        <div className="flex flex-col gap-1">
          <div
            className={cn(
              'flex flex-wrap rounded-2xl border p-1',
              'border-border/60 bg-muted/40',
              'dark:border-[rgba(148,163,184,0.15)] dark:bg-[rgba(2,6,23,0.35)]',
            )}
          >
            {MODES.map((mode) => {
              const isActive = currentMode === mode.id
              return (
                <button
                  key={mode.id}
                  className={cn(
                    'rounded-xl px-2 py-1.5 text-xs font-bold transition-all sm:px-4 sm:py-2 sm:text-sm',
                    isActive
                      ? [
                          'bg-primary text-primary-foreground shadow-sm',
                          'dark:bg-gradient-to-r dark:from-primary/80 dark:to-primary/50',
                          'dark:border dark:border-primary/40',
                          'dark:shadow-[0_0_14px_rgba(56,189,248,0.2)]',
                        ]
                      : [
                          'text-muted-foreground hover:text-foreground',
                          'dark:hover:bg-white/5',
                        ],
                  )}
                  onClick={() => handleModeChange(mode.id)}
                  title={mode.description}
                >
                  {mode.label}
                </button>
              )
            })}
          </div>
          {modeError && (
            <div className="flex items-center gap-1 text-xs text-destructive">
              <AlertCircle className="h-3 w-3" />
              {modeError}
            </div>
          )}
        </div>

        {/* Temperature unit chip */}
        <div
          className={cn(
            'rounded-full border px-4 py-2 text-xs font-bold',
            'border-border/60 text-muted-foreground',
            'dark:border-[rgba(148,163,184,0.18)] dark:bg-[rgba(2,6,23,0.30)] dark:backdrop-blur-[10px]',
          )}
        >
          {temperatureUnit === 'celsius' ? 'C' : 'F'}
        </div>

        <ThemeToggle />
      </div>
    </header>
  )
}
