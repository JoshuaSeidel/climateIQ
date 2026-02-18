import { useState, useMemo, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { api } from '@/lib/api'
import type { Schedule, ZoneBackend } from '@/types'
import { useSettingsStore } from '@/stores/settingsStore'
import { formatTemperature, toDisplayTemp, toStorageCelsius, tempUnitLabel } from '@/lib/utils'
import {
  Plus,
  Pencil,
  Trash2,
  X,
  Check,
  ArrowLeft,
  Loader2,
  Clock,
  AlertTriangle,
  Calendar,
  Thermometer,
  Power,
} from 'lucide-react'

// ============================================================================
// Constants
// ============================================================================

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const
const DAY_VALUES = [0, 1, 2, 3, 4, 5, 6] as const

const HVAC_MODES: { value: string; label: string }[] = [
  { value: 'auto', label: 'Auto' },
  { value: 'heat', label: 'Heat' },
  { value: 'cool', label: 'Cool' },
  { value: 'off', label: 'Off' },
]

const HVAC_MODE_COLORS: Record<string, string> = {
  auto: 'bg-blue-500/10 text-blue-400 border-blue-500/20',
  heat: 'bg-orange-500/10 text-orange-400 border-orange-500/20',
  cool: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/20',
  off: 'bg-muted text-muted-foreground border-border/40',
}

// ============================================================================
// Types
// ============================================================================

type ViewMode = 'list' | 'create' | 'edit'

interface ScheduleFormData {
  name: string
  zone_id: string // '' means all zones (null)
  days_of_week: number[]
  start_time: string
  end_time: string
  target_temp: string // in display unit
  hvac_mode: string
  priority: string
}

type ConflictWarning = {
  schedule_a_id: string
  schedule_a_name: string
  schedule_b_id: string
  schedule_b_name: string
  conflict_type: string
  description: string
}

const defaultForm: ScheduleFormData = {
  name: '',
  zone_id: '',
  days_of_week: [0, 1, 2, 3, 4],
  start_time: '08:00',
  end_time: '',
  target_temp: '22',
  hvac_mode: 'auto',
  priority: '5',
}

// ============================================================================
// Main Component
// ============================================================================

export const Schedules = () => {
  const queryClient = useQueryClient()
  const { temperatureUnit } = useSettingsStore()
  const unitKey: 'c' | 'f' = temperatureUnit === 'celsius' ? 'c' : 'f'

  const [viewMode, setViewMode] = useState<ViewMode>('list')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [form, setForm] = useState<ScheduleFormData>(defaultForm)
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)

  // ---- Queries ----

  const { data: schedules, isLoading: schedulesLoading } = useQuery<Schedule[]>({
    queryKey: ['schedules'],
    queryFn: () => api.get<Schedule[]>('/schedules'),
  })

  const { data: zones } = useQuery<ZoneBackend[]>({
    queryKey: ['zones-raw'],
    queryFn: () => api.get<ZoneBackend[]>('/zones'),
  })

  const { data: conflicts } = useQuery<ConflictWarning[]>({
    queryKey: ['schedule-conflicts'],
    queryFn: () => api.get<ConflictWarning[]>('/schedules/conflicts'),
  })

  // ---- Mutations ----

  const createSchedule = useMutation({
    mutationFn: (data: ScheduleFormData) => {
      const targetC = toStorageCelsius(Number(data.target_temp), unitKey)
      return api.post<Schedule>('/schedules', {
        name: data.name,
        zone_id: data.zone_id || null,
        days_of_week: data.days_of_week,
        start_time: data.start_time,
        end_time: data.end_time || null,
        target_temp_c: Number(targetC.toFixed(2)),
        hvac_mode: data.hvac_mode,
        priority: Number(data.priority),
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] })
      queryClient.invalidateQueries({ queryKey: ['schedule-conflicts'] })
      queryClient.invalidateQueries({ queryKey: ['upcoming-schedules'] })
      setViewMode('list')
      setForm(defaultForm)
    },
  })

  const updateSchedule = useMutation({
    mutationFn: ({ id, data }: { id: string; data: ScheduleFormData }) => {
      const targetC = toStorageCelsius(Number(data.target_temp), unitKey)
      return api.put<Schedule>(`/schedules/${id}`, {
        name: data.name,
        zone_id: data.zone_id || null,
        days_of_week: data.days_of_week,
        start_time: data.start_time,
        end_time: data.end_time || null,
        target_temp_c: Number(targetC.toFixed(2)),
        hvac_mode: data.hvac_mode,
        priority: Number(data.priority),
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] })
      queryClient.invalidateQueries({ queryKey: ['schedule-conflicts'] })
      queryClient.invalidateQueries({ queryKey: ['upcoming-schedules'] })
      setViewMode('list')
      setEditingId(null)
      setForm(defaultForm)
    },
  })

  const deleteSchedule = useMutation({
    mutationFn: (id: string) => api.delete(`/schedules/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] })
      queryClient.invalidateQueries({ queryKey: ['schedule-conflicts'] })
      queryClient.invalidateQueries({ queryKey: ['upcoming-schedules'] })
      setDeleteConfirm(null)
    },
  })

  const enableSchedule = useMutation({
    mutationFn: (id: string) => api.post(`/schedules/${id}/enable`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] })
      queryClient.invalidateQueries({ queryKey: ['upcoming-schedules'] })
    },
  })

  const disableSchedule = useMutation({
    mutationFn: (id: string) => api.post(`/schedules/${id}/disable`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] })
      queryClient.invalidateQueries({ queryKey: ['upcoming-schedules'] })
    },
  })

  // ---- Handlers ----

  const handleEdit = useCallback(
    (schedule: Schedule) => {
      setEditingId(schedule.id)
      setForm({
        name: schedule.name,
        zone_id: schedule.zone_id ?? '',
        days_of_week: [...schedule.days_of_week],
        start_time: schedule.start_time,
        end_time: schedule.end_time ?? '',
        target_temp: String(Number(toDisplayTemp(schedule.target_temp_c, unitKey).toFixed(1))),
        hvac_mode: schedule.hvac_mode,
        priority: String(schedule.priority),
      })
      setViewMode('edit')
    },
    [unitKey],
  )

  const handleToggleDay = useCallback((day: number) => {
    setForm((f) => ({
      ...f,
      days_of_week: f.days_of_week.includes(day)
        ? f.days_of_week.filter((d) => d !== day)
        : [...f.days_of_week, day].sort(),
    }))
  }, [])

  const handleToggleEnabled = useCallback(
    (schedule: Schedule) => {
      if (schedule.is_enabled) {
        disableSchedule.mutate(schedule.id)
      } else {
        enableSchedule.mutate(schedule.id)
      }
    },
    [enableSchedule, disableSchedule],
  )

  // Sort schedules: enabled first, then by priority (higher first), then by name
  const sortedSchedules = useMemo(() => {
    if (!schedules) return []
    return [...schedules].sort((a, b) => {
      if (a.is_enabled !== b.is_enabled) return a.is_enabled ? -1 : 1
      if (a.priority !== b.priority) return b.priority - a.priority
      return a.name.localeCompare(b.name)
    })
  }, [schedules])

  // ============================================================================
  // CREATE / EDIT VIEW
  // ============================================================================
  if (viewMode === 'create' || viewMode === 'edit') {
    const isEdit = viewMode === 'edit'
    const isPending = createSchedule.isPending || updateSchedule.isPending
    const error = createSchedule.error || updateSchedule.error

    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setViewMode('list')
              setEditingId(null)
              setForm(defaultForm)
            }}
          >
            <ArrowLeft className="mr-2 h-4 w-4" />
            Back
          </Button>
          <div>
            <p className="text-xs uppercase tracking-widest text-muted-foreground">Schedules</p>
            <h2 className="text-2xl font-semibold">{isEdit ? 'Edit Schedule' : 'Create Schedule'}</h2>
          </div>
        </div>

        <Card className="border-border/60">
          <CardContent className="space-y-5 pt-6">
            {/* Name */}
            <div>
              <label className="text-sm font-medium">Name</label>
              <Input
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="e.g. Morning Warmup"
              />
            </div>

            {/* Zone + HVAC Mode */}
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="text-sm font-medium">Zone</label>
                <select
                  value={form.zone_id}
                  onChange={(e) => setForm((f) => ({ ...f, zone_id: e.target.value }))}
                  className="flex h-11 w-full rounded-xl border border-input bg-transparent px-4 text-sm"
                >
                  <option value="">All Zones</option>
                  {(zones ?? []).map((z) => (
                    <option key={z.id} value={z.id}>
                      {z.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-sm font-medium">HVAC Mode</label>
                <select
                  value={form.hvac_mode}
                  onChange={(e) => setForm((f) => ({ ...f, hvac_mode: e.target.value }))}
                  className="flex h-11 w-full rounded-xl border border-input bg-transparent px-4 text-sm"
                >
                  {HVAC_MODES.map((m) => (
                    <option key={m.value} value={m.value}>
                      {m.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Days of Week */}
            <div>
              <label className="text-sm font-medium">Days of Week</label>
              <div className="mt-2 flex flex-wrap gap-2">
                {DAY_VALUES.map((day, idx) => (
                  <button
                    key={day}
                    type="button"
                    onClick={() => handleToggleDay(day)}
                    className={`rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors ${
                      form.days_of_week.includes(day)
                        ? 'border-primary bg-primary/10 text-primary'
                        : 'border-border/60 text-muted-foreground hover:border-border hover:text-foreground'
                    }`}
                  >
                    {DAY_LABELS[idx]}
                  </button>
                ))}
              </div>
              <div className="mt-2 flex gap-2">
                <button
                  type="button"
                  onClick={() => setForm((f) => ({ ...f, days_of_week: [0, 1, 2, 3, 4] }))}
                  className="text-xs text-muted-foreground underline hover:text-foreground"
                >
                  Weekdays
                </button>
                <button
                  type="button"
                  onClick={() => setForm((f) => ({ ...f, days_of_week: [5, 6] }))}
                  className="text-xs text-muted-foreground underline hover:text-foreground"
                >
                  Weekends
                </button>
                <button
                  type="button"
                  onClick={() => setForm((f) => ({ ...f, days_of_week: [0, 1, 2, 3, 4, 5, 6] }))}
                  className="text-xs text-muted-foreground underline hover:text-foreground"
                >
                  Every day
                </button>
              </div>
            </div>

            {/* Time + Temp + Priority */}
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <label className="text-sm font-medium">Start Time</label>
                <Input
                  type="time"
                  value={form.start_time}
                  onChange={(e) => setForm((f) => ({ ...f, start_time: e.target.value }))}
                />
              </div>
              <div>
                <label className="text-sm font-medium">
                  End Time <span className="text-muted-foreground">(optional)</span>
                </label>
                <Input
                  type="time"
                  value={form.end_time}
                  onChange={(e) => setForm((f) => ({ ...f, end_time: e.target.value }))}
                />
              </div>
              <div>
                <label className="text-sm font-medium">Target Temp ({tempUnitLabel(unitKey)})</label>
                <Input
                  type="number"
                  step="0.5"
                  value={form.target_temp}
                  onChange={(e) => setForm((f) => ({ ...f, target_temp: e.target.value }))}
                />
              </div>
              <div>
                <label className="text-sm font-medium">Priority (1-10)</label>
                <div className="flex items-center gap-3">
                  <input
                    type="range"
                    min="1"
                    max="10"
                    value={form.priority}
                    onChange={(e) => setForm((f) => ({ ...f, priority: e.target.value }))}
                    className="h-2 flex-1 cursor-pointer appearance-none rounded-lg bg-border accent-primary"
                  />
                  <span className="w-6 text-center text-sm font-semibold">{form.priority}</span>
                </div>
              </div>
            </div>

            {/* Actions */}
            <div className="flex gap-3 pt-2">
              <Button
                onClick={() => {
                  if (isEdit && editingId) {
                    updateSchedule.mutate({ id: editingId, data: form })
                  } else {
                    createSchedule.mutate(form)
                  }
                }}
                disabled={!form.name || form.days_of_week.length === 0 || !form.start_time || isPending}
              >
                {isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Check className="mr-2 h-4 w-4" />
                )}
                {isEdit ? 'Update Schedule' : 'Create Schedule'}
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  setViewMode('list')
                  setEditingId(null)
                  setForm(defaultForm)
                }}
              >
                Cancel
              </Button>
            </div>
            {error && (
              <p className="text-sm text-red-500">{error.message ?? 'An error occurred'}</p>
            )}
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
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-widest text-muted-foreground">Schedules</p>
          <h2 className="text-2xl font-semibold">Manage Schedules</h2>
        </div>
        <Button
          className="gap-2"
          onClick={() => {
            setForm({
              ...defaultForm,
              target_temp: String(Number(toDisplayTemp(22, unitKey).toFixed(1))),
            })
            setViewMode('create')
          }}
        >
          <Plus className="h-4 w-4" />
          Add Schedule
        </Button>
      </div>

      {/* Conflict Warnings */}
      {conflicts && conflicts.length > 0 && (
        <Card className="border-yellow-500/30 bg-yellow-500/5">
          <CardHeader className="flex flex-row items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-yellow-500" />
            <div>
              <CardTitle className="text-yellow-500">Schedule Conflicts</CardTitle>
              <CardDescription>
                The following schedules have overlapping times and zones.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {conflicts.map((c, i) => (
                <div
                  key={i}
                  className="rounded-lg border border-yellow-500/20 bg-yellow-500/5 px-3 py-2 text-sm"
                >
                  <span className="font-medium">{c.schedule_a_name}</span>
                  {' & '}
                  <span className="font-medium">{c.schedule_b_name}</span>
                  {c.description && (
                    <span className="text-muted-foreground"> — {c.description}</span>
                  )}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Loading State */}
      {schedulesLoading ? (
        <div className="grid gap-4">
          {[1, 2, 3].map((i) => (
            <Card key={i} className="h-36 animate-pulse border-border/60 bg-muted/20" />
          ))}
        </div>
      ) : sortedSchedules.length === 0 ? (
        /* Empty State */
        <Card className="border-dashed border-border/70 bg-card/20">
          <CardContent className="flex flex-col items-center justify-center py-16 text-center">
            <Calendar className="mb-4 h-12 w-12 text-muted-foreground/40" />
            <h3 className="text-lg font-semibold text-foreground">No schedules yet</h3>
            <p className="mt-1 max-w-sm text-sm text-muted-foreground">
              Create your first schedule to automate temperature control throughout the day.
            </p>
            <Button
              className="mt-6 gap-2"
              onClick={() => {
                setForm({
                  ...defaultForm,
                  target_temp: String(Number(toDisplayTemp(22, unitKey).toFixed(1))),
                })
                setViewMode('create')
              }}
            >
              <Plus className="h-4 w-4" />
              Create Schedule
            </Button>
          </CardContent>
        </Card>
      ) : (
        /* Schedule Cards */
        <div className="grid gap-4">
          {sortedSchedules.map((schedule) => (
            <Card
              key={schedule.id}
              className={`border-border/60 transition-opacity ${
                !schedule.is_enabled ? 'opacity-60' : ''
              }`}
            >
              <CardHeader className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0 flex-1">
                  {/* Title row */}
                  <div className="flex flex-wrap items-center gap-2">
                    <CardTitle className="truncate">{schedule.name}</CardTitle>
                    {/* HVAC mode badge */}
                    <span
                      className={`rounded-md border px-2 py-0.5 text-xs font-medium capitalize ${
                        HVAC_MODE_COLORS[schedule.hvac_mode] ?? HVAC_MODE_COLORS.auto
                      }`}
                    >
                      {schedule.hvac_mode}
                    </span>
                    {/* Priority badge */}
                    <span className="rounded-md border border-border/40 bg-muted/50 px-2 py-0.5 text-xs font-medium text-muted-foreground">
                      P{schedule.priority}
                    </span>
                    {!schedule.is_enabled && (
                      <span className="rounded-md bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                        Disabled
                      </span>
                    )}
                  </div>
                  {/* Zone */}
                  <p className="mt-1 text-sm text-muted-foreground">
                    {schedule.zone_name || 'All Zones'}
                  </p>
                </div>

                {/* Actions */}
                <div className="flex shrink-0 items-center gap-1">
                  {/* Enable/Disable toggle */}
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleToggleEnabled(schedule)}
                    disabled={enableSchedule.isPending || disableSchedule.isPending}
                    title={schedule.is_enabled ? 'Disable schedule' : 'Enable schedule'}
                  >
                    <Power
                      className={`h-4 w-4 ${
                        schedule.is_enabled ? 'text-green-500' : 'text-muted-foreground'
                      }`}
                    />
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => handleEdit(schedule)}>
                    <Pencil className="h-4 w-4" />
                  </Button>
                  {deleteConfirm === schedule.id ? (
                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-red-500"
                        onClick={() => deleteSchedule.mutate(schedule.id)}
                        disabled={deleteSchedule.isPending}
                      >
                        {deleteSchedule.isPending ? (
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
                      onClick={() => setDeleteConfirm(schedule.id)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  )}
                </div>
              </CardHeader>

              <CardContent>
                <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:gap-6">
                  {/* Days of week pills */}
                  <div className="flex flex-wrap gap-1">
                    {DAY_VALUES.map((day, idx) => (
                      <span
                        key={day}
                        className={`rounded px-2 py-0.5 text-xs font-medium ${
                          schedule.days_of_week.includes(day)
                            ? 'bg-primary/10 text-primary'
                            : 'bg-muted/30 text-muted-foreground/40'
                        }`}
                      >
                        {DAY_LABELS[idx]}
                      </span>
                    ))}
                  </div>

                  {/* Time */}
                  <div className="flex items-center gap-1.5 text-sm">
                    <Clock className="h-3.5 w-3.5 text-muted-foreground" />
                    <span>
                      {schedule.start_time}
                      {schedule.end_time ? ` - ${schedule.end_time}` : ''}
                    </span>
                  </div>

                  {/* Temperature */}
                  <div className="flex items-center gap-1.5 text-sm">
                    <Thermometer className="h-3.5 w-3.5 text-muted-foreground" />
                    <span className="font-medium">
                      {formatTemperature(schedule.target_temp_c, unitKey)}
                    </span>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
