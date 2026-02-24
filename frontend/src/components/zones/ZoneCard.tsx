import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Activity, Droplets, Users } from 'lucide-react'
import type { Zone } from '@/types'
import { cn, formatHumidity, formatTemperature, tempUnitLabel } from '@/lib/utils'
import { useSettingsStore } from '@/stores/settingsStore'

type ZoneCardProps = {
  zone: Zone
  /** Schedule target temp already in the user's display unit (°F or °C).
   *  When provided, shown as the zone's target instead of the thermostat setpoint. */
  scheduleTargetTemp?: number | null
  onClick?: () => void
}

export const ZoneCard = ({ zone, scheduleTargetTemp, onClick }: ZoneCardProps) => {
  const { temperatureUnit } = useSettingsStore()
  const unitKey = temperatureUnit === 'celsius' ? 'c' as const : 'f' as const

  const isOccupied = zone.occupancy === 'occupied'
  const isVacant = zone.occupancy === 'vacant'

  return (
    <Card
      className={cn(
        'group relative overflow-hidden',
        // Light mode: clean solid card with subtle shadow
        'bg-card shadow-sm border-border/70',
        // Dark mode: glassmorphism with status-colored left border and glow
        isOccupied && 'dark:border-l-2 dark:border-l-[#4ade80] dark:shadow-[0_0_20px_rgba(74,222,128,0.12)]',
        isVacant && 'dark:border-l-2 dark:border-l-[#38bdf8] dark:shadow-[0_0_20px_rgba(56,189,248,0.12)]',
        !isOccupied && !isVacant && 'dark:border-l-2 dark:border-l-[rgba(148,163,184,0.22)] dark:shadow-[0_0_20px_rgba(148,163,184,0.06)]',
        onClick && 'cursor-pointer transition-all hover:shadow-md dark:hover:shadow-lg',
      )}
      onClick={onClick}
    >
      <CardHeader className="flex flex-row items-center justify-between">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">
            Zone
          </p>
          <CardTitle className="font-black tracking-tight">{zone.name}</CardTitle>
        </div>
        {/* Status indicator dot */}
        <span
          className={cn(
            'h-3 w-3 rounded-full',
            isOccupied && 'bg-[#4ade80] animate-pulse',
            isVacant && 'bg-[#38bdf8]',
            !isOccupied && !isVacant && 'bg-[rgba(148,163,184,0.22)]',
          )}
        />
      </CardHeader>
      <CardContent>
        <div className="flex flex-col gap-6">
          {/* Hero temperature */}
          <div className="flex items-baseline gap-2">
            <span className="text-4xl font-black text-foreground">
              {zone.temperature != null ? formatTemperature(zone.temperature, unitKey) : '--'}
            </span>
            <span className="text-sm text-muted-foreground">
              {/* scheduleTargetTemp is already in display unit — don't pass through formatTemperature */}
              Target {scheduleTargetTemp != null
                ? `${Math.round(scheduleTargetTemp)}${tempUnitLabel(unitKey)}`
                : zone.targetTemperature != null
                  ? formatTemperature(zone.targetTemperature, unitKey)
                  : '--'}
            </span>
          </div>

          {/* Metric chips */}
          <div className="flex flex-wrap gap-2">
            {/* Humidity chip */}
            <span
              className={cn(
                'inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-sm',
                'bg-muted/60 border border-border/40',
                'dark:bg-[rgba(2,6,23,0.30)] dark:border-slate-400/20 dark:backdrop-blur-[10px]',
              )}
            >
              <Droplets className="h-4 w-4 text-muted-foreground" />
              <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">
                Humidity
              </span>
              <span className="font-bold text-foreground">
                {zone.humidity != null ? formatHumidity(zone.humidity) : '--'}
              </span>
            </span>

            {/* Status chip */}
            <span
              className={cn(
                'inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-sm',
                'bg-muted/60 border border-border/40',
                'dark:bg-[rgba(2,6,23,0.30)] dark:border-slate-400/20 dark:backdrop-blur-[10px]',
              )}
            >
              <Activity className="h-4 w-4 text-muted-foreground" />
              <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">
                Status
              </span>
              <span
                className={cn(
                  'font-bold',
                  isOccupied ? 'text-[#4ade80]' : 'text-muted-foreground',
                )}
              >
                {isOccupied ? 'Active' : isVacant ? 'Idle' : '--'}
              </span>
            </span>

            {/* Occupancy chip */}
            <span
              className={cn(
                'inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-sm',
                'bg-muted/60 border border-border/40',
                'dark:bg-[rgba(2,6,23,0.30)] dark:border-slate-400/20 dark:backdrop-blur-[10px]',
              )}
            >
              <Users className="h-4 w-4 text-muted-foreground" />
              <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">
                Occupancy
              </span>
              <span className="font-bold text-foreground">
                {isOccupied ? 'Detected' : isVacant ? 'Clear' : '--'}
              </span>
            </span>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
