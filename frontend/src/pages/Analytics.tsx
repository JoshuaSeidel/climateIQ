import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import type {
  ZoneBackend,
  HistoryResponse,
  EnergyResponse,
  ComfortResponse,
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
]

export const Analytics = () => {
  const [activeTab, setActiveTab] = useState<AnalyticsTab>('temperature')
  const [hours, setHours] = useState(24)
  const [selectedZoneId, setSelectedZoneId] = useState<string | null>(null)

  // Fetch zones
  const { data: zones } = useQuery<ZoneBackend[]>({
    queryKey: ['zones-raw'],
    queryFn: () => api.get<ZoneBackend[]>('/zones'),
  })

  // Auto-select first zone
  const effectiveZoneId = selectedZoneId ?? zones?.[0]?.id ?? null

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs uppercase tracking-widest text-muted-foreground">Analytics</p>
          <h2 className="flex items-center gap-2 text-2xl font-semibold">
            <BarChart3 className="h-6 w-6 text-primary" />
            Climate Analytics
          </h2>
        </div>
        <div className="flex items-center gap-2">
          {/* Time range selector */}
          <div className="flex rounded-xl border border-border/60 p-0.5">
            {HOURS_OPTIONS.map((opt) => (
              <Button
                key={opt.value}
                variant={hours === opt.value ? 'default' : 'ghost'}
                size="sm"
                className="px-3"
                onClick={() => setHours(opt.value)}
              >
                {opt.label}
              </Button>
            ))}
          </div>
        </div>
      </div>

      {/* Tab Navigation */}
      <div className="flex flex-wrap gap-1 rounded-2xl border border-border/60 p-1">
        {TABS.map((tab) => (
          <Button
            key={tab.id}
            variant={activeTab === tab.id ? 'default' : 'ghost'}
            size="sm"
            className="gap-2"
            onClick={() => setActiveTab(tab.id)}
          >
            <tab.icon className="h-4 w-4" />
            {tab.label}
          </Button>
        ))}
      </div>

      {/* Zone selector for temperature tab */}
      {(activeTab === 'temperature' || activeTab === 'occupancy') && zones && zones.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {zones.map((zone) => (
            <Button
              key={zone.id}
              variant={effectiveZoneId === zone.id ? 'default' : 'outline'}
              size="sm"
              onClick={() => setSelectedZoneId(zone.id)}
            >
              {zone.name}
            </Button>
          ))}
        </div>
      )}

      {/* Tab Content */}
      {activeTab === 'temperature' && (
        <TemperatureTab zoneId={effectiveZoneId} hours={hours} zones={zones} />
      )}
      {activeTab === 'occupancy' && (
        <OccupancyTab zoneId={effectiveZoneId} hours={hours} />
      )}
      {activeTab === 'energy' && <EnergyTab hours={hours} />}
      {activeTab === 'comfort' && <ComfortTab hours={hours} />}
      {activeTab === 'decisions' && <DecisionsTab hours={hours} />}
    </div>
  )
}

