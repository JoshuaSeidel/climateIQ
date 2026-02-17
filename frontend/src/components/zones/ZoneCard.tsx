import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { BadgeCheck, Activity, Droplets, Users } from 'lucide-react'
import type { Zone } from '@/types'
import { cn, formatHumidity, formatTemperature } from '@/lib/utils'
import { useSettingsStore } from '@/stores/settingsStore'

type ZoneCardProps = {
  zone: Zone
}

export const ZoneCard = ({ zone }: ZoneCardProps) => {
  const { temperatureUnit } = useSettingsStore()
  return (
    <Card className="relative overflow-hidden border-border/70 bg-gradient-to-br from-card to-card-muted">
      <CardHeader className="flex flex-row items-center justify-between">
        <div>
          <p className="text-xs uppercase tracking-widest text-muted-foreground">Zone</p>
          <CardTitle>{zone.name}</CardTitle>
        </div>
        <BadgeCheck className="h-5 w-5 text-primary" />
      </CardHeader>
      <CardContent>
        <div className="flex flex-col gap-6">
          <div className="flex items-baseline gap-2">
            <span className="text-4xl font-semibold text-foreground">
              {formatTemperature(zone.temperature, temperatureUnit === 'celsius' ? 'c' : 'f')}
            </span>
            <span className="text-sm text-muted-foreground">Target {formatTemperature(zone.targetTemperature, temperatureUnit === 'celsius' ? 'c' : 'f')}</span>
          </div>
          <div className="grid grid-cols-3 gap-4 text-sm text-muted-foreground">
            <div className="flex flex-col gap-1">
              <span className="flex items-center gap-2 text-foreground">
                <Droplets className="h-4 w-4" /> Humidity
              </span>
              <span className="text-lg font-semibold text-foreground">{formatHumidity(zone.humidity)}</span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="flex items-center gap-2 text-foreground">
                <Activity className="h-4 w-4" /> Status
              </span>
              <span className={cn('text-lg font-semibold', zone.occupancy === 'occupied' ? 'text-primary' : 'text-muted-foreground')}>
                {zone.occupancy === 'occupied' ? 'Active' : 'Idle'}
              </span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="flex items-center gap-2 text-foreground">
                <Users className="h-4 w-4" /> Occupancy
              </span>
              <span className="text-lg font-semibold text-foreground">{zone.occupancy === 'occupied' ? 'Detected' : 'Clear'}</span>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
