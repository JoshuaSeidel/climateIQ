import { useCallback, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { ThemeToggle } from '@/components/common/ThemeToggle'
import { useSettingsStore } from '@/stores/settingsStore'
import { useUIStore } from '@/stores/uiStore'
import { api } from '@/lib/api'
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

  // Fetch current system mode from backend
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
    <header className="flex flex-col gap-4 border-b border-border/60 bg-background/80 px-6 py-4 backdrop-blur lg:flex-row lg:items-center lg:justify-between">
      <div className="flex items-center gap-3">
        <button className="rounded-xl border border-border p-2 lg:hidden" onClick={toggleSidebar}>
          <MonitorSmartphone className="h-5 w-5" />
        </button>
        <div>
          <p className="text-xs uppercase tracking-widest text-muted-foreground">Smart Control</p>
          <h1 className="text-2xl font-semibold text-foreground">Climate Overview</h1>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex flex-col gap-1">
          <div className="flex rounded-2xl border border-border/70 p-1">
            {MODES.map((mode) => (
              <Button
                key={mode.id}
                variant={currentMode === mode.id ? 'default' : 'ghost'}
                size="sm"
                className="px-4"
                onClick={() => handleModeChange(mode.id)}
                title={mode.description}
              >
                {mode.label}
              </Button>
            ))}
          </div>
          {modeError && (
            <div className="flex items-center gap-1 text-xs text-destructive">
              <AlertCircle className="h-3 w-3" />
              {modeError}
            </div>
          )}
        </div>
        <div className="rounded-2xl border border-border/60 px-4 py-2 text-sm text-muted-foreground">
          Unit: {temperatureUnit === 'celsius' ? '°C' : '°F'}
        </div>
        <ThemeToggle />
      </div>
    </header>
  )
}