// ============================================================================
// Temperature History Tab
// ============================================================================
function TemperatureTab({
  zoneId,
  hours,
  zones,
}: {
  zoneId: string | null
  hours: number
  zones?: ZoneBackend[]
}) {
  const { data: history, isLoading } = useQuery<HistoryResponse>({
    queryKey: ['zone-history', zoneId, hours],
    queryFn: () =>
      api.get<HistoryResponse>(`/analytics/zones/${zoneId}/history`, {
        hours,
        resolution: hours > 24 ? 900 : 300,
      }),
    enabled: !!zoneId,
  })

  const chartData = useMemo(() => {
    if (!history?.readings?.length) return []
    return history.readings.map((r) => ({
      time: new Date(r.recorded_at).toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        ...(hours > 24 ? { month: 'short', day: 'numeric' } : {}),
      }),
      temperature: r.temperature_c,
      humidity: r.humidity,
    }))
  }, [history, hours])

  const zoneName = zones?.find((z) => z.id === zoneId)?.name ?? 'Zone'

  return (
    <div className="space-y-6">
      {/* Summary Stats */}
      {history && (
        <div className="grid gap-4 sm:grid-cols-4">
          <Card className="border-border/60">
            <CardContent className="p-4">
              <p className="text-xs text-muted-foreground">Avg Temperature</p>
              <p className="text-2xl font-semibold">
                {history.avg_temperature_c != null
                  ? `${history.avg_temperature_c.toFixed(1)}°C`
                  : '--'}
              </p>
            </CardContent>
          </Card>
          <Card className="border-border/60">
            <CardContent className="p-4">
              <p className="text-xs text-muted-foreground">Min / Max</p>
              <p className="text-2xl font-semibold">
                {history.min_temperature_c != null && history.max_temperature_c != null
                  ? `${history.min_temperature_c.toFixed(1)} / ${history.max_temperature_c.toFixed(1)}°C`
                  : '--'}
              </p>
            </CardContent>
          </Card>
          <Card className="border-border/60">
            <CardContent className="p-4">
              <p className="text-xs text-muted-foreground">Avg Humidity</p>
              <p className="text-2xl font-semibold">
                {history.avg_humidity != null ? `${history.avg_humidity.toFixed(0)}%` : '--'}
              </p>
            </CardContent>
          </Card>
          <Card className="border-border/60">
            <CardContent className="p-4">
              <p className="text-xs text-muted-foreground">Total Readings</p>
              <p className="text-2xl font-semibold">{history.total_readings}</p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Chart */}
      <Card className="border-border/60">
        <CardHeader>
          <CardTitle>
            {zoneName} - Temperature & Humidity ({hours}h)
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex h-80 items-center justify-center">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          ) : chartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={350}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                <XAxis dataKey="time" tick={{ fontSize: 10 }} stroke="hsl(var(--muted-foreground))" interval="preserveStartEnd" />
                <YAxis yAxisId="temp" tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" domain={['auto', 'auto']} />
                <YAxis yAxisId="humidity" orientation="right" tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" domain={[0, 100]} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: 'hsl(var(--card))',
                    border: '1px solid hsl(var(--border))',
                    borderRadius: '8px',
                  }}
                />
                <Legend />
                <Line
                  yAxisId="temp"
                  type="monotone"
                  dataKey="temperature"
                  stroke={COLORS[0]}
                  strokeWidth={2}
                  dot={false}
                  name="Temperature (°C)"
                />
                <Line
                  yAxisId="humidity"
                  type="monotone"
                  dataKey="humidity"
                  stroke={COLORS[1]}
                  strokeWidth={2}
                  dot={false}
                  name="Humidity (%)"
                />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex h-80 items-center justify-center text-muted-foreground">
              {zoneId ? 'No data available for this time period' : 'Select a zone to view data'}
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
function OccupancyTab({ zoneId, hours }: { zoneId: string | null; hours: number }) {
  const { data: history, isLoading } = useQuery<HistoryResponse>({
    queryKey: ['zone-history-occupancy', zoneId, hours],
    queryFn: () =>
      api.get<HistoryResponse>(`/analytics/zones/${zoneId}/history`, {
        hours: Math.min(hours, 168),
        resolution: 3600,
      }),
    enabled: !!zoneId,
  })

  // Build heatmap data: 7 days x 24 hours
  const heatmapData = useMemo(() => {
    if (!history?.readings?.length) return []

    const dayNames = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    const grid: { day: string; hour: number; value: number; count: number }[] = []

    for (let d = 0; d < 7; d++) {
      for (let h = 0; h < 24; h++) {
        grid.push({ day: dayNames[d], hour: h, value: 0, count: 0 })
      }
    }

    for (const reading of history.readings) {
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
  }, [history])

  // Aggregate by hour for bar chart
  const hourlyData = useMemo(() => {
    if (!history?.readings?.length) return []
    const hourBuckets: { hour: string; occupied: number; total: number }[] = []
    for (let h = 0; h < 24; h++) {
      hourBuckets.push({ hour: `${h.toString().padStart(2, '0')}:00`, occupied: 0, total: 0 })
    }
    for (const reading of history.readings) {
      const hour = new Date(reading.recorded_at).getHours()
      hourBuckets[hour].total++
      if (reading.presence) hourBuckets[hour].occupied++
    }
    return hourBuckets.map((b) => ({
      ...b,
      rate: b.total > 0 ? Math.round((b.occupied / b.total) * 100) : 0,
    }))
  }, [history])

  return (
    <div className="space-y-6">
      {/* Occupancy Heatmap (simplified as a bar chart by hour) */}
      <Card className="border-border/60">
        <CardHeader>
          <CardTitle>Occupancy by Hour</CardTitle>
          <CardDescription>Percentage of time the zone was occupied during each hour</CardDescription>
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
                <XAxis dataKey="hour" tick={{ fontSize: 10 }} stroke="hsl(var(--muted-foreground))" />
                <YAxis tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" domain={[0, 100]} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: 'hsl(var(--card))',
                    border: '1px solid hsl(var(--border))',
                    borderRadius: '8px',
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
              {zoneId ? 'No occupancy data available' : 'Select a zone to view data'}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Heatmap Grid */}
      {heatmapData.length > 0 && (
        <Card className="border-border/60">
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
          <Card className="border-border/60">
            <CardContent className="flex items-center justify-between p-4">
              <div>
                <p className="text-xs text-muted-foreground">Total Energy</p>
                <p className="text-2xl font-semibold">{energy.total_estimated_kwh.toFixed(1)} kWh</p>
              </div>
              <Zap className="h-8 w-8 text-yellow-500" />
            </CardContent>
          </Card>
          <Card className="border-border/60">
            <CardContent className="flex items-center justify-between p-4">
              <div>
                <p className="text-xs text-muted-foreground">Estimated Cost</p>
                <p className="text-2xl font-semibold">
                  ${energy.total_estimated_cost_usd.toFixed(2)}
                </p>
              </div>
              <DollarSign className="h-8 w-8 text-green-500" />
            </CardContent>
          </Card>
          <Card className="border-border/60">
            <CardContent className="flex items-center justify-between p-4">
              <div>
                <p className="text-xs text-muted-foreground">Rate</p>
                <p className="text-2xl font-semibold">${energy.cost_per_kwh}/kWh</p>
              </div>
              <Activity className="h-8 w-8 text-primary" />
            </CardContent>
          </Card>
        </div>
      )}

      {/* Energy by Zone Chart */}
      <Card className="border-border/60">
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
                    backgroundColor: 'hsl(var(--card))',
                    border: '1px solid hsl(var(--border))',
                    borderRadius: '8px',
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
        <Card className="border-border/60">
          <CardHeader>
            <CardTitle>Zone Breakdown</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border/60">
                    <th className="pb-2 text-left font-medium">Zone</th>
                    <th className="pb-2 text-right font-medium">Devices</th>
                    <th className="pb-2 text-right font-medium">Actions</th>
                    <th className="pb-2 text-right font-medium">Energy (kWh)</th>
                    <th className="pb-2 text-right font-medium">Cost</th>
                    <th className="pb-2 text-right font-medium">Primary Device</th>
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
function ComfortTab({ hours }: { hours: number }) {
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
        <Card className="border-border/60">
          <CardContent className="flex items-center gap-6 p-6">
            <div
              className="flex h-24 w-24 items-center justify-center rounded-full border-4"
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
            <Card key={zone.zone_id} className="border-border/60">
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
                    <div className="mt-1 h-2 rounded-full bg-muted">
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
                    <div className="mt-1 h-2 rounded-full bg-muted">
                      <div
                        className="h-2 rounded-full bg-blue-500"
                        style={{ width: `${Math.min(100, zone.humidity_in_range_pct)}%` }}
                      />
                    </div>
                  </div>
                  <div className="flex justify-between text-xs text-muted-foreground">
                    <span>
                      Avg: {zone.avg_temperature_c?.toFixed(1) ?? '--'}°C /{' '}
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
        <Card className="border-border/60">
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
      <Card className="border-border/60">
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
                      className="flex items-center justify-between rounded-lg border border-border/40 p-3"
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
