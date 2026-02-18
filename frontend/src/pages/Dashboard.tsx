import { useMemo, useEffect, useCallback, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ZoneCard } from '@/components/zones/ZoneCard'
import { api, BASE_PATH } from '@/lib/api'
import { ReconnectingWebSocket } from '@/lib/websocket'
import { useSettingsStore } from '@/stores/settingsStore'
import { formatTemperature, toDisplayTemp, toStorageCelsius } from '@/lib/utils'
import type { Zone, ZonesResponse, SystemSettings, UpcomingSchedule } from '@/types'
import {
  Activity,
  Droplets,
  Gauge,
  Wind,
  Sun,
  Cloud,
  Thermometer,
  Zap,
  Calendar,
  Clock,
  RefreshCw,
  Brain,
  Loader2,
  ChevronUp,
  ChevronDown,
  X,
} from 'lucide-react'

interface WeatherPayload {
  state: string
  temperature: number | null
  humidity: number | null
  pressure: number | null
  wind_speed: number | null
  wind_bearing: number | null
  visibility: number | null
  temperature_unit: string
  pressure_unit: string
  wind_speed_unit: string
  visibility_unit: string
  attribution: string
  entity_id: string
  last_updated: string
}

interface WeatherEnvelope {
  source: 'cache' | 'live'
  cached: boolean
  stale: boolean
  cache_age_seconds: number | null
  fetched_at: string
  data: WeatherPayload
}

interface SystemStats {
  avgTemp: number
  avgHumidity: number
  activeZones: number
  totalZones: number
}

const getWeatherIcon = (state: string | undefined) => {
  const lower = (state ?? '').toLowerCase()
  if (lower.includes('sun') || lower.includes('clear')) return Sun
  if (lower.includes('cloud') || lower.includes('overcast')) return Cloud
  if (lower.includes('rain') || lower.includes('drizzle')) return Cloud
  if (lower.includes('snow') || lower.includes('sleet')) return Cloud
  if (lower.includes('fog') || lower.includes('haz')) return Cloud
  return Sun
}

