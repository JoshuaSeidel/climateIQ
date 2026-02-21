import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { useSettingsStore } from '@/stores/settingsStore'
import { formatTemperature, toDisplayTemp, tempUnitLabel } from '@/lib/utils'
import type {
  ZoneBackend,
  EnergyResponse,
  ComfortResponse,
  OverviewResponse,
} from '@/types'
import {
  BarChart3,
  Thermometer,
  Zap,
  Heart,
  Clock,
  Loader2,
  Users,
  DollarSign,
  Activity,
} from 'lucide-react'
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  Cell,
} from 'recharts'

type AnalyticsTab = 'temperature' | 'occupancy' | 'energy' | 'comfort' | 'decisions'

const TABS: { id: AnalyticsTab; label: string; icon: React.ElementType }[] = [
  { id: 'temperature', label: 'Temperature', icon: Thermometer },
  { id: 'occupancy', label: 'Occupancy', icon: Users },
  { id: 'energy', label: 'Energy', icon: Zap },
  { id: 'comfort', label: 'Comfort', icon: Heart },
  { id: 'decisions', label: 'Decisions', icon: Clock },
]

const HOURS_OPTIONS = [
  { value: 6, label: '6h' },
  { value: 12, label: '12h' },
  { value: 24, label: '24h' },
  { value: 48, label: '48h' },
  { value: 168, label: '7d' },
]

const COLORS = [
  'hsl(25, 95%, 53%)',
  'hsl(210, 100%, 60%)',
  'hsl(142, 76%, 36%)',
  'hsl(280, 65%, 60%)',
  'hsl(45, 93%, 47%)',
  'hsl(0, 84%, 60%)',
  'hsl(180, 70%, 45%)',
  'hsl(330, 80%, 55%)',
  'hsl(200, 80%, 50%)',
  'hsl(60, 70%, 45%)',
  'hsl(310, 60%, 50%)',
  'hsl(160, 65%, 40%)',
  'hsl(20, 80%, 45%)',
  'hsl(240, 60%, 55%)',
  'hsl(100, 60%, 40%)',
  'hsl(350, 70%, 50%)',
]

function getZoneColor(index: number): string {
  return COLORS[index % COLORS.length]
}

export const Analytics = () => {
  const { temperatureUnit } = useSettingsStore()
  const unitKey: 'c' | 'f' = temperatureUnit === 'celsius' ? 'c' : 'f'
  const [activeTab, setActiveTab] = useState<AnalyticsTab>('temperature')
  const [hours, setHours] = useState(24)
  const [selectedZoneIds, setSelectedZoneIds] = useState<Set<string>>(new Set(['all']))

  // Fetch zones
  const { data: zones } = useQuery<ZoneBackend[]>({
    queryKey: ['zones-raw'],
    queryFn: () => api.get<ZoneBackend[]>('/zones'),
  })

  const isAllZones = selectedZoneIds.has('all')
  // For single-zone views (exactly one zone selected), use that zone's ID
  const selectedArray = Array.from(selectedZoneIds).filter((id) => id !== 'all')

  const toggleZone = (zoneId: string) => {
    setSelectedZoneIds((prev) => {
      const next = new Set(prev)
      // If clicking "all", reset to all
      if (zoneId === 'all') {
        return new Set(['all'])
      }
      // Remove "all" when selecting specific zones
      next.delete('all')
      // Toggle the zone
      if (next.has(zoneId)) {
        next.delete(zoneId)
      } else {
        next.add(zoneId)
      }
      // If nothing selected, go back to "all"
      if (next.size === 0) {
        return new Set(['all'])
      }
      // If all zones are now individually selected, switch to "all"
      if (zones && next.size === zones.length) {
        return new Set(['all'])
      }
      return next
    })
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Analytics</p>
          <h2 className="flex items-center gap-2 text-2xl font-black tracking-tight">
            <BarChart3 className="h-6 w-6 text-primary dark:drop-shadow-[0_0_6px_rgba(56,189,248,0.4)]" />
            Climate Analytics
          </h2>
        </div>
        <div className="flex items-center gap-2">
          {/* Time range selector */}
          <div className="flex rounded-2xl border border-border/60 dark:border-[rgba(148,163,184,0.15)] dark:bg-[rgba(2,6,23,0.35)] p-0.5">
            {HOURS_OPTIONS.map((opt) => (
              <Button
                key={opt.value}
                variant={hours === opt.value ? 'default' : 'ghost'}
                size="sm"
                className="px-2 text-xs sm:px-3 sm:text-sm"
                onClick={() => setHours(opt.value)}
              >
                {opt.label}
              </Button>
            ))}
          </div>
        </div>
      </div>

      {/* Tab Navigation */}
      <div className="flex flex-wrap gap-1 rounded-2xl border border-border/60 dark:border-[rgba(148,163,184,0.15)] dark:bg-[rgba(2,6,23,0.35)] p-1">
        {TABS.map((tab) => (
          <Button
            key={tab.id}
            variant={activeTab === tab.id ? 'default' : 'ghost'}
            size="sm"
            className="gap-2"
            onClick={() => setActiveTab(tab.id)}
          >
            <tab.icon className="h-4 w-4" />
            <span className="hidden sm:inline">{tab.label}</span>
          </Button>
        ))}
      </div>

      {/* Zone selector for temperature and occupancy tabs */}
      {(activeTab === 'temperature' || activeTab === 'occupancy') && zones && zones.length > 0 && (
        <div className="flex flex-wrap gap-2">
          <Button
            variant={isAllZones ? 'default' : 'outline'}
            size="sm"
            onClick={() => toggleZone('all')}
          >
            All Zones
          </Button>
          {zones.map((zone) => (
            <Button
              key={zone.id}
              variant={!isAllZones && selectedZoneIds.has(zone.id) ? 'default' : 'outline'}
              size="sm"
              onClick={() => toggleZone(zone.id)}
            >
              {zone.name}
            </Button>
          ))}
          {!isAllZones && selectedArray.length > 1 && (
            <span className="flex items-center text-xs text-muted-foreground">
              {selectedArray.length} zones selected
            </span>
          )}
        </div>
      )}

      {/* Tab Content */}
      {activeTab === 'temperature' && (
        <TemperatureTab
          selectedZoneIds={selectedArray}
          isAllZones={isAllZones}
          hours={hours}
          zones={zones}
          unitKey={unitKey}
        />
      )}
      {activeTab === 'occupancy' && (
        <OccupancyTab
          selectedZoneIds={selectedArray}
          isAllZones={isAllZones}
          hours={hours}
          zones={zones}
        />
      )}
      {activeTab === 'energy' && <EnergyTab hours={hours} />}
      {activeTab === 'comfort' && <ComfortTab hours={hours} unitKey={unitKey} />}
      {activeTab === 'decisions' && <DecisionsTab hours={hours} />}
    </div>
  )
}

