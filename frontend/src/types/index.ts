// ============================================================================
// Zone Types (matches backend ZoneResponse)
// ============================================================================
export type ZoneType =
  | 'bedroom'
  | 'living_area'
  | 'kitchen'
  | 'bathroom'
  | 'hallway'
  | 'basement'
  | 'attic'
  | 'garage'
  | 'office'
  | 'other'

export type SensorType =
  | 'multisensor'
  | 'temp_only'
  | 'humidity_only'
  | 'presence_only'
  | 'temp_humidity'
  | 'presence_lux'
  | 'other'

export type DeviceType =
  | 'thermostat'
  | 'smart_vent'
  | 'blind'
  | 'shade'
  | 'space_heater'
  | 'fan'
  | 'mini_split'
  | 'humidifier'
  | 'dehumidifier'
  | 'other'

export type SystemMode = 'learn' | 'scheduled' | 'follow_me' | 'active'

export type Sensor = {
  id: string
  zone_id: string
  name: string
  type: SensorType
  manufacturer?: string
  model?: string
  firmware_version?: string
  config: Record<string, unknown>
  ha_entity_id?: string
  entity_id?: string
  capabilities: Record<string, unknown>
  calibration_offsets: Record<string, unknown>
  is_active: boolean
  last_seen?: string
  created_at: string
}

export type Device = {
  id: string
  zone_id: string
  name: string
  type: DeviceType
  manufacturer?: string
  model?: string
  ha_entity_id?: string
  control_method: 'ha_service_call'
  capabilities: Record<string, unknown>
  constraints: Record<string, unknown>
  is_primary: boolean
  created_at: string
}

// Raw backend zone shape (matches backend ZoneResponse)
export type ZoneBackend = {
  id: string
  name: string
  description?: string
  type: ZoneType
  floor?: number
  is_active: boolean
  comfort_preferences: Record<string, unknown>
  thermal_profile: Record<string, unknown>
  created_at: string
  updated_at: string
  sensors: Sensor[]
  devices: Device[]
  current_temp?: number | null
  current_humidity?: number | null
  is_occupied?: boolean | null
  target_temp?: number | null
}

// Frontend-friendly zone (used by ZoneCard and Dashboard)
export type Zone = {
  id: string
  name: string
  description?: string
  type?: ZoneType
  floor?: number
  is_active?: boolean
  temperature: number
  humidity: number
  occupancy: 'occupied' | 'vacant'
  targetTemperature: number
  sensors?: Sensor[]
  devices?: Device[]
}

export type SensorReading = {
  id: string
  sensor_id: string
  recorded_at: string
  temperature_c?: number
  humidity?: number
  presence?: boolean
  lux?: number
  payload: Record<string, unknown>
}

export type DeviceAction = {
  id: string
  device_id: string
  triggered_by: string
  action_type: string
  parameters: Record<string, unknown>
  result?: Record<string, unknown>
  created_at: string
}

// ============================================================================
// API Response Types
// ============================================================================
export type ApiResponse<T> = {
  data: T
  meta?: {
    total?: number
    page?: number
    pageSize?: number
  }
}

export type ZonesResponse = Zone[]
export type SensorsResponse = ApiResponse<Sensor[]>
export type DevicesResponse = ApiResponse<Device[]>
export type SettingsResponse = ApiResponse<Record<string, unknown>>

// ============================================================================
// WebSocket Types
// ============================================================================
export type WSMessage =
  | { type: 'zone_update'; data: Record<string, unknown>[]; timestamp: string }
  | { type: 'sensor_update'; sensor_id: string; data: Record<string, unknown>; timestamp: string }
  | { type: 'device_state'; device_id: string; state: Record<string, unknown>; timestamp: string }
  | { type: 'alert'; payload: { message: string; level: 'info' | 'warning' | 'critical' } }

// ============================================================================
// Analytics Types
// ============================================================================
export type ReadingPoint = {
  recorded_at: string
  temperature_c?: number
  humidity?: number
  presence?: boolean
  lux?: number
  sensor_id: string
}

export type HistoryResponse = {
  zone_id: string
  zone_name: string
  period_start: string
  period_end: string
  total_readings: number
  avg_temperature_c?: number
  min_temperature_c?: number
  max_temperature_c?: number
  avg_humidity?: number
  min_humidity?: number
  max_humidity?: number
  readings: ReadingPoint[]
}

export type EnergyZoneEstimate = {
  zone_id: string
  zone_name: string
  device_count: number
  action_count: number
  estimated_kwh: number
  estimated_cost_usd: number
  primary_device_type?: string
}

export type EnergyResponse = {
  period_start: string
  period_end: string
  total_estimated_kwh: number
  total_estimated_cost_usd: number
  cost_per_kwh: number
  zones: EnergyZoneEstimate[]
  estimation_note: string
}

export type ComfortZoneScore = {
  zone_id: string
  zone_name: string
  score: number
  avg_temperature_c?: number
  avg_humidity?: number
  temp_in_range_pct: number
  humidity_in_range_pct: number
  reading_count: number
  factors: Record<string, unknown>
}

export type ComfortResponse = {
  period_start: string
  period_end: string
  overall_score: number
  zones: ComfortZoneScore[]
}

// ============================================================================
// Chat Types
// ============================================================================
export type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: Date
  actions?: Array<{ id: string; function: { name: string; arguments: string } }>
}

export type ChatResponse = {
  message: string
  session_id: string
  actions_taken: Array<{ id: string; function: { name: string; arguments: string } }>
  suggestions: string[]
  metadata: Record<string, unknown>
  timestamp: string
}

export type ConversationHistoryItem = {
  id: string
  session_id: string
  user_message: string
  assistant_response: string
  created_at: string
  metadata: Record<string, unknown>
}

// ============================================================================
// Settings Types
// ============================================================================
export type WeatherEntity = {
  entity_id: string
  name: string
  state: string
}

export type HAEntity = {
  entity_id: string
  name: string
  state: string
  domain: string
}

export type SystemSettings = {
  system_name: string
  current_mode: SystemMode
  timezone: string
  temperature_unit: string
  default_comfort_temp_min: number
  default_comfort_temp_max: number
  default_humidity_min: number
  default_humidity_max: number
  energy_cost_per_kwh: number
  currency: string
  weather_entity: string
  climate_entities: string
  sensor_entities: string

  home_assistant_url: string
  home_assistant_token: string
  llm_settings: Record<string, unknown>
  default_schedule?: Record<string, unknown>
  last_synced_at?: string
}

export type LLMModelInfo = {
  id: string
  display_name?: string
  context_length?: number
}

export type LLMProviderInfo = {
  provider: string
  configured: boolean
  models: LLMModelInfo[]
}

export type LLMProvidersResponse = {
  providers: LLMProviderInfo[]
}

// ============================================================================
// Schedule Types
// ============================================================================
export type Schedule = {
  id: string
  name: string
  zone_id?: string
  zone_name?: string
  days_of_week: number[]
  start_time: string
  end_time?: string
  target_temp_c: number
  hvac_mode: string
  is_enabled: boolean
  priority: number
  created_at: string
  updated_at: string
}

export type UpcomingSchedule = {
  schedule_id: string
  schedule_name: string
  zone_id?: string
  zone_name?: string
  start_time: string
  end_time?: string
  target_temp_c: number
  hvac_mode: string
}
