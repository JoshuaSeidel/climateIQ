import { useMemo, useState, useCallback, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { api } from '@/lib/api'
import type { Zone, ZoneBackend, Sensor, ZoneType, SensorType, HistoryResponse, HAEntity } from '@/types'
import { useSettingsStore } from '@/stores/settingsStore'
import { formatTemperature, toDisplayTemp, toStorageCelsius, tempUnitLabel } from '@/lib/utils'
import {
  Plus,
  Thermometer,
  Droplets,
  Users,
  Pencil,
  Trash2,
  X,
  Check,
  ChevronRight,
  ArrowLeft,
  Loader2,
  Eye,
  Lightbulb,
} from 'lucide-react'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts'

type ViewMode = 'list' | 'detail' | 'create' | 'edit'

const ZONE_TYPES: { value: ZoneType; label: string }[] = [
  { value: 'bedroom', label: 'Bedroom' },
  { value: 'living_area', label: 'Living Area' },
  { value: 'kitchen', label: 'Kitchen' },
  { value: 'bathroom', label: 'Bathroom' },
  { value: 'hallway', label: 'Hallway' },
  { value: 'basement', label: 'Basement' },
  { value: 'attic', label: 'Attic' },
  { value: 'garage', label: 'Garage' },
  { value: 'office', label: 'Office' },
  { value: 'other', label: 'Other' },
]

const SENSOR_TYPES: { value: SensorType; label: string }[] = [
  { value: 'multisensor', label: 'Multi-sensor' },
  { value: 'temp_only', label: 'Temperature Only' },
  { value: 'humidity_only', label: 'Humidity Only' },
  { value: 'presence_only', label: 'Presence Only' },
  { value: 'temp_humidity', label: 'Temp + Humidity' },
  { value: 'presence_lux', label: 'Presence + Lux' },
  { value: 'other', label: 'Other' },
]

interface ZoneFormData {
  name: string
  description: string
  type: ZoneType
  floor: string
  is_active: boolean
}

interface SensorFormData {
  name: string
  type: SensorType
  manufacturer: string
  model: string
  ha_entity_id: string
}

interface ComfortPrefs {
  temp_min: string
  temp_max: string
  humidity_min: string
  humidity_max: string
}

const defaultZoneForm: ZoneFormData = {
  name: '',
  description: '',
  type: 'living_area',
  floor: '1',
  is_active: true,
}

export const Zones = () => {
  const queryClient = useQueryClient()
  const { temperatureUnit } = useSettingsStore()
  const unitKey: 'c' | 'f' = temperatureUnit === 'celsius' ? 'c' : 'f'
  const [viewMode, setViewMode] = useState<ViewMode>('list')
  const [selectedZoneId, setSelectedZoneId] = useState<string | null>(null)
  const [zoneForm, setZoneForm] = useState<ZoneFormData>(defaultZoneForm)
  const [sensorForm, setSensorForm] = useState<SensorFormData>({
    name: '',
    type: 'temp_humidity',
    manufacturer: '',
    model: '',
    ha_entity_id: '',
  })
  const [showSensorForm, setShowSensorForm] = useState(false)
  const [entitySearch, setEntitySearch] = useState('')
  const [entityPickerOpen, setEntityPickerOpen] = useState(false)
  const entityPickerRef = useRef<HTMLDivElement>(null)

  // Close entity picker on outside click
  useEffect(() => {
    if (!entityPickerOpen) return
    const handler = (e: MouseEvent) => {
      if (entityPickerRef.current && !entityPickerRef.current.contains(e.target as Node)) {
        setEntityPickerOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [entityPickerOpen])
  const [comfortPrefs, setComfortPrefs] = useState<ComfortPrefs>({
    temp_min: '20',
    temp_max: '24',
    humidity_min: '30',
    humidity_max: '60',
  })
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)

  // Fetch zones from backend
  const { data: zonesRaw, isLoading: zonesLoading } = useQuery<ZoneBackend[]>({
    queryKey: ['zones-raw'],
    queryFn: () => api.get<ZoneBackend[]>('/zones'),
  })

  // Map to frontend Zone shape (same approach as Dashboard.tsx)
  const zones: Zone[] = useMemo(
    () =>
      (zonesRaw ?? []).map((z) => {
        const raw = z as unknown as Record<string, unknown>
        return {
          id: z.id,
          name: z.name,
          description: z.description,
          type: z.type,
          floor: z.floor,
          is_active: z.is_active,
          temperature: (raw.current_temp as number | null) || null,
          humidity: (raw.current_humidity as number | null) || null,
          occupancy: raw.is_occupied === true ? 'occupied' : raw.is_occupied === false ? 'vacant' : null,
          targetTemperature: (raw.target_temp as number | null) ?? null,
          sensors: z.sensors,
          devices: z.devices,
        }
      }),
    [zonesRaw],
  )

  const selectedZone = useMemo(
    () => zones.find((z) => z.id === selectedZoneId),
    [zones, selectedZoneId],
  )

  const selectedZoneRaw = useMemo(
    () => (zonesRaw ?? []).find((z) => z.id === selectedZoneId),
    [zonesRaw, selectedZoneId],
  )

  // Fetch zone history for detail view
  const { data: zoneHistory, isLoading: historyLoading } = useQuery<HistoryResponse>({
    queryKey: ['zone-history', selectedZoneId],
    queryFn: () => api.get<HistoryResponse>(`/analytics/zones/${selectedZoneId}/history`, { hours: 24, resolution: 300 }),
    enabled: !!selectedZoneId && viewMode === 'detail',
  })

  // Fetch HA sensor entities for the sensor picker
  const { data: haSensorEntities } = useQuery<HAEntity[]>({
    queryKey: ['ha-entities', 'sensor-all'],
    queryFn: async () => {
      const [sensors, binary] = await Promise.all([
        api.get<HAEntity[]>('/settings/ha/entities', { domain: 'sensor' }),
        api.get<HAEntity[]>('/settings/ha/entities', { domain: 'binary_sensor' }),
      ])
      return [...sensors, ...binary]
    },
    enabled: viewMode === 'detail' && showSensorForm,
  })

  // Fetch sensors for a zone
  const { data: zoneSensors } = useQuery<Sensor[]>({
    queryKey: ['zone-sensors', selectedZoneId],
    queryFn: () => api.get<Sensor[]>('/sensors', { zone_id: selectedZoneId! }),
    enabled: !!selectedZoneId && viewMode === 'detail',
  })

  // Create zone mutation
  const createZone = useMutation({
    mutationFn: (data: ZoneFormData) =>
      api.post<ZoneBackend>('/zones', {
        name: data.name,
        description: data.description || undefined,
        type: data.type,
        floor: data.floor ? Number(data.floor) : undefined,
        is_active: data.is_active,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['zones-raw'] })
      queryClient.invalidateQueries({ queryKey: ['zones'] })
      setViewMode('list')
      setZoneForm(defaultZoneForm)
    },
  })

  // Update zone mutation
  const updateZone = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<ZoneFormData> }) =>
      api.put<ZoneBackend>(`/zones/${id}`, {
        name: data.name,
        description: data.description || undefined,
        type: data.type,
        floor: data.floor ? Number(data.floor) : undefined,
        is_active: data.is_active,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['zones-raw'] })
      queryClient.invalidateQueries({ queryKey: ['zones'] })
      setViewMode('list')
    },
  })

  // Delete zone mutation
  const deleteZone = useMutation({
    mutationFn: (id: string) => api.delete(`/zones/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['zones-raw'] })
      queryClient.invalidateQueries({ queryKey: ['zones'] })
      setDeleteConfirm(null)
      if (selectedZoneId) {
        setSelectedZoneId(null)
        setViewMode('list')
      }
    },
  })

  // Create sensor mutation
  const createSensor = useMutation({
    mutationFn: (data: SensorFormData & { zone_id: string }) =>
      api.post<Sensor>('/sensors', {
        name: data.name,
        type: data.type,
        zone_id: data.zone_id,
        manufacturer: data.manufacturer || undefined,
        model: data.model || undefined,
        ha_entity_id: data.ha_entity_id || undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['zone-sensors', selectedZoneId] })
      queryClient.invalidateQueries({ queryKey: ['zones-raw'] })
      setShowSensorForm(false)
      setSensorForm({ name: '', type: 'temp_humidity', manufacturer: '', model: '', ha_entity_id: '' })
    },
  })

  // Delete sensor mutation
  const deleteSensor = useMutation({
    mutationFn: (id: string) => api.delete(`/sensors/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['zone-sensors', selectedZoneId] })
      queryClient.invalidateQueries({ queryKey: ['zones-raw'] })
    },
  })

  // Save comfort preferences mutation
  const [comfortSaveStatus, setComfortSaveStatus] = useState<'idle' | 'success' | 'error'>('idle')
  const saveComfortPrefs = useMutation({
    mutationFn: ({ id, prefs }: { id: string; prefs: ComfortPrefs }) =>
      api.put<ZoneBackend>(`/zones/${id}`, {
        comfort_preferences: {
          temp_min: Number(toStorageCelsius(Number(prefs.temp_min), unitKey).toFixed(2)),
          temp_max: Number(toStorageCelsius(Number(prefs.temp_max), unitKey).toFixed(2)),
          humidity_min: Number(prefs.humidity_min),
          humidity_max: Number(prefs.humidity_max),
        },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['zones-raw'] })
      queryClient.invalidateQueries({ queryKey: ['zones'] })
      setComfortSaveStatus('success')
      setTimeout(() => setComfortSaveStatus('idle'), 3000)
    },
    onError: () => {
      setComfortSaveStatus('error')
      setTimeout(() => setComfortSaveStatus('idle'), 3000)
    },
  })

  const handleOpenDetail = useCallback((zoneId: string) => {
    const zoneRaw = (zonesRaw ?? []).find((z) => z.id === zoneId)
    if (zoneRaw?.comfort_preferences) {
      const cp = zoneRaw.comfort_preferences as Record<string, unknown>
      // Backend stores in Celsius — convert to display unit
      const minC = Number(cp.temp_min ?? 20)
      const maxC = Number(cp.temp_max ?? 24)
      setComfortPrefs({
        temp_min: String(Number(toDisplayTemp(minC, unitKey).toFixed(1))),
        temp_max: String(Number(toDisplayTemp(maxC, unitKey).toFixed(1))),
        humidity_min: String(cp.humidity_min ?? 30),
        humidity_max: String(cp.humidity_max ?? 60),
      })
    } else {
      setComfortPrefs({
        temp_min: String(Number(toDisplayTemp(20, unitKey).toFixed(1))),
        temp_max: String(Number(toDisplayTemp(24, unitKey).toFixed(1))),
        humidity_min: '30',
        humidity_max: '60',
      })
    }
    setSelectedZoneId(zoneId)
    setViewMode('detail')
  }, [zonesRaw, unitKey])

  const handleEditZone = useCallback(
    (zone: Zone) => {
      setSelectedZoneId(zone.id)
      setZoneForm({
        name: zone.name,
        description: zone.description ?? '',
        type: zone.type ?? 'other',
        floor: String(zone.floor ?? 1),
        is_active: zone.is_active ?? true,
      })
      setViewMode('edit')
    },
    [],
  )

  // Chart data from history
  const chartData = useMemo(() => {
    if (!zoneHistory?.readings?.length) return []
    return zoneHistory.readings.map((r) => ({
      time: new Date(r.recorded_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      temperature: r.temperature_c != null ? toDisplayTemp(r.temperature_c, unitKey) : null,
      humidity: r.humidity,
    }))
  }, [zoneHistory, unitKey])

  // ============================================================================
  // CREATE VIEW
  // ============================================================================
  if (viewMode === 'create' || viewMode === 'edit') {
    const isEdit = viewMode === 'edit'
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="sm" onClick={() => setViewMode('list')}>
            <ArrowLeft className="mr-2 h-4 w-4" />
            Back
          </Button>
          <div>
            <p className="text-xs uppercase tracking-widest text-muted-foreground">Zones</p>
            <h2 className="text-2xl font-semibold">{isEdit ? 'Edit Zone' : 'Create Zone'}</h2>
          </div>
        </div>

        <Card className="border-border/60">
          <CardContent className="space-y-4 pt-6">
            <div>
              <label className="text-sm font-medium">Name</label>
              <Input
                value={zoneForm.name}
                onChange={(e) => setZoneForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="e.g. Living Room"
              />
            </div>
            <div>
              <label className="text-sm font-medium">Description</label>
              <Input
                value={zoneForm.description}
                onChange={(e) => setZoneForm((f) => ({ ...f, description: e.target.value }))}
                placeholder="Optional description"
              />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="text-sm font-medium">Type</label>
                <select
                  value={zoneForm.type}
                  onChange={(e) => setZoneForm((f) => ({ ...f, type: e.target.value as ZoneType }))}
                  className="flex h-11 w-full rounded-xl border border-input bg-transparent px-4 text-sm"
                >
                  {ZONE_TYPES.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-sm font-medium">Floor</label>
                <Input
                  type="number"
                  value={zoneForm.floor}
                  onChange={(e) => setZoneForm((f) => ({ ...f, floor: e.target.value }))}
                />
              </div>
            </div>
            <div className="flex items-center gap-3">
              <input
                type="checkbox"
                checked={zoneForm.is_active}
                onChange={(e) => setZoneForm((f) => ({ ...f, is_active: e.target.checked }))}
                className="h-4 w-4 rounded border-border"
              />
              <label className="text-sm font-medium">Active</label>
            </div>
            <div className="flex gap-3 pt-2">
              <Button
                onClick={() => {
                  if (isEdit && selectedZoneId) {
                    updateZone.mutate({ id: selectedZoneId, data: zoneForm })
                  } else {
                    createZone.mutate(zoneForm)
                  }
                }}
                disabled={!zoneForm.name || createZone.isPending || updateZone.isPending}
              >
                {(createZone.isPending || updateZone.isPending) ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Check className="mr-2 h-4 w-4" />
                )}
                {isEdit ? 'Update Zone' : 'Create Zone'}
              </Button>
              <Button variant="outline" onClick={() => setViewMode('list')}>
                Cancel
              </Button>
            </div>
            {(createZone.isError || updateZone.isError) && (
              <p className="text-sm text-red-500">
                {(createZone.error || updateZone.error)?.message ?? 'An error occurred'}
              </p>
            )}
          </CardContent>
        </Card>
      </div>
    )
  }

  // ============================================================================
  // DETAIL VIEW
  // ============================================================================
  if (viewMode === 'detail' && selectedZone) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="sm" onClick={() => setViewMode('list')}>
            <ArrowLeft className="mr-2 h-4 w-4" />
            Back
          </Button>
          <div className="flex-1">
            <p className="text-xs uppercase tracking-widest text-muted-foreground">Zone Detail</p>
            <h2 className="text-2xl font-semibold">{selectedZone.name}</h2>
            {selectedZone.description && (
              <p className="text-sm text-muted-foreground">{selectedZone.description}</p>
            )}
          </div>
          <Button variant="outline" size="sm" onClick={() => handleEditZone(selectedZone)}>
            <Pencil className="mr-2 h-4 w-4" />
            Edit
          </Button>
        </div>

        {/* Zone Stats */}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Card className="border-border/60">
            <CardContent className="p-4">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Thermometer className="h-4 w-4" /> Avg Temp
              </div>
              <p className="text-2xl font-semibold">
                {zoneHistory?.avg_temperature_c != null
                  ? formatTemperature(zoneHistory.avg_temperature_c, unitKey)
                  : '--'}
              </p>
            </CardContent>
          </Card>
          <Card className="border-border/60">
            <CardContent className="p-4">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Droplets className="h-4 w-4" /> Avg Humidity
              </div>
              <p className="text-2xl font-semibold">
                {zoneHistory?.avg_humidity != null
                  ? `${zoneHistory.avg_humidity.toFixed(0)}%`
                  : '--'}
              </p>
            </CardContent>
          </Card>
          <Card className="border-border/60">
            <CardContent className="p-4">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Thermometer className="h-4 w-4" /> Temp Range
              </div>
              <p className="text-2xl font-semibold">
                {zoneHistory?.min_temperature_c != null && zoneHistory?.max_temperature_c != null
                  ? `${toDisplayTemp(zoneHistory.min_temperature_c, unitKey).toFixed(0)}-${toDisplayTemp(zoneHistory.max_temperature_c, unitKey).toFixed(0)}${tempUnitLabel(unitKey)}`
                  : '--'}
              </p>
            </CardContent>
          </Card>
          <Card className="border-border/60">
            <CardContent className="p-4">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Eye className="h-4 w-4" /> Readings
              </div>
              <p className="text-2xl font-semibold">{zoneHistory?.total_readings ?? 0}</p>
            </CardContent>
          </Card>
        </div>

        {/* 24-hour Temperature/Humidity Chart */}
        <Card className="border-border/60">
          <CardHeader>
            <CardTitle>24-Hour History</CardTitle>
            <CardDescription>Temperature and humidity over the last 24 hours</CardDescription>
          </CardHeader>
          <CardContent>
            {historyLoading ? (
              <div className="flex h-64 items-center justify-center">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
              </div>
            ) : chartData.length > 0 ? (
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                  <XAxis dataKey="time" tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
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
                    stroke="hsl(25, 95%, 53%)"
                    strokeWidth={2}
                    dot={false}
                    name={`Temperature (${tempUnitLabel(unitKey)})`}
                  />
                  <Line
                    yAxisId="humidity"
                    type="monotone"
                    dataKey="humidity"
                    stroke="hsl(210, 100%, 60%)"
                    strokeWidth={2}
                    dot={false}
                    name="Humidity (%)"
                  />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-64 items-center justify-center text-muted-foreground">
                No sensor data available for this zone
              </div>
            )}
          </CardContent>
        </Card>

        {/* Sensors */}
        <Card className="border-border/60">
          <CardHeader className="flex flex-row items-center justify-between">
            <div>
              <CardTitle>Sensors</CardTitle>
              <CardDescription>Sensors assigned to this zone</CardDescription>
            </div>
            <Button size="sm" onClick={() => setShowSensorForm(!showSensorForm)}>
              {showSensorForm ? <X className="mr-2 h-4 w-4" /> : <Plus className="mr-2 h-4 w-4" />}
              {showSensorForm ? 'Cancel' : 'Add Sensor'}
            </Button>
          </CardHeader>
          <CardContent>
            {showSensorForm && (
              <div className="mb-4 space-y-3 rounded-lg border border-border/60 p-4">
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <label className="text-sm font-medium">Name</label>
                    <Input
                      value={sensorForm.name}
                      onChange={(e) => setSensorForm((f) => ({ ...f, name: e.target.value }))}
                      placeholder="e.g. Living Room Sensor"
                    />
                  </div>
                  <div>
                    <label className="text-sm font-medium">Type</label>
                    <select
                      value={sensorForm.type}
                      onChange={(e) => setSensorForm((f) => ({ ...f, type: e.target.value as SensorType }))}
                      className="flex h-11 w-full rounded-xl border border-input bg-transparent px-4 text-sm"
                    >
                      {SENSOR_TYPES.map((t) => (
                        <option key={t.value} value={t.value}>
                          {t.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-sm font-medium">Manufacturer</label>
                    <Input
                      value={sensorForm.manufacturer}
                      onChange={(e) => setSensorForm((f) => ({ ...f, manufacturer: e.target.value }))}
                      placeholder="Optional"
                    />
                  </div>
                  <div>
                    <label className="text-sm font-medium">Model</label>
                    <Input
                      value={sensorForm.model}
                      onChange={(e) => setSensorForm((f) => ({ ...f, model: e.target.value }))}
                      placeholder="Optional"
                    />
                  </div>
                  <div className="sm:col-span-2">
                    <label className="text-sm font-medium">HA Entity (optional)</label>
                    <div className="relative" ref={entityPickerRef}>
                      <Input
                        value={entityPickerOpen ? entitySearch : (sensorForm.ha_entity_id || '')}
                        onChange={(e) => {
                          setEntitySearch(e.target.value)
                          if (!entityPickerOpen) setEntityPickerOpen(true)
                          // Allow typing a custom entity ID directly
                          setSensorForm((f) => ({ ...f, ha_entity_id: e.target.value }))
                        }}
                        onFocus={() => setEntityPickerOpen(true)}
                        placeholder="Search entities or paste an entity_id..."
                        className="w-full"
                      />
                      {sensorForm.ha_entity_id && !entityPickerOpen && (
                        <button
                          type="button"
                          onClick={() => {
                            setSensorForm((f) => ({ ...f, ha_entity_id: '' }))
                            setEntitySearch('')
                          }}
                          className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                        >
                          <X className="h-4 w-4" />
                        </button>
                      )}
                      {entityPickerOpen && (
                        <div className="absolute z-50 mt-1 max-h-60 w-full overflow-auto rounded-lg border border-border bg-background shadow-lg">
                          <button
                            type="button"
                            className="w-full px-3 py-2 text-left text-sm hover:bg-muted"
                            onClick={() => {
                              setSensorForm((f) => ({ ...f, ha_entity_id: '' }))
                              setEntitySearch('')
                              setEntityPickerOpen(false)
                            }}
                          >
                            <span className="text-muted-foreground">None — manual sensor</span>
                          </button>
                          {(haSensorEntities ?? [])
                            .filter((entity) => {
                              if (!entitySearch.trim()) return true
                              const q = entitySearch.toLowerCase()
                              return (
                                entity.entity_id.toLowerCase().includes(q) ||
                                entity.name.toLowerCase().includes(q) ||
                                (entity.device_class ?? '').toLowerCase().includes(q)
                              )
                            })
                            .map((entity) => (
                              <button
                                key={entity.entity_id}
                                type="button"
                                className={`w-full px-3 py-2 text-left text-sm hover:bg-muted ${
                                  sensorForm.ha_entity_id === entity.entity_id ? 'bg-muted' : ''
                                }`}
                                onClick={() => {
                                  setSensorForm((f) => ({
                                    ...f,
                                    ha_entity_id: entity.entity_id,
                                    name: f.name || entity.name,
                                  }))
                                  setEntitySearch('')
                                  setEntityPickerOpen(false)
                                }}
                              >
                                <div className="flex items-center justify-between">
                                  <div className="min-w-0 flex-1">
                                    <div className="truncate font-medium">{entity.name}</div>
                                    <div className="truncate text-xs text-muted-foreground">
                                      {entity.entity_id}
                                      {entity.device_class ? ` · ${entity.device_class}` : ''}
                                      {entity.unit_of_measurement ? ` · ${entity.unit_of_measurement}` : ''}
                                    </div>
                                  </div>
                                  <span className="ml-2 shrink-0 text-xs text-muted-foreground">
                                    {entity.state}
                                  </span>
                                </div>
                              </button>
                            ))}
                          {entitySearch.trim() &&
                            (haSensorEntities ?? []).filter((e) => {
                              const q = entitySearch.toLowerCase()
                              return e.entity_id.toLowerCase().includes(q) || e.name.toLowerCase().includes(q)
                            }).length === 0 && (
                              <div className="px-3 py-2 text-sm text-muted-foreground">
                                No matches. The typed value will be used as a custom entity ID.
                              </div>
                            )}
                        </div>
                      )}
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Search by name, entity ID, or device class. You can also paste a custom entity ID.
                    </p>
                   </div>
                </div>
                <Button
                  size="sm"
                  disabled={!sensorForm.name || createSensor.isPending}
                  onClick={() =>
                    createSensor.mutate({ ...sensorForm, zone_id: selectedZoneId! })
                  }
                >
                  {createSensor.isPending ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <Check className="mr-2 h-4 w-4" />
                  )}
                  Add Sensor
                </Button>
              </div>
            )}

            {(zoneSensors ?? selectedZoneRaw?.sensors ?? []).length > 0 ? (
              <div className="space-y-2">
                {(zoneSensors ?? selectedZoneRaw?.sensors ?? []).map((sensor) => (
                  <div
                    key={sensor.id}
                    className="flex items-center justify-between rounded-lg border border-border/40 p-3"
                  >
                    <div>
                      <p className="text-sm font-medium">{sensor.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {sensor.type} {sensor.manufacturer ? `- ${sensor.manufacturer}` : ''}
                        {sensor.model ? ` ${sensor.model}` : ''}
                        {sensor.ha_entity_id ? ` · ${sensor.ha_entity_id}` : ''}
                      </p>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8 text-red-500 hover:text-red-600"
                      onClick={() => deleteSensor.mutate(sensor.id)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No sensors assigned to this zone</p>
            )}
          </CardContent>
        </Card>

        {/* Comfort Preferences */}
        <Card className="border-border/60">
          <CardHeader>
            <div className="flex items-center gap-2">
              <Lightbulb className="h-5 w-5 text-primary" />
              <CardTitle>Comfort Preferences</CardTitle>
            </div>
            <CardDescription>Set comfort ranges for this zone</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="text-sm font-medium">Min Temperature ({tempUnitLabel(unitKey)})</label>
                <Input
                  type="number"
                  step="0.5"
                  value={comfortPrefs.temp_min}
                  onChange={(e) => setComfortPrefs((p) => ({ ...p, temp_min: e.target.value }))}
                />
              </div>
              <div>
                <label className="text-sm font-medium">Max Temperature ({tempUnitLabel(unitKey)})</label>
                <Input
                  type="number"
                  step="0.5"
                  value={comfortPrefs.temp_max}
                  onChange={(e) => setComfortPrefs((p) => ({ ...p, temp_max: e.target.value }))}
                />
              </div>
              <div>
                <label className="text-sm font-medium">Min Humidity (%)</label>
                <Input
                  type="number"
                  value={comfortPrefs.humidity_min}
                  onChange={(e) => setComfortPrefs((p) => ({ ...p, humidity_min: e.target.value }))}
                />
              </div>
              <div>
                <label className="text-sm font-medium">Max Humidity (%)</label>
                <Input
                  type="number"
                  value={comfortPrefs.humidity_max}
                  onChange={(e) => setComfortPrefs((p) => ({ ...p, humidity_max: e.target.value }))}
                />
              </div>
            </div>
            <div className="mt-4 flex items-center gap-3">
              <Button
                size="sm"
                disabled={saveComfortPrefs.isPending}
                onClick={() => {
                  if (selectedZoneId) {
                    saveComfortPrefs.mutate({ id: selectedZoneId, prefs: comfortPrefs })
                  }
                }}
              >
                {saveComfortPrefs.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Check className="mr-2 h-4 w-4" />
                )}
                Save Preferences
              </Button>
              {comfortSaveStatus === 'success' && (
                <span className="text-sm text-green-600">Preferences saved</span>
              )}
              {comfortSaveStatus === 'error' && (
                <span className="text-sm text-red-500">Failed to save preferences</span>
              )}
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              Comfort range: {comfortPrefs.temp_min}-{comfortPrefs.temp_max}{tempUnitLabel(unitKey)},{' '}
              {comfortPrefs.humidity_min}-{comfortPrefs.humidity_max}% humidity
            </p>
          </CardContent>
        </Card>
      </div>
    )
  }

  // ============================================================================
  // LIST VIEW
  // ============================================================================
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-widest text-muted-foreground">Zones</p>
          <h2 className="text-2xl font-semibold">Manage Zones</h2>
        </div>
        <Button
          className="gap-2"
          onClick={() => {
            setZoneForm(defaultZoneForm)
            setViewMode('create')
          }}
        >
          <Plus className="h-4 w-4" />
          Add Zone
        </Button>
      </div>

      {zonesLoading ? (
        <div className="grid gap-4">
          {[1, 2, 3].map((i) => (
            <Card key={i} className="h-40 animate-pulse border-border/60 bg-muted/20" />
          ))}
        </div>
      ) : (
        <div className="grid gap-4">
          {zones.map((zone) => (
            <Card key={zone.id} className="border-border/60">
              <CardHeader className="flex flex-row items-center justify-between">
                <div className="cursor-pointer" onClick={() => handleOpenDetail(zone.id)}>
                  <p className="text-xs uppercase tracking-widest text-muted-foreground">
                    {zone.type?.replace('_', ' ') ?? 'Zone'}
                    {zone.floor != null ? ` - Floor ${zone.floor}` : ''}
                  </p>
                  <CardTitle className="flex items-center gap-2">
                    {zone.name}
                    {zone.is_active === false && (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                        Inactive
                      </span>
                    )}
                  </CardTitle>
                  {zone.description && (
                    <p className="text-sm text-muted-foreground">{zone.description}</p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <Button variant="ghost" size="sm" onClick={() => handleOpenDetail(zone.id)}>
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => handleEditZone(zone)}>
                    <Pencil className="h-4 w-4" />
                  </Button>
                  {deleteConfirm === zone.id ? (
                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-red-500"
                        onClick={() => deleteZone.mutate(zone.id)}
                        disabled={deleteZone.isPending}
                      >
                        {deleteZone.isPending ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Check className="h-4 w-4" />
                        )}
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => setDeleteConfirm(null)}>
                        <X className="h-4 w-4" />
                      </Button>
                    </div>
                  ) : (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-red-500"
                      onClick={() => setDeleteConfirm(zone.id)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  )}
                </div>
              </CardHeader>
              <CardContent>
                <div className="grid gap-4 md:grid-cols-4">
                  <div className="rounded-2xl border border-border/60 p-4">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Thermometer className="h-4 w-4" /> Temp
                    </div>
                    <p className="text-2xl font-semibold text-foreground">
                      {zone.temperature != null ? formatTemperature(zone.temperature, unitKey) : '--'}
                    </p>
                  </div>
                  <div className="rounded-2xl border border-border/60 p-4">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Droplets className="h-4 w-4" /> Humidity
                    </div>
                    <p className="text-2xl font-semibold text-foreground">
                      {zone.humidity != null ? `${zone.humidity.toFixed(0)}%` : '--'}
                    </p>
                  </div>
                  <div className="rounded-2xl border border-border/60 p-4">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Users className="h-4 w-4" /> Occupancy
                    </div>
                    <p className="text-2xl font-semibold text-foreground">
                      {zone.occupancy === 'occupied' ? 'Occupied' : zone.occupancy === 'vacant' ? 'Vacant' : '--'}
                    </p>
                  </div>
                  <div className="rounded-2xl border border-border/60 p-4">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      Sensors / Devices
                    </div>
                    <p className="text-2xl font-semibold text-foreground">
                      {zone.sensors?.length ?? 0} / {zone.devices?.length ?? 0}
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
          {!zones.length && (
            <Card className="border-dashed border-border/70 bg-card/20 p-8 text-center text-muted-foreground">
              No zones configured yet. Click "Add Zone" to create your first zone.
            </Card>
          )}
        </div>
      )}
    </div>
  )
}