// ============================================================================
// Temperature History Tab
// ============================================================================
function TemperatureTab({
  selectedZoneIds,
  isAllZones,
  hours,
  zones,
  unitKey,
}: {
  selectedZoneIds: string[]
  isAllZones: boolean
  hours: number
  zones?: ZoneBackend[]
  unitKey: 'c' | 'f'
}) {
  const [metricView, setMetricView] = useState<'temperature' | 'humidity'>('temperature')
  const isSingleZone = !isAllZones && selectedZoneIds.length === 1
  const singleZoneId = isSingleZone ? selectedZoneIds[0] : null

  // Always use the overview endpoint — it handles all-zones, multi-zone,
  // and single-zone consistently (same aggregate view selection).
  // Previously single-zone used a separate /history endpoint that picked a
  // different aggregate view and could return empty results.
  const { data: overview, isLoading } = useQuery<OverviewResponse>({
    queryKey: ['analytics-overview', hours, isAllZones ? 'all' : selectedZoneIds.join(',')],
    queryFn: () =>
      api.get<OverviewResponse>('/analytics/overview', {
        hours,
        ...(!isAllZones && selectedZoneIds.length > 0 ? { zone_ids: selectedZoneIds } : {}),
      }),
  })

  // Chart data: merge all zones' readings into a unified timeline
  const multiZoneChartData = useMemo(() => {
    if (!overview?.zones?.length) return []

    // Collect all unique timestamps across all zones
    const timeMap = new Map<string, Record<string, number | string | null>>()

    for (const zone of overview.zones) {
      for (const reading of zone.readings) {
        const timeKey = reading.recorded_at
        if (!timeMap.has(timeKey)) {
          timeMap.set(timeKey, {
            time: new Date(reading.recorded_at).toLocaleTimeString([], {
              hour: '2-digit',
              minute: '2-digit',
              ...(hours > 24 ? { month: 'short', day: 'numeric' } : {}),
            }),
            _ts: reading.recorded_at,
          })
        }
        const point = timeMap.get(timeKey)!
        const zoneKey = `zone_${zone.zone_id}`
        if (metricView === 'temperature') {
          const val = reading.temperature_c != null ? toDisplayTemp(reading.temperature_c, unitKey) : null
          // Don't overwrite a real value with null (can happen with
          // separate temp/humidity sensors producing multiple rows)
          if (val != null || point[zoneKey] == null) {
            point[zoneKey] = val
          }
        } else {
          const val = reading.humidity ?? null
          if (val != null || point[zoneKey] == null) {
            point[zoneKey] = val
          }
        }
      }
    }

    // Sort by timestamp
    const sorted = Array.from(timeMap.values()).sort((a, b) =>
      String(a._ts).localeCompare(String(b._ts))
    )

    // Remove the _ts helper key
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    return sorted.map(({ _ts, ...rest }) => rest)
  }, [overview, hours, unitKey, metricView])

  // Aggregate stats for all zones
  const overviewStats = useMemo(() => {
    if (!overview?.zones?.length) return null

    const temps: number[] = []
    const mins: number[] = []
    const maxes: number[] = []
    const humidities: number[] = []
    let totalReadings = 0

    for (const zone of overview.zones) {
      if (zone.avg_temperature_c != null) temps.push(zone.avg_temperature_c)
      if (zone.min_temperature_c != null) mins.push(zone.min_temperature_c)
      if (zone.max_temperature_c != null) maxes.push(zone.max_temperature_c)
      if (zone.avg_humidity != null) humidities.push(zone.avg_humidity)
      totalReadings += zone.total_readings
    }

    return {
      avgTemp: temps.length > 0 ? temps.reduce((a, b) => a + b, 0) / temps.length : null,
      minTemp: mins.length > 0 ? Math.min(...mins) : null,
      maxTemp: maxes.length > 0 ? Math.max(...maxes) : null,
      avgHumidity:
        humidities.length > 0
          ? humidities.reduce((a, b) => a + b, 0) / humidities.length
          : null,
      totalReadings,
    }
  }, [overview])

  const zoneName = isSingleZone ? (zones?.find((z) => z.id === singleZoneId)?.name ?? 'Zone') : 'Selected Zones'

  // Build zone list for multi-line chart
  const overviewZones = overview?.zones ?? []

  return (
    <div className="space-y-6">
      {/* Summary Stats */}
      {overviewStats && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Card>
            <CardContent className="p-4">
                <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Avg Temperature</p>
              <p className="text-2xl font-black">
                {overviewStats.avgTemp != null
                  ? formatTemperature(overviewStats.avgTemp, unitKey)
                  : '--'}
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
                <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Min / Max</p>
              <p className="text-2xl font-black">
                {overviewStats.minTemp != null && overviewStats.maxTemp != null
                  ? `${toDisplayTemp(overviewStats.minTemp, unitKey).toFixed(1)} / ${toDisplayTemp(overviewStats.maxTemp, unitKey).toFixed(1)}${tempUnitLabel(unitKey)}`
                  : '--'}
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
                <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Avg Humidity</p>
              <p className="text-2xl font-black">
                {overviewStats.avgHumidity != null
                  ? `${overviewStats.avgHumidity.toFixed(0)}%`
                  : '--'}
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
                <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Total Readings</p>
              <p className="text-2xl font-black">{overviewStats.totalReadings}</p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Chart */}
      <Card>
        <CardHeader>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <CardTitle>
                {isAllZones
                  ? `All Zones`
                  : isSingleZone
                    ? zoneName
                    : `${selectedZoneIds.length} Zones`}{' '}
                - {metricView === 'temperature' ? 'Temperature' : 'Humidity'} ({hours}h)
              </CardTitle>
              <CardDescription>
                {metricView === 'temperature'
                  ? `Temperature shown in ${tempUnitLabel(unitKey)}`
                  : 'Humidity shown in %'}
              </CardDescription>
            </div>
            <div className="flex rounded-2xl border border-border/60 dark:border-[rgba(148,163,184,0.15)] dark:bg-[rgba(2,6,23,0.35)] p-0.5">
                <Button
                  variant={metricView === 'temperature' ? 'default' : 'ghost'}
                  size="sm"
                  className="px-3 text-xs"
                  onClick={() => setMetricView('temperature')}
                >
                  Temperature
                </Button>
                <Button
                  variant={metricView === 'humidity' ? 'default' : 'ghost'}
                  size="sm"
                  className="px-3 text-xs"
                  onClick={() => setMetricView('humidity')}
                >
                  Humidity
                </Button>
              </div>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex h-80 items-center justify-center">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          ) : multiZoneChartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={350}>
              <LineChart data={multiZoneChartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                <XAxis
                  dataKey="time"
                  tick={{ fontSize: 10 }}
                  stroke="hsl(var(--muted-foreground))"
                  interval="preserveStartEnd"
                />
                <YAxis
                  tick={{ fontSize: 11 }}
                  stroke="hsl(var(--muted-foreground))"
                  domain={metricView === 'humidity' ? [0, 100] : ['auto', 'auto']}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: 'var(--glass-bg, hsl(var(--card)))',
                    border: '1px solid var(--glass-border, hsl(var(--border)))',
                    borderRadius: '12px',
                    backdropFilter: 'blur(12px)',
                  }}
                  formatter={(value: number | undefined, name: string | undefined) => {
                    const zoneIdFromKey = (name ?? '').replace('zone_', '')
                    const zone = overviewZones.find((z) => z.zone_id === zoneIdFromKey)
                    const label = zone?.zone_name ?? (name ?? '')
                    const suffix = metricView === 'temperature' ? tempUnitLabel(unitKey) : '%'
                    return [
                      `${typeof value === 'number' ? value.toFixed(1) : '--'}${suffix}`,
                      label,
                    ]
                  }}
                />
                <Legend
                  formatter={(value: string) => {
                    const zoneIdFromKey = value.replace('zone_', '')
                    const zone = overviewZones.find((z) => z.zone_id === zoneIdFromKey)
                    return zone?.zone_name ?? value
                  }}
                />
                {overviewZones.map((zone, idx) => (
                  <Line
                    key={zone.zone_id}
                    type="monotone"
                    dataKey={`zone_${zone.zone_id}`}
                    stroke={getZoneColor(idx)}
                    strokeWidth={2}
                    dot={false}
                    connectNulls
                    name={`zone_${zone.zone_id}`}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex h-80 items-center justify-center text-muted-foreground">
              No data available for this time period
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

// ============================================================================
// Occupancy Heatmap Tab
// ============================================================================
function OccupancyTab({
  selectedZoneIds,
  isAllZones,
  hours,
}: {
  selectedZoneIds: string[]
  isAllZones: boolean
  hours: number
  zones?: ZoneBackend[]
}) {
  const isSingleZone = !isAllZones && selectedZoneIds.length === 1
  const singleZoneId = isSingleZone ? selectedZoneIds[0] : null

  // Always use the overview endpoint for consistency (same aggregate view
  // selection regardless of how many zones are selected).
  const { data: overview, isLoading } = useQuery<OverviewResponse>({
    queryKey: ['analytics-overview-occ', hours, isAllZones ? 'all' : selectedZoneIds.join(',')],
    queryFn: () =>
      api.get<OverviewResponse>('/analytics/overview', {
        hours,
        ...(!isAllZones && selectedZoneIds.length > 0 ? { zone_ids: selectedZoneIds } : {}),
      }),
  })

  // For single-zone views, extract readings from the first (only) zone in overview
  const singleZoneReadings = useMemo(() => {
    if (!isSingleZone || !overview?.zones?.length) return []
    const zone = overview.zones.find((z) => z.zone_id === singleZoneId)
    return zone?.readings ?? []
  }, [overview, isSingleZone, singleZoneId])

  // Build heatmap data: 7 days x 24 hours (single zone)
  const heatmapData = useMemo(() => {
    if (!singleZoneReadings.length) return []

    const dayNames = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    const grid: { day: string; hour: number; value: number; count: number }[] = []

    for (let d = 0; d < 7; d++) {
      for (let h = 0; h < 24; h++) {
        grid.push({ day: dayNames[d], hour: h, value: 0, count: 0 })
      }
    }

    for (const reading of singleZoneReadings) {
      const date = new Date(reading.recorded_at)
      const dayIdx = (date.getDay() + 6) % 7 // Monday = 0
      const hourIdx = date.getHours()
      const idx = dayIdx * 24 + hourIdx
      if (idx < grid.length) {
        grid[idx].value += reading.presence ? 1 : 0
        grid[idx].count++
      }
    }

    return grid
  }, [singleZoneReadings])

  // Aggregate by hour for bar chart (single zone)
  const hourlyData = useMemo(() => {
    if (!singleZoneReadings.length) return []
    const hourBuckets: { hour: string; occupied: number; total: number }[] = []
    for (let h = 0; h < 24; h++) {
      hourBuckets.push({ hour: `${h.toString().padStart(2, '0')}:00`, occupied: 0, total: 0 })
    }
    for (const reading of singleZoneReadings) {
      const hour = new Date(reading.recorded_at).getHours()
      hourBuckets[hour].total++
      if (reading.presence) hourBuckets[hour].occupied++
    }
    return hourBuckets.map((b) => ({
      ...b,
      rate: b.total > 0 ? Math.round((b.occupied / b.total) * 100) : 0,
    }))
  }, [singleZoneReadings])

  // Grouped bar chart data for all zones
  const overviewZones = overview?.zones ?? []

  const groupedBarData = useMemo(() => {
    if (!overview?.zones?.length) return []

    // Build hour buckets with per-zone occupancy rates
    const hourBuckets: Record<string, number | string>[] = []
    for (let h = 0; h < 24; h++) {
      hourBuckets.push({
        hour: `${h.toString().padStart(2, '0')}:00`,
      })
    }

    for (const zone of overview.zones) {
      // Count occupied and total per hour for this zone
      const occupied = new Array(24).fill(0)
      const total = new Array(24).fill(0)

      for (const reading of zone.readings) {
        const hour = new Date(reading.recorded_at).getHours()
        total[hour]++
        if (reading.presence) occupied[hour]++
      }

      for (let h = 0; h < 24; h++) {
        hourBuckets[h][`zone_${zone.zone_id}`] =
          total[h] > 0 ? Math.round((occupied[h] / total[h]) * 100) : 0
      }
    }

    return hourBuckets
  }, [overview])

  return (
    <div className="space-y-6">
      {/* All/Multi Zones - Grouped Bar Chart */}
      {!isSingleZone && (
        <Card>
          <CardHeader>
            <CardTitle>Occupancy by Hour - All Zones</CardTitle>
            <CardDescription>
              Occupancy rate per zone for each hour of the day ({hours}h)
            </CardDescription>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="flex h-64 items-center justify-center">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
              </div>
            ) : groupedBarData.length > 0 && overviewZones.length > 0 ? (
              <ResponsiveContainer width="100%" height={350}>
                <BarChart data={groupedBarData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                  <XAxis
                    dataKey="hour"
                    tick={{ fontSize: 10 }}
                    stroke="hsl(var(--muted-foreground))"
                  />
                  <YAxis
                    tick={{ fontSize: 11 }}
                    stroke="hsl(var(--muted-foreground))"
                    domain={[0, 100]}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: 'var(--glass-bg, hsl(var(--card)))',
                      border: '1px solid var(--glass-border, hsl(var(--border)))',
                      borderRadius: '12px',
                      backdropFilter: 'blur(12px)',
                    }}
                    formatter={(value: number | undefined, name: string | undefined) => {
                      const zoneIdFromKey = (name ?? '').replace('zone_', '')
                      const zone = overviewZones.find((z) => z.zone_id === zoneIdFromKey)
                      return [`${value ?? 0}%`, zone?.zone_name ?? (name ?? '')]
                    }}
                  />
                  <Legend
                    formatter={(value: string) => {
                      const zoneIdFromKey = value.replace('zone_', '')
                      const zone = overviewZones.find((z) => z.zone_id === zoneIdFromKey)
                      return zone?.zone_name ?? value
                    }}
                  />
                  {overviewZones.map((zone, idx) => (
                    <Bar
                      key={zone.zone_id}
                      dataKey={`zone_${zone.zone_id}`}
                      name={`zone_${zone.zone_id}`}
                      fill={getZoneColor(idx)}
                      radius={[2, 2, 0, 0]}
                    />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-64 items-center justify-center text-muted-foreground">
                No occupancy data available for this period
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Single Zone - Occupancy by Hour Bar Chart */}
      {isSingleZone && (
        <Card>
          <CardHeader>
            <CardTitle>Occupancy by Hour</CardTitle>
            <CardDescription>
              Percentage of time the zone was occupied during each hour
            </CardDescription>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="flex h-64 items-center justify-center">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
              </div>
            ) : hourlyData.length > 0 ? (
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={hourlyData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                  <XAxis
                    dataKey="hour"
                    tick={{ fontSize: 10 }}
                    stroke="hsl(var(--muted-foreground))"
                  />
                  <YAxis
                    tick={{ fontSize: 11 }}
                    stroke="hsl(var(--muted-foreground))"
                    domain={[0, 100]}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: 'var(--glass-bg, hsl(var(--card)))',
                      border: '1px solid var(--glass-border, hsl(var(--border)))',
                      borderRadius: '12px',
                      backdropFilter: 'blur(12px)',
                    }}
                    formatter={(value) => [`${value}%`, 'Occupancy Rate']}
                  />
                  <Bar dataKey="rate" name="Occupancy %" radius={[4, 4, 0, 0]}>
                    {hourlyData.map((_, index) => (
                      <Cell
                        key={`cell-${index}`}
                        fill={
                          hourlyData[index].rate > 70
                            ? COLORS[2]
                            : hourlyData[index].rate > 30
                              ? COLORS[4]
                              : 'hsl(var(--muted))'
                        }
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-64 items-center justify-center text-muted-foreground">
                {singleZoneId ? 'No occupancy data available' : 'Select a zone to view data'}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Single Zone - Heatmap Grid */}
      {isSingleZone && heatmapData.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Weekly Occupancy Heatmap</CardTitle>
            <CardDescription>Darker cells indicate higher occupancy</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <div className="min-w-[600px]">
                {/* Hour labels */}
                <div className="mb-1 flex">
                  <div className="w-10" />
                  {Array.from({ length: 24 }, (_, i) => (
                    <div key={i} className="flex-1 text-center text-[10px] text-muted-foreground">
                      {i % 3 === 0 ? `${i}` : ''}
                    </div>
                  ))}
                </div>
                {/* Grid rows */}
                {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((day, dayIdx) => (
                  <div key={day} className="flex items-center gap-0.5">
                    <div className="w-10 text-xs text-muted-foreground">{day}</div>
                    {Array.from({ length: 24 }, (_, hourIdx) => {
                      const cell = heatmapData[dayIdx * 24 + hourIdx]
                      const intensity = cell?.count > 0 ? cell.value / cell.count : 0
                      return (
                        <div
                          key={hourIdx}
                          className="h-6 flex-1 rounded-sm"
                          style={{
                            backgroundColor:
                              intensity > 0.7
                                ? 'hsl(142, 76%, 36%)'
                                : intensity > 0.3
                                  ? 'hsl(142, 76%, 56%)'
                                  : intensity > 0
                                    ? 'hsl(142, 40%, 80%)'
                                    : 'hsl(var(--muted))',
                            opacity: cell?.count > 0 ? 1 : 0.3,
                          }}
                          title={`${day} ${hourIdx}:00 - ${Math.round(intensity * 100)}% occupied`}
                        />
                      )
                    })}
                  </div>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

// ============================================================================
// Energy Tab
// ============================================================================
function EnergyTab({ hours }: { hours: number }) {
  const { data: energy, isLoading } = useQuery<EnergyResponse>({
    queryKey: ['energy', hours],
    queryFn: () => api.get<EnergyResponse>('/analytics/energy', { hours }),
  })

  const chartData = useMemo(() => {
    if (!energy?.zones?.length) return []
    return energy.zones.map((z) => ({
      name: z.zone_name,
      kwh: z.estimated_kwh,
      cost: z.estimated_cost_usd,
      actions: z.action_count,
      devices: z.device_count,
    }))
  }, [energy])

  return (
    <div className="space-y-6">
      {/* Summary */}
      {energy && (
        <div className="grid gap-4 sm:grid-cols-3">
          <Card>
            <CardContent className="flex items-center justify-between p-4">
              <div>
                <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Total Energy</p>
                <p className="text-2xl font-black">{energy.total_estimated_kwh.toFixed(1)} kWh</p>
              </div>
              <Zap className="h-8 w-8 text-yellow-500" />
            </CardContent>
          </Card>
          <Card>
            <CardContent className="flex items-center justify-between p-4">
              <div>
                <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Estimated Cost</p>
                <p className="text-2xl font-black">
                  ${energy.total_estimated_cost_usd.toFixed(2)}
                </p>
              </div>
              <DollarSign className="h-8 w-8 text-green-500" />
            </CardContent>
          </Card>
          <Card>
            <CardContent className="flex items-center justify-between p-4">
              <div>
                <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Rate</p>
                <p className="text-2xl font-black">${energy.cost_per_kwh}/kWh</p>
              </div>
              <Activity className="h-8 w-8 text-primary" />
            </CardContent>
          </Card>
        </div>
      )}

      {/* Energy by Zone Chart */}
      <Card>
        <CardHeader>
          <CardTitle>Energy Usage by Zone</CardTitle>
          <CardDescription>Estimated energy consumption per zone ({hours}h)</CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex h-64 items-center justify-center">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          ) : chartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                <XAxis dataKey="name" tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
                <YAxis tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
                <Tooltip
                  contentStyle={{
                    backgroundColor: 'var(--glass-bg, hsl(var(--card)))',
                    border: '1px solid var(--glass-border, hsl(var(--border)))',
                    borderRadius: '12px',
                    backdropFilter: 'blur(12px)',
                  }}
                />
                <Legend />
                <Bar dataKey="kwh" name="Energy (kWh)" fill={COLORS[4]} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex h-64 items-center justify-center text-muted-foreground">
              No energy data available for this period
            </div>
          )}
        </CardContent>
      </Card>

      {/* Zone Details Table */}
      {energy?.zones && energy.zones.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Zone Breakdown</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border/60">
                    <th className="pb-2 text-left font-bold">Zone</th>
                    <th className="pb-2 text-right font-bold">Devices</th>
                    <th className="pb-2 text-right font-bold">Actions</th>
                    <th className="pb-2 text-right font-bold">Energy (kWh)</th>
                    <th className="pb-2 text-right font-bold">Cost</th>
                    <th className="pb-2 text-right font-bold">Primary Device</th>
                  </tr>
                </thead>
                <tbody>
                  {energy.zones.map((zone) => (
                    <tr key={zone.zone_id} className="border-b border-border/30">
                      <td className="py-2 font-medium">{zone.zone_name}</td>
                      <td className="py-2 text-right">{zone.device_count}</td>
                      <td className="py-2 text-right">{zone.action_count}</td>
                      <td className="py-2 text-right">{zone.estimated_kwh.toFixed(2)}</td>
                      <td className="py-2 text-right">${zone.estimated_cost_usd.toFixed(3)}</td>
                      <td className="py-2 text-right capitalize">
                        {zone.primary_device_type?.replace('_', ' ') ?? '-'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

// ============================================================================
// Comfort Score Tab
// ============================================================================
function ComfortTab({ hours, unitKey }: { hours: number; unitKey: 'c' | 'f' }) {
  const { data: comfort, isLoading } = useQuery<ComfortResponse>({
    queryKey: ['comfort', hours],
    queryFn: () => api.get<ComfortResponse>('/analytics/comfort', { hours }),
  })

  const radarData = useMemo(() => {
    if (!comfort?.zones?.length) return []
    return comfort.zones.map((z) => ({
      zone: z.zone_name,
      score: z.score,
      temperature: z.temp_in_range_pct,
      humidity: z.humidity_in_range_pct,
    }))
  }, [comfort])

  return (
    <div className="space-y-6">
      {/* Overall Score */}
      {comfort && (
        <Card>
          <CardContent className="flex items-center gap-6 p-6">
            <div
              className="flex h-24 w-24 items-center justify-center rounded-full border-4 dark:shadow-[0_0_20px_rgba(74,222,128,0.2)]"
              style={{
                borderColor:
                  comfort.overall_score >= 80
                    ? 'hsl(142, 76%, 36%)'
                    : comfort.overall_score >= 50
                      ? 'hsl(45, 93%, 47%)'
                      : 'hsl(0, 84%, 60%)',
              }}
            >
              <span className="text-3xl font-bold">{comfort.overall_score.toFixed(0)}</span>
            </div>
            <div>
              <h3 className="text-xl font-semibold">Overall Comfort Score</h3>
              <p className="text-sm text-muted-foreground">
                Based on temperature and humidity readings over the last {hours} hours
              </p>
              <p className="mt-1 text-sm">
                {comfort.overall_score >= 80
                  ? 'Excellent comfort levels across your home'
                  : comfort.overall_score >= 50
                    ? 'Moderate comfort - some zones may need adjustment'
                    : 'Low comfort - consider adjusting your settings'}
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Zone Scores */}
      {comfort?.zones && comfort.zones.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {comfort.zones.map((zone) => (
            <Card key={zone.zone_id}>
              <CardContent className="p-4">
                <div className="flex items-center justify-between">
                  <h4 className="font-medium">{zone.zone_name}</h4>
                  <span
                    className="rounded-full px-2 py-0.5 text-sm font-semibold"
                    style={{
                      backgroundColor:
                        zone.score >= 80
                          ? 'hsl(142, 76%, 36%, 0.1)'
                          : zone.score >= 50
                            ? 'hsl(45, 93%, 47%, 0.1)'
                            : 'hsl(0, 84%, 60%, 0.1)',
                      color:
                        zone.score >= 80
                          ? 'hsl(142, 76%, 36%)'
                          : zone.score >= 50
                            ? 'hsl(45, 93%, 47%)'
                            : 'hsl(0, 84%, 60%)',
                    }}
                  >
                    {zone.score.toFixed(0)}
                  </span>
                </div>
                <div className="mt-3 space-y-2">
                  <div>
                    <div className="flex justify-between text-xs text-muted-foreground">
                      <span>Temperature in range</span>
                      <span>{zone.temp_in_range_pct.toFixed(0)}%</span>
                    </div>
                    <div className="mt-1 h-2 rounded-full bg-muted dark:bg-[rgba(2,6,23,0.38)]">
                      <div
                        className="h-2 rounded-full bg-orange-500"
                        style={{ width: `${Math.min(100, zone.temp_in_range_pct)}%` }}
                      />
                    </div>
                  </div>
                  <div>
                    <div className="flex justify-between text-xs text-muted-foreground">
                      <span>Humidity in range</span>
                      <span>{zone.humidity_in_range_pct.toFixed(0)}%</span>
                    </div>
                    <div className="mt-1 h-2 rounded-full bg-muted dark:bg-[rgba(2,6,23,0.38)]">
                      <div
                        className="h-2 rounded-full bg-blue-500"
                        style={{ width: `${Math.min(100, zone.humidity_in_range_pct)}%` }}
                      />
                    </div>
                  </div>
                  <div className="flex justify-between text-xs text-muted-foreground">
                    <span>
                      Avg: {zone.avg_temperature_c != null ? formatTemperature(zone.avg_temperature_c, unitKey) : '--'} /{' '}
                      {zone.avg_humidity?.toFixed(0) ?? '--'}%
                    </span>
                    <span>{zone.reading_count} readings</span>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Radar Chart */}
      {radarData.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Comfort Comparison</CardTitle>
            <CardDescription>Zone comfort scores compared</CardDescription>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={350}>
              <RadarChart data={radarData}>
                <PolarGrid stroke="hsl(var(--border))" />
                <PolarAngleAxis dataKey="zone" tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
                <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fontSize: 10 }} />
                <Radar
                  name="Overall Score"
                  dataKey="score"
                  stroke={COLORS[2]}
                  fill={COLORS[2]}
                  fillOpacity={0.3}
                />
                <Radar
                  name="Temp in Range %"
                  dataKey="temperature"
                  stroke={COLORS[0]}
                  fill={COLORS[0]}
                  fillOpacity={0.1}
                />
                <Legend />
              </RadarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      )}

      {isLoading && (
        <div className="flex h-64 items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      )}
    </div>
  )
}

// ============================================================================
// Decision Log Tab
// ============================================================================
interface DeviceInfo {
  id: string
  name: string
  zone_id: string
  type: string
}

function DecisionsTab({ hours }: { hours: number }) {
  const { data: devices, isLoading: devicesLoading } = useQuery<DeviceInfo[]>({
    queryKey: ['devices'],
    queryFn: () => api.get<DeviceInfo[]>('/devices'),
  })

  const { data: zones } = useQuery<ZoneBackend[]>({
    queryKey: ['zones-raw'],
    queryFn: () => api.get<ZoneBackend[]>('/zones'),
  })

  // Fetch energy data which includes action counts per zone (uses DeviceAction table)
  const { data: energy, isLoading: energyLoading } = useQuery<EnergyResponse>({
    queryKey: ['energy-decisions', hours],
    queryFn: () => api.get<EnergyResponse>('/analytics/energy', { hours }),
  })

  const hasActions = energy?.zones?.some((z) => z.action_count > 0) ?? false
  const isLoading = devicesLoading || energyLoading

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Decision Log</CardTitle>
          <CardDescription>
            HVAC decisions and actions taken by the system
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex h-64 items-center justify-center">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          ) : hasActions ? (
            <div className="space-y-4">
              {/* Action summary per zone from energy analytics */}
              <h4 className="text-sm font-medium">Actions by Zone ({hours}h)</h4>
              <div className="space-y-2">
                {energy!.zones
                  .filter((z) => z.action_count > 0)
                  .map((zone) => (
                    <div
                      key={zone.zone_id}
                      className="flex items-center justify-between rounded-lg border border-border/40 dark:bg-[rgba(2,6,23,0.35)] dark:border-[rgba(148,163,184,0.15)] p-3"
                    >
                      <div className="flex items-center gap-3">
                        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/10">
                          <Activity className="h-4 w-4 text-primary" />
                        </div>
                        <div>
                          <p className="text-sm font-medium">{zone.zone_name}</p>
                          <p className="text-xs text-muted-foreground capitalize">
                            {zone.primary_device_type?.replace('_', ' ') ?? 'Unknown device'}
                          </p>
                        </div>
                      </div>
                      <div className="text-right">
                        <p className="text-sm font-medium">{zone.action_count} actions</p>
                        <p className="text-xs text-muted-foreground">
                          {zone.estimated_kwh.toFixed(2)} kWh
                        </p>
                      </div>
                    </div>
                  ))}
              </div>

              {/* Device list */}
              {devices && devices.length > 0 && (
                <div className="mt-4">
                  <h4 className="mb-2 text-sm font-medium">Registered Devices</h4>
                  <div className="space-y-2">
                    {devices.map((device) => (
                      <div
                        key={device.id}
                        className="flex items-center justify-between rounded-lg border border-border/30 p-2 text-sm"
                      >
                        <div className="flex items-center gap-2">
                          <Clock className="h-4 w-4 text-muted-foreground" />
                          <span className="font-medium">{device.name}</span>
                          <span className="text-xs text-muted-foreground capitalize">
                            ({device.type.replace('_', ' ')})
                          </span>
                        </div>
                        <span className="text-xs text-muted-foreground">
                          {zones?.find((z) => z.id === device.zone_id)?.name ?? 'Unknown zone'}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="flex h-48 flex-col items-center justify-center gap-3 text-center">
              <Clock className="h-10 w-10 text-muted-foreground/50" />
              <div>
                <p className="text-sm font-medium text-muted-foreground">
                  No decision history available yet
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Decision history will appear after the system has been running and making
                  HVAC control decisions. Try switching to &quot;Active&quot; or &quot;Scheduled&quot; mode.
                </p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