export const Dashboard = () => {
  const queryClient = useQueryClient()
  const [tempOverride, setTempOverride] = useState<{ zoneId: string; temp: string } | null>(null)
  const [overrideSubmitting, setOverrideSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Fetch zones
  const {
    data: zonesData,
    isLoading: zonesLoading,
    refetch: refetchZones,
  } = useQuery<ZonesResponse>({
    queryKey: ['zones'],
    queryFn: async () => {
      const response = await fetch(`${BASE_PATH}/api/v1/zones`)
      if (!response.ok) throw new Error(`Failed to fetch zones: ${response.status}`)
      const data = await response.json()
      // Backend returns list[ZoneResponse] directly
      const zones = Array.isArray(data) ? data : data.data || []
      // Map backend shape to frontend Zone shape
      const mapped: Zone[] = zones.map((z: Record<string, unknown>) => ({
        id: z.id as string,
        name: z.name as string,
        description: z.description as string | undefined,
        type: z.type as string | undefined,
        floor: z.floor as number | undefined,
        is_active: z.is_active as boolean,
        temperature: (z.current_temp as number | null) ?? 0,
        humidity: (z.current_humidity as number | null) ?? 0,
        occupancy: (z.is_occupied === true ? 'occupied' : z.is_occupied === false ? 'vacant' : 'vacant') as 'occupied' | 'vacant',
        targetTemperature: (z.target_temp as number | null) ?? 22,
        sensors: z.sensors as Zone['sensors'],
        devices: z.devices as Zone['devices'],
      }))
      return mapped
    },
    refetchInterval: 30_000,
  })

  // Fetch system settings (for current mode)
  const { data: settings } = useQuery<SystemSettings>({
    queryKey: ['settings'],
    queryFn: () => api.get<SystemSettings>('/settings'),
  })

  // Hydrate the settings store when backend settings are fetched
  const hydrateStore = useSettingsStore((s) => s.hydrate)
  useEffect(() => {
    if (settings) {
      hydrateStore(settings)
    }
  }, [settings, hydrateStore])

  // Fetch weather
  const { data: weatherEnvelope, isLoading: weatherLoading } = useQuery<WeatherEnvelope | null>({
    queryKey: ['weather'],
    queryFn: async () => {
      const response = await fetch(`${BASE_PATH}/api/v1/weather/current`)
      if (!response.ok) {
        return null
      }
      return response.json()
    },
    retry: false,
    refetchInterval: 5 * 60 * 1000, // re-fetch every 5 minutes
  })

  // Fetch upcoming schedules
  const { data: schedules } = useQuery<UpcomingSchedule[]>({
    queryKey: ['upcoming-schedules'],
    queryFn: async () => {
      const response = await fetch(`${BASE_PATH}/api/v1/schedules/upcoming?hours=24`)
      if (!response.ok) throw new Error(`Failed to fetch schedules: ${response.status}`)
      return response.json()
    },
  })

  // Fetch live energy data from HA entity (only shows if energy_entity is configured)
  const { data: energyData } = useQuery<{
    configured: boolean
    value: number | null
    unit: string | null
    entity_id: string | null
    friendly_name: string | null
  }>({
    queryKey: ['energy-live'],
    queryFn: async () => {
      const response = await fetch(`${BASE_PATH}/api/v1/analytics/energy/live`)
      if (!response.ok) return { configured: false, value: null, unit: null, entity_id: null, friendly_name: null }
      return response.json()
    },
    refetchInterval: 60_000, // refresh every minute
  })

  // Fetch LLM summary
  const {
    data: llmSummary,
    isLoading: summaryLoading,
    refetch: refetchSummary,
  } = useQuery<{ message: string }>({
    queryKey: ['llm-summary'],
    queryFn: async () => {
      const response = await fetch(`${BASE_PATH}/api/v1/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: "Give me a brief summary of what's happening with the home climate right now. Be concise, 2-3 sentences max.",
        }),
      })
      if (!response.ok) throw new Error(`Failed to fetch LLM summary: ${response.status}`)
      const data = await response.json()
      return { message: data.message }
    },
    staleTime: 120_000,
    retry: false,
  })

  // WebSocket for real-time updates (exponential backoff reconnect)
  useEffect(() => {
    const ws = new ReconnectingWebSocket<{ type: string }>('zones')
    const unsubscribe = ws.subscribe((data) => {
      if (data.type === 'zone_update' || data.type === 'sensor_update') {
        queryClient.invalidateQueries({ queryKey: ['zones'] })
      }
    })
    ws.connect()

    return () => {
      unsubscribe()
      ws.close()
    }
  }, [queryClient])

  const zones = useMemo(() => zonesData ?? [], [zonesData])

  // Calculate stats
  const stats: SystemStats = useMemo(() => {
    if (!zones.length) {
      return {
        avgTemp: 0,
        avgHumidity: 0,
        activeZones: 0,
        totalZones: 0,
      }
    }

    const temps = zones.map((z) => z.temperature).filter((t) => Number.isFinite(t))
    const humidities = zones.map((z) => z.humidity).filter((h) => Number.isFinite(h))

    return {
      avgTemp: temps.length ? temps.reduce((a, b) => a + b, 0) / temps.length : 0,
      avgHumidity: humidities.length
        ? humidities.reduce((a, b) => a + b, 0) / humidities.length
        : 0,
      activeZones: zones.filter((z) => z.occupancy === 'occupied').length,
      totalZones: zones.length,
    }
  }, [zones])

  // Temperature override handler — uses the command endpoint for safety clamping
  const handleTempOverride = useCallback(
    async (zoneId: string, temp: number) => {
      setOverrideSubmitting(true)
      try {
        // Convert from display unit back to Celsius for the backend
        const tempC = Math.round(toStorageCelsius(temp, unitKey))
        await api.post('/chat/command', {
          command: `Set zone to ${tempC} degrees celsius`,
          zone_id: zoneId,
        })
        setError(null)
        refetchZones()
        setTempOverride(null)
      } catch {
        setError('Failed to set temperature. Please try again.')
      } finally {
        setOverrideSubmitting(false)
      }
    },
    [refetchZones, unitKey],
  )

  // Quick action handlers
  const handleQuickAction = useCallback(
    async (action: string) => {
      try {
        await api.post('/chat/command', { command: action })
        setError(null)
        refetchZones()
      } catch {
        setError('Failed to execute action. Please try again.')
      }
    },
    [refetchZones],
  )

  const weather = weatherEnvelope?.data ?? null
  const WeatherIcon = weather ? getWeatherIcon(weather.state) : Sun
  const currentMode = settings?.current_mode ?? 'learn'
  const { temperatureUnit } = useSettingsStore()
  const unitKey: 'c' | 'f' = temperatureUnit === 'celsius' ? 'c' : 'f'

  return (
    <div className="space-y-6">
      {error && (
        <div className="flex items-center justify-between rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="ml-4 font-medium underline">
            Dismiss
          </button>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs uppercase tracking-widest text-muted-foreground">Dashboard</p>
          <h2 className="text-2xl font-semibold">Home Overview</h2>
        </div>
        <Button variant="outline" size="sm" onClick={() => refetchZones()}>
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
      </div>

      {/* Stats Grid */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {/* Average Temperature */}
        <Card className="border-border/60 bg-card">
          <CardContent className="flex items-center justify-between p-4">
            <div>
              <p className="text-xs uppercase tracking-widest text-muted-foreground">Avg Temp</p>
              <p className="text-2xl font-semibold text-foreground">
                {stats.avgTemp > 0 ? formatTemperature(stats.avgTemp, unitKey) : '--'}
              </p>
            </div>
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-orange-500/10">
              <Thermometer className="h-6 w-6 text-orange-500" />
            </div>
          </CardContent>
        </Card>

        {/* Average Humidity */}
        <Card className="border-border/60 bg-card">
          <CardContent className="flex items-center justify-between p-4">
            <div>
              <p className="text-xs uppercase tracking-widest text-muted-foreground">Humidity</p>
              <p className="text-2xl font-semibold text-foreground">
                {stats.avgHumidity > 0 ? `${stats.avgHumidity.toFixed(0)}%` : '--'}
              </p>
            </div>
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-blue-500/10">
              <Droplets className="h-6 w-6 text-blue-500" />
            </div>
          </CardContent>
        </Card>

        {/* Active Zones */}
        <Card className="border-border/60 bg-card">
          <CardContent className="flex items-center justify-between p-4">
            <div>
              <p className="text-xs uppercase tracking-widest text-muted-foreground">Active</p>
              <p className="text-2xl font-semibold text-foreground">
                {stats.activeZones} / {stats.totalZones}
              </p>
            </div>
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-green-500/10">
              <Activity className="h-6 w-6 text-green-500" />
            </div>
          </CardContent>
        </Card>

        {/* Energy — only shown when an HA energy entity is configured */}
        {energyData?.configured && (
          <Card className="border-border/60 bg-card">
            <CardContent className="flex items-center justify-between p-4">
              <div>
                <p className="text-xs uppercase tracking-widest text-muted-foreground">Energy</p>
                <p className="text-2xl font-semibold text-foreground">
                  {energyData.value != null ? `${energyData.value.toFixed(1)} ${energyData.unit ?? 'kWh'}` : '--'}
                </p>
              </div>
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-yellow-500/10">
                <Zap className="h-6 w-6 text-yellow-500" />
              </div>
            </CardContent>
          </Card>
        )}
      </div>

      {/* LLM Summary */}
      <Card className="border-border/60">
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="flex items-center gap-2 text-sm font-medium">
            <Brain className="h-4 w-4 text-primary" />
            What's Happening
          </CardTitle>
          <Button variant="ghost" size="sm" onClick={() => refetchSummary()} disabled={summaryLoading}>
            <RefreshCw className={`h-3 w-3 ${summaryLoading ? 'animate-spin' : ''}`} />
          </Button>
        </CardHeader>
        <CardContent>
          {summaryLoading ? (
            <div className="flex items-center gap-2 text-sm">
              <Loader2 className="h-4 w-4 animate-spin" />
              Generating summary...
            </div>
          ) : (
            <p className="text-sm text-foreground">{llmSummary?.message ?? 'No summary available.'}</p>
          )}
        </CardContent>
      </Card>

      {/* Main Content Grid */}
      <div className="grid gap-6 xl:grid-cols-3">
        {/* Zones Section */}
        <div className="xl:col-span-2">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <p className="text-xs uppercase tracking-widest text-muted-foreground">Zones</p>
              <h3 className="text-xl font-semibold text-foreground">Climate Zones</h3>
            </div>
          </div>
          {zonesLoading ? (
            <div className="grid gap-4 md:grid-cols-2">
              {[1, 2, 3, 4].map((i) => (
                <Card key={i} className="h-32 animate-pulse border-border/60 bg-muted/20" />
              ))}
            </div>
          ) : zones.length ? (
            <div className="grid gap-4 md:grid-cols-2">
              {zones.map((zone) => (
                <div key={zone.id} className="relative">
                  <ZoneCard zone={zone} />
                  {/* Temperature Override Button */}
                  <div className="absolute right-2 top-2 flex gap-1">
                    {tempOverride?.zoneId === zone.id ? (
                      <div className="flex items-center gap-1 rounded-lg border border-border bg-card p-1 shadow-lg">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => {
                            const min = unitKey === 'f' ? 50 : 10
                            setTempOverride({
                              zoneId: zone.id,
                              temp: String(Math.max(min, Number(tempOverride.temp) - 1)),
                            })
                          }
                        >
                          <ChevronDown className="h-4 w-4" />
                        </Button>
                        <Input
                          type="number"
                          value={tempOverride.temp}
                          onChange={(e) => setTempOverride({ zoneId: zone.id, temp: e.target.value })}
                          className="h-8 w-14 border-0 p-0 text-center text-xs"
                        />
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => {
                            const max = unitKey === 'f' ? 95 : 35
                            setTempOverride({
                              zoneId: zone.id,
                              temp: String(Math.min(max, Number(tempOverride.temp) + 1)),
                            })
                          }
                        >
                          <ChevronUp className="h-4 w-4" />
                        </Button>
                        <Button
                          size="sm"
                          className="h-8 px-2 text-xs"
                          disabled={overrideSubmitting}
                          onClick={() => handleTempOverride(zone.id, Number(tempOverride.temp))}
                        >
                          {overrideSubmitting ? <Loader2 className="h-3 w-3 animate-spin" /> : 'Set'}
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => setTempOverride(null)}
                        >
                          <X className="h-4 w-4" />
                        </Button>
                      </div>
                    ) : (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 opacity-70 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:hover:opacity-100"
                        onClick={() =>
                          setTempOverride({
                            zoneId: zone.id,
                            temp: String(Math.round(toDisplayTemp(zone.targetTemperature, unitKey))),
                          })
                        }
                        title="Override temperature"
                      >
                        <Thermometer className="h-3 w-3" />
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <Card className="border-dashed border-border/70 bg-card/20 p-8 text-center text-muted-foreground">
              No zones configured yet. Add zones to start monitoring.
            </Card>
          )}
        </div>

        {/* Sidebar */}
        <div className="space-y-6">
          {/* HVAC Status */}
          <Card className="border-border/60">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm font-medium">
                <Gauge className="h-4 w-4 text-muted-foreground" />
                HVAC Status
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm">Mode</span>
                  <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary capitalize">
                    {currentMode.replace('_', ' ')}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm">Zones Active</span>
                  <span className="text-sm font-medium">
                    {stats.activeZones}/{stats.totalZones}
                  </span>
                </div>
                {energyData?.configured && (
                  <div className="flex items-center justify-between">
                    <span className="text-sm">Energy</span>
                    <span className="text-sm font-medium">
                      {energyData.value != null ? `${energyData.value.toFixed(1)} ${energyData.unit ?? 'kWh'}` : '--'}
                    </span>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Weather Widget */}
          <Card className="border-border/60">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm font-medium">
                <Cloud className="h-4 w-4 text-muted-foreground" />
                Current Weather
                {weatherEnvelope?.stale && (
                  <span className="ml-auto text-xs font-normal text-yellow-600">
                    {weatherEnvelope.cache_age_seconds != null
                      ? `${Math.round(weatherEnvelope.cache_age_seconds / 60)}m old`
                      : 'stale'}
                  </span>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent>
              {weatherLoading ? (
                <div className="flex items-center gap-4">
                  <div className="h-16 w-16 animate-pulse rounded-full bg-muted" />
                  <div className="space-y-2">
                    <div className="h-6 w-16 animate-pulse rounded bg-muted" />
                    <div className="h-4 w-24 animate-pulse rounded bg-muted" />
                  </div>
                </div>
              ) : weather ? (
                <div className="flex items-center gap-4">
                  <div className="flex h-16 w-16 items-center justify-center rounded-full bg-primary/10">
                    <WeatherIcon className="h-8 w-8 text-primary" />
                  </div>
                  <div>
                    <p className="text-3xl font-bold">
                      {weather.temperature != null ? `${weather.temperature.toFixed(0)}${weather.temperature_unit}` : '--'}
                    </p>
                    <p className="text-sm capitalize text-muted-foreground">{weather.state}</p>
                  </div>
                </div>
              ) : (
                <p className="text-muted-foreground">Weather data unavailable</p>
              )}
              {weather && (
                <div className="mt-4 grid grid-cols-2 gap-2 border-t border-border/60 pt-4">
                  <div className="flex items-center gap-2 text-sm">
                    <Droplets className="h-4 w-4 text-blue-400" />
                    <span>{weather.humidity != null ? `${weather.humidity}%` : '--'}</span>
                  </div>
                  <div className="flex items-center gap-2 text-sm">
                    <Wind className="h-4 w-4 text-muted-foreground" />
                    <span>
                      {weather.wind_speed != null
                        ? `${weather.wind_speed.toFixed(0)} ${weather.wind_speed_unit}`
                        : '--'}
                    </span>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Upcoming Schedules */}
          <Card className="border-border/60">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm font-medium">
                <Calendar className="h-4 w-4 text-muted-foreground" />
                Upcoming Schedules
              </CardTitle>
            </CardHeader>
            <CardContent>
              {schedules && schedules.length > 0 ? (
                <div className="space-y-3">
                  {schedules.slice(0, 4).map((schedule) => (
                    <div
                      key={schedule.schedule_id}
                      className="flex items-center justify-between rounded-lg border border-border/40 p-2"
                    >
                      <div>
                        <p className="text-sm font-medium">{schedule.schedule_name}</p>
                        <p className="text-xs text-muted-foreground">
                          {schedule.zone_name || 'All zones'}
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-sm font-medium">{formatTemperature(schedule.target_temp_c, unitKey)}</p>
                        <p className="flex items-center gap-1 text-xs text-muted-foreground">
                          <Clock className="h-3 w-3" />
                          {new Date(schedule.start_time).toLocaleTimeString([], {
                            hour: '2-digit',
                            minute: '2-digit',
                          })}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">No upcoming schedules</p>
              )}
            </CardContent>
          </Card>

          {/* Quick Actions */}
          <Card className="border-border/60">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Quick Actions</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-2">
              <Button
                variant="outline"
                size="sm"
                className="justify-start"
                onClick={() => handleQuickAction('Set all zones to eco mode')}
              >
                <Thermometer className="mr-2 h-4 w-4" />
                Eco Mode
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="justify-start"
                onClick={() => handleQuickAction('Set all zones to away mode')}
              >
                <Activity className="mr-2 h-4 w-4" />
                Away Mode
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="justify-start"
                onClick={() => handleQuickAction('Boost heating in all zones by 2 degrees')}
              >
                <Gauge className="mr-2 h-4 w-4" />
                Boost Heat
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="justify-start"
                onClick={() => handleQuickAction('Boost cooling in all zones by 2 degrees')}
              >
                <Wind className="mr-2 h-4 w-4" />
                Boost Cool
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
