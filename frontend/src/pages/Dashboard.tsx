import { useMemo, useEffect, useCallback, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ZoneCard } from '@/components/zones/ZoneCard'
import { api, BASE_PATH } from '@/lib/api'
import { ReconnectingWebSocket } from '@/lib/websocket'
import { useSettingsStore } from '@/stores/settingsStore'
import { formatTemperature, toDisplayTemp, toStorageCelsius, tempUnitLabel } from '@/lib/utils'
import type { Zone, ZonesResponse, SystemSettings, UpcomingSchedule, ActiveScheduleResponse, OverrideStatus } from '@/types'
import {
  Activity,
  Droplets,
  Gauge,
  Users,
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
  Minus,
  Plus,
  RotateCcw,
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
  avgTargetTemp: number
}

/** Return null if temperature (Celsius) is outside plausible range */
function validateTempC(value: number | null | undefined): number | null {
  if (value == null || !Number.isFinite(value)) return null
  if (value < -40 || value > 60) return null
  return value
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
  const navigate = useNavigate()
  const [tempOverride, setTempOverride] = useState<{ zoneId: string; temp: string } | null>(null)
  const [overrideSubmitting, setOverrideSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [manualTemp, setManualTemp] = useState<number | null>(null)

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
        temperature: validateTempC(z.current_temp as number | null | undefined),
        humidity: (z.current_humidity as number | null) ?? null,
        lux: (z.current_lux as number | null) ?? null,
        occupancy: z.is_occupied === true ? 'occupied' : z.is_occupied === false ? 'vacant' : null,
        targetTemperature: (z.target_temp as number | null) ?? null,
        sensors: z.sensors as Zone['sensors'],
        devices: z.devices as Zone['devices'],
      }))
      return mapped
    },
    refetchInterval: 15_000,
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

  // Fetch currently active schedule
  const { data: activeSchedule } = useQuery<ActiveScheduleResponse>({
    queryKey: ['active-schedule'],
    queryFn: async () => {
      const response = await fetch(`${BASE_PATH}/api/v1/schedules/active`)
      if (!response.ok) return { active: false, schedule: null }
      return response.json()
    },
    refetchInterval: 30_000, // check every 30 seconds
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

  // Fetch thermostat override status
  const { data: overrideStatus, refetch: refetchOverride } = useQuery<OverrideStatus>({
    queryKey: ['override-status'],
    queryFn: () => api.get<OverrideStatus>('/system/override'),
    refetchInterval: 10_000,
  })

  // Initialize manualTemp from schedule target temp when it loads
  useEffect(() => {
    if (overrideStatus?.schedule_target_temp != null && manualTemp === null) {
      setManualTemp(Math.round(overrideStatus.schedule_target_temp))
    }
  }, [overrideStatus, manualTemp])

  // Mutation for setting manual override
  const overrideMutation = useMutation({
    mutationFn: (temperature: number) =>
      api.post<{ success: boolean; message: string }>('/system/override', { temperature }),
    onSuccess: (result) => {
      if (result.success) {
        setError(null)
        refetchOverride()
        refetchZones()
      } else {
        setError(result.message || 'Override failed.')
      }
    },
    onError: () => {
      setError('Failed to set temperature override. Please try again.')
    },
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
        avgTargetTemp: 0,
      }
    }

    const temps = zones.map((z) => z.temperature).filter((t): t is number => t != null && Number.isFinite(t) && t >= -40 && t <= 60)
    const humidities = zones.map((z) => z.humidity).filter((h): h is number => h != null && Number.isFinite(h))
    const targets = zones.map((z) => z.targetTemperature).filter((t): t is number => t != null && Number.isFinite(t))

    return {
      avgTemp: temps.length ? temps.reduce((a, b) => a + b, 0) / temps.length : 0,
      avgHumidity: humidities.length
        ? humidities.reduce((a, b) => a + b, 0) / humidities.length
        : 0,
      activeZones: zones.filter((z) => z.occupancy === 'occupied').length,
      totalZones: zones.length,
      avgTargetTemp: targets.length ? targets.reduce((a, b) => a + b, 0) / targets.length : 0,
    }
  }, [zones])

  const { temperatureUnit } = useSettingsStore()
  const unitKey: 'c' | 'f' = temperatureUnit === 'celsius' ? 'c' : 'f'

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

  // Quick action handlers — call the dedicated quick-action endpoint
  const handleQuickAction = useCallback(
    async (action: string) => {
      try {
        const result = await api.post<{ success: boolean; message: string }>('/system/quick-action', { action })
        if (result.success) {
          setError(null)
        } else {
          setError(result.message || 'Action failed.')
        }
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

  return (
    <div className="space-y-6">
      {error && (
        <div className="flex items-center justify-between rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive dark:bg-red-500/10 dark:border-red-500/30 dark:backdrop-blur-xl">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="ml-4 font-medium underline">
            Dismiss
          </button>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Dashboard</p>
          <h2 className="text-2xl font-black tracking-tight">Home Overview</h2>
        </div>
        <Button variant="outline" size="sm" onClick={() => refetchZones()}>
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
      </div>

      {/* Stats Grid */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {/* Average Temperature + Set Temp */}
        {(() => {
          // Use hvac_action from HA — the actual running state, not the mode.
          // Values: "heating", "cooling", "idle", "off", "fan"
          const action = overrideStatus?.hvac_action ?? null
          const isHeating = action === 'heating'
          const isCooling = action === 'cooling'

          const iconBg = isHeating
            ? 'bg-orange-500/10 dark:bg-orange-500/15 dark:shadow-[0_0_12px_rgba(249,115,22,0.15)]'
            : isCooling
            ? 'bg-blue-500/10 dark:bg-blue-500/15 dark:shadow-[0_0_12px_rgba(59,130,246,0.15)]'
            : 'bg-muted/40'
          const iconColor = isHeating ? 'text-orange-500' : isCooling ? 'text-blue-500' : 'text-muted-foreground'
          const badgeColor = isHeating
            ? 'text-orange-500'
            : isCooling
            ? 'text-blue-500'
            : 'text-muted-foreground'
          const badgeLabel = isHeating ? '▲ Heating' : isCooling ? '▼ Cooling' : '— Idle'

          return (
            <Card>
              <CardContent className="flex items-center justify-between p-4">
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Avg Temp</p>
                  <p className="text-3xl font-black text-foreground">
                    {/* Prefer schedule-zone avg when a schedule is active — avoids
                        showing the all-rooms average when only one zone is targeted */}
                    {overrideStatus?.schedule_avg_temp != null
                      ? `${overrideStatus.schedule_avg_temp}${tempUnitLabel(unitKey)}`
                      : stats.avgTemp > 0 ? formatTemperature(stats.avgTemp, unitKey) : '--'}
                  </p>
                  {overrideStatus?.schedule_target_temp != null && (
                    <p className="text-xs text-muted-foreground">
                      Set: {Math.round(overrideStatus.schedule_target_temp)}{tempUnitLabel(unitKey)}
                    </p>
                  )}
                  {action && action !== 'off' && (
                    <p className={`text-xs font-medium mt-0.5 ${badgeColor}`}>{badgeLabel}</p>
                  )}
                </div>
                <div className={`flex h-12 w-12 items-center justify-center rounded-full ${iconBg}`}>
                  <Thermometer className={`h-6 w-6 ${iconColor}`} />
                </div>
              </CardContent>
            </Card>
          )
        })()}

        {/* Average Humidity */}
        <Card>
          <CardContent className="flex items-center justify-between p-4">
            <div>
              <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Humidity</p>
              <p className="text-3xl font-black text-foreground">
                {stats.avgHumidity > 0 ? `${stats.avgHumidity.toFixed(0)}%` : '--'}
              </p>
            </div>
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-blue-500/10 dark:bg-blue-500/15 dark:shadow-[0_0_12px_rgba(59,130,246,0.15)]">
              <Droplets className="h-6 w-6 text-blue-500" />
            </div>
          </CardContent>
        </Card>

        {/* Occupied Zones */}
        <Card>
          <CardContent className="flex items-center justify-between p-4">
            <div>
              <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Occupied</p>
              <p className="text-3xl font-black text-foreground">
                {stats.activeZones} / {stats.totalZones}
              </p>
            </div>
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-green-500/10 dark:bg-green-500/15 dark:shadow-[0_0_12px_rgba(34,197,94,0.15)]">
              <Users className="h-6 w-6 text-green-500" />
            </div>
          </CardContent>
        </Card>

        {/* Energy — only shown when an HA energy entity is configured */}
        {energyData?.configured && (
          <Card>
            <CardContent className="flex items-center justify-between p-4">
              <div>
                <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Energy</p>
                <p className="text-3xl font-black text-foreground">
                  {energyData.value != null ? `${energyData.value.toFixed(1)} ${energyData.unit ?? 'kWh'}` : '--'}
                </p>
              </div>
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-yellow-500/10 dark:bg-yellow-500/15 dark:shadow-[0_0_12px_rgba(234,179,8,0.15)]">
                <Zap className="h-6 w-6 text-yellow-500" />
              </div>
            </CardContent>
          </Card>
        )}
      </div>

      {/* Manual Override */}
      <Card className="overflow-hidden dark:bg-[rgba(10,12,16,0.85)] dark:backdrop-blur-xl dark:border-[rgba(148,163,184,0.12)]">
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2">
            <Thermometer className="h-4 w-4 text-orange-500 dark:drop-shadow-[0_0_6px_rgba(249,115,22,0.4)]" />
            <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Manual Override</span>
            {overrideStatus?.is_override_active && (
              <span className="ml-auto rounded-full bg-orange-500/15 px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.15em] text-orange-500 border border-orange-500/30">
                Override Active
              </span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:gap-6">
            {/* Temperature display and controls */}
            <div className="flex items-center gap-3">
              <Button
                variant="outline"
                size="icon"
                className="h-10 w-10 rounded-full border-[rgba(148,163,184,0.2)] dark:bg-[rgba(2,6,23,0.45)] dark:hover:bg-[rgba(2,6,23,0.7)]"
                onClick={() => {
                  const min = unitKey === 'f' ? 50 : 10
                  setManualTemp((prev) => Math.max(min, (prev ?? (unitKey === 'f' ? 72 : 22)) - 1))
                }}
              >
                <Minus className="h-4 w-4" />
              </Button>
              <div className="flex flex-col items-center">
                <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Target Temperature</p>
                <p className="text-5xl font-black tabular-nums text-foreground dark:drop-shadow-[0_0_20px_rgba(249,115,22,0.2)]">
                  {manualTemp ?? (overrideStatus?.schedule_target_temp != null ? Math.round(overrideStatus.schedule_target_temp) : '--')}
                  <span className="text-2xl font-bold text-muted-foreground">{tempUnitLabel(unitKey)}</span>
                </p>
              </div>
              <Button
                variant="outline"
                size="icon"
                className="h-10 w-10 rounded-full border-[rgba(148,163,184,0.2)] dark:bg-[rgba(2,6,23,0.45)] dark:hover:bg-[rgba(2,6,23,0.7)]"
                onClick={() => {
                  const max = unitKey === 'f' ? 95 : 35
                  setManualTemp((prev) => Math.min(max, (prev ?? (unitKey === 'f' ? 72 : 22)) + 1))
                }}
              >
                <Plus className="h-4 w-4" />
              </Button>
            </div>

            {/* Slider */}
            <div className="flex-1">
              <input
                type="range"
                min={unitKey === 'f' ? 50 : 10}
                max={unitKey === 'f' ? 95 : 35}
                value={manualTemp ?? (overrideStatus?.schedule_target_temp != null ? Math.round(overrideStatus.schedule_target_temp) : (unitKey === 'f' ? 72 : 22))}
                onChange={(e) => setManualTemp(Number(e.target.value))}
                className="w-full accent-orange-500"
              />
              <div className="mt-1 flex justify-between text-[10px] font-bold uppercase tracking-[0.15em] text-muted-foreground">
                <span>{unitKey === 'f' ? '50' : '10'}{tempUnitLabel(unitKey)}</span>
                <span>{unitKey === 'f' ? '95' : '35'}{tempUnitLabel(unitKey)}</span>
              </div>
            </div>

            {/* Action buttons */}
            <div className="flex gap-2 sm:flex-col">
              <Button
                size="sm"
                className="flex-1 bg-orange-500 text-white hover:bg-orange-600 dark:shadow-[0_0_12px_rgba(249,115,22,0.25)]"
                disabled={overrideMutation.isPending || manualTemp === null}
                onClick={() => {
                  if (manualTemp !== null) {
                    overrideMutation.mutate(manualTemp)
                  }
                }}
              >
                {overrideMutation.isPending ? (
                  <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                ) : (
                  <Thermometer className="mr-2 h-3 w-3" />
                )}
                Set Override
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="flex-1"
                onClick={async () => {
                  await handleQuickAction('resume')
                  refetchOverride()
                }}
              >
                <RotateCcw className="mr-2 h-3 w-3" />
                Resume Schedule
              </Button>
            </div>
          </div>

          {/* Status bar */}
          <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border/40 pt-3 dark:border-[rgba(148,163,184,0.12)]">
            <span className="text-xs text-muted-foreground">
              Thermostat: <span className="font-bold text-foreground">{overrideStatus?.current_temp != null ? `${overrideStatus.current_temp}${tempUnitLabel(unitKey)}` : '--'}</span>
              {overrideStatus?.target_temp != null && (
                <> → <span className="font-bold text-foreground">{overrideStatus.target_temp}{tempUnitLabel(unitKey)}</span></>
              )}
            </span>
            <span className="text-xs text-muted-foreground">
              Current Temp: <span className="font-bold text-foreground">{overrideStatus?.schedule_avg_temp != null ? `${overrideStatus.schedule_avg_temp}${tempUnitLabel(unitKey)}` : '--'}</span>
            </span>
            <span className="text-xs text-muted-foreground">
              All Zones: <span className="font-bold text-foreground">{overrideStatus?.all_zones_avg_temp != null ? `${overrideStatus.all_zones_avg_temp}${tempUnitLabel(unitKey)}` : '--'}</span>
            </span>
            <span className="text-xs text-muted-foreground">
              Mode: <span className="font-bold capitalize text-foreground">{overrideStatus?.hvac_mode ?? '--'}</span>
            </span>
            <span className="text-xs text-muted-foreground">
              Status: <span className={`font-bold ${overrideStatus?.is_override_active ? 'text-orange-500' : 'text-green-500'}`}>
                {overrideStatus?.is_override_active ? 'Override Active' : 'Following Schedule'}
              </span>
            </span>
            {overrideStatus?.preset_mode && overrideStatus.preset_mode.toLowerCase() !== 'none' && (
              <span className="text-xs text-muted-foreground">
                Preset: <span className="font-bold capitalize text-foreground">{overrideStatus.preset_mode}</span>
              </span>
            )}
            {overrideStatus?.schedule_zone_names && (
              <span className="text-xs text-muted-foreground">
                Targeting <span className="font-bold text-foreground">{overrideStatus.schedule_zone_names}</span>
                {overrideStatus.offset_info?.offset_f != null && Math.abs(overrideStatus.offset_info.offset_f) > 0.5 && (
                  <>{' '}(offset: <span className="font-bold text-foreground">
                    {overrideStatus.offset_info.offset_f > 0 ? '+' : ''}{unitKey === 'f'
                      ? `${overrideStatus.offset_info.offset_f.toFixed(1)}${tempUnitLabel('f')}`
                      : `${overrideStatus.offset_info.offset_c?.toFixed(1) ?? '0'}${tempUnitLabel('c')}`}
                  </span>)</>
                )}
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      {/* LLM Summary */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <CardTitle className="flex items-center gap-2 text-sm font-bold">
            <Brain className="h-4 w-4 text-primary dark:text-primary dark:drop-shadow-[0_0_6px_rgba(56,189,248,0.4)]" />
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
              <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Zones</p>
              <h3 className="text-xl font-black tracking-tight text-foreground">Climate Zones</h3>
            </div>
          </div>
          {zonesLoading ? (
            <div className="grid gap-4 md:grid-cols-2">
              {[1, 2, 3, 4].map((i) => (
                <Card key={i} className="h-32 animate-pulse bg-muted/20" />
              ))}
            </div>
          ) : zones.length ? (
            <div className="grid gap-4 md:grid-cols-2">
              {zones.map((zone) => {
                // Determine whether this zone is targeted by the active schedule.
                // zone_ids empty means ALL zones are targeted; otherwise check membership.
                const activeZoneIds = activeSchedule?.schedule?.zone_ids ?? []
                const inActiveSchedule =
                  activeSchedule?.active === true &&
                  (activeZoneIds.length === 0 || activeZoneIds.includes(zone.id))
                // scheduleTargetTemp is already in the user's display unit (from overrideStatus)
                const scheduleTargetTemp =
                  inActiveSchedule ? (overrideStatus?.schedule_target_temp ?? null) : null
                return (
                <div key={zone.id} className="relative">
                  <ZoneCard zone={zone} scheduleTargetTemp={scheduleTargetTemp} onClick={() => navigate({ to: '/zones', search: { zone: zone.id } })} />
                  {/* Temperature Override Button */}
                  <div className="absolute right-2 top-2 flex gap-1">
                    {tempOverride?.zoneId === zone.id ? (
                      <div className="flex items-center gap-1 rounded-lg border border-border bg-card p-1 shadow-lg dark:bg-[rgba(10,12,16,0.85)] dark:border-[rgba(148,163,184,0.2)] dark:backdrop-blur-xl">
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
                          }}
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
                          }}
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
                            temp: String(Math.round(toDisplayTemp(zone.targetTemperature ?? 22, unitKey))),
                          })
                        }
                        title="Override temperature"
                      >
                        <Thermometer className="h-3 w-3" />
                      </Button>
                    )}
                  </div>
                </div>
                )
              })}
            </div>
          ) : (
            <Card className="border-dashed bg-card/20 p-8 text-center text-muted-foreground">
              No zones configured yet. Add zones to start monitoring.
            </Card>
          )}
        </div>

        {/* Sidebar */}
        <div className="space-y-6">
          {/* HVAC Status */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm font-bold">
                <Gauge className="h-4 w-4 text-muted-foreground" />
                HVAC Status
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm">Mode</span>
                  <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium capitalize dark:bg-primary/15 dark:text-primary dark:border dark:border-primary/30">
                    {currentMode.replace('_', ' ')}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm">Zones Occupied</span>
                  <span className="text-sm font-bold">
                    {stats.activeZones}/{stats.totalZones}
                  </span>
                </div>
                {energyData?.configured && (
                  <div className="flex items-center justify-between">
                    <span className="text-sm">Energy</span>
                    <span className="text-sm font-bold">
                      {energyData.value != null ? `${energyData.value.toFixed(1)} ${energyData.unit ?? 'kWh'}` : '--'}
                    </span>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Weather Widget */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm font-bold">
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
                  <div className="flex h-16 w-16 items-center justify-center rounded-full bg-primary/10 dark:bg-primary/15 dark:shadow-[0_0_12px_rgba(56,189,248,0.2)]">
                    <WeatherIcon className="h-8 w-8 text-primary" />
                  </div>
                  <div>
                    <p className="text-4xl font-black">
                      {weather.temperature != null ? `${weather.temperature.toFixed(0)}${weather.temperature_unit}` : '--'}
                    </p>
                    <p className="font-bold capitalize text-muted-foreground">{weather.state}</p>
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
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm font-bold">
                <Calendar className="h-4 w-4 text-muted-foreground" />
                Upcoming Schedules
              </CardTitle>
            </CardHeader>
            <CardContent>
              {/* Active schedule indicator */}
              {activeSchedule?.active && activeSchedule.schedule && (
                <div className="mb-3 flex items-center justify-between rounded-lg border border-green-500/30 bg-green-500/5 p-2 dark:bg-green-500/10 dark:border-green-500/20">
                  <div>
                    <div className="flex items-center gap-1.5">
                      <span className="relative flex h-2 w-2">
                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-75" />
                        <span className="relative inline-flex h-2 w-2 rounded-full bg-green-500" />
                      </span>
                      <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-green-600 dark:text-green-400">Now Active</p>
                    </div>
                    <p className="text-sm font-bold">{activeSchedule.schedule.schedule_name}</p>
                    <p className="text-xs text-muted-foreground">
                      {activeSchedule.schedule.zone_names?.length
                        ? activeSchedule.schedule.zone_names.join(', ')
                        : 'All zones'}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-sm font-bold">{formatTemperature(activeSchedule.schedule.target_temp_c, unitKey)}</p>
                    {activeSchedule.schedule.end_time && (
                      <p className="text-xs text-muted-foreground">
                        until {new Date(activeSchedule.schedule.end_time).toLocaleTimeString([], {
                          hour: 'numeric',
                          minute: '2-digit',
                          hour12: true,
                        })}
                      </p>
                    )}
                  </div>
                </div>
              )}
              {(() => {
                // Filter out ALL occurrences of the active schedule
                // since it's already shown in the "Now Active" badge above
                const activeId = activeSchedule?.active ? activeSchedule.schedule?.schedule_id : null
                const filtered = (schedules ?? []).filter((s) => {
                  if (activeId && s.schedule_id === activeId) return false
                  return true
                })
                return filtered.length > 0 ? (
                  <div className="space-y-3">
                    {filtered.slice(0, 4).map((schedule, idx) => (
                      <div
                        key={`${schedule.schedule_id}-${idx}`}
                        className="flex items-center justify-between rounded-lg border border-border/40 p-2 dark:bg-[rgba(2,6,23,0.35)] dark:border-[rgba(148,163,184,0.15)]"
                      >
                        <div>
                          <p className="text-sm font-bold">{schedule.schedule_name}</p>
                          <p className="text-xs text-muted-foreground">
                            {schedule.zone_names?.length
                              ? schedule.zone_names.join(', ')
                              : 'All zones'}
                          </p>
                        </div>
                        <div className="text-right">
                          <p className="text-sm font-bold">{formatTemperature(schedule.target_temp_c, unitKey)}</p>
                          <p className="flex items-center gap-1 text-xs text-muted-foreground">
                            <Clock className="h-3 w-3" />
                            {new Date(schedule.start_time).toLocaleTimeString([], {
                              hour: 'numeric',
                              minute: '2-digit',
                              hour12: true,
                            })}
                          </p>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">No upcoming schedules</p>
                )
              })()}
            </CardContent>
          </Card>

          {/* Quick Actions */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-bold">Quick Actions</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-2">
              <Button
                variant="outline"
                size="sm"
                className="justify-start"
                onClick={() => handleQuickAction('eco')}
              >
                <Thermometer className="mr-2 h-4 w-4" />
                Eco Mode
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="justify-start"
                onClick={() => handleQuickAction('away')}
              >
                <Activity className="mr-2 h-4 w-4" />
                Away Mode
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="justify-start"
                onClick={() => handleQuickAction('boost_heat')}
              >
                <Gauge className="mr-2 h-4 w-4" />
                Boost Heat
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="justify-start"
                onClick={() => handleQuickAction('boost_cool')}
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
