import { useState, useCallback, useEffect, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { api, BASE_PATH } from '@/lib/api'
import type { SystemSettings, SystemMode, LLMProvidersResponse, WeatherEntity } from '@/types'
import { useSettingsStore } from '@/stores/settingsStore'
import {
  Bot,
  RefreshCw,
  Check,
  AlertCircle,
  Thermometer,
  Home,
  Wifi,
  Settings2,
  Loader2,
  Globe,
  Database,
  Download,
  Upload,
  Info,
  Zap,
  Brain,
  Eye,
  EyeOff,
} from 'lucide-react'

type SettingsTab = 'general' | 'mqtt' | 'homeassistant' | 'llm' | 'modes' | 'backup' | 'about'

const TABS: { id: SettingsTab; label: string; icon: React.ElementType }[] = [
  { id: 'general', label: 'General', icon: Settings2 },
  { id: 'mqtt', label: 'MQTT', icon: Wifi },
  { id: 'homeassistant', label: 'Home Assistant', icon: Home },
  { id: 'llm', label: 'LLM Providers', icon: Bot },
  { id: 'modes', label: 'Modes', icon: Zap },
  { id: 'backup', label: 'Backup', icon: Database },
  { id: 'about', label: 'About', icon: Info },
]

const MODE_DESCRIPTIONS: Record<SystemMode, string> = {
  learn: 'System observes occupancy patterns and temperature preferences without making changes.',
  scheduled: 'System follows configured schedules to control HVAC devices.',
  follow_me: 'System tracks occupancy and adjusts climate in occupied zones automatically.',
  active: 'Full AI-driven control. The LLM makes decisions based on all available data.',
}

export const Settings = () => {
  const [activeTab, setActiveTab] = useState<SettingsTab>('general')

  // Fetch system settings
  const { data: settings, isLoading: settingsLoading } = useQuery<SystemSettings>({
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

  // Fetch LLM providers
  const {
    data: llmProviders,
    isLoading: providersLoading,
    refetch: refetchProviders,
  } = useQuery<LLMProvidersResponse>({
    queryKey: ['llm-providers'],
    queryFn: () => api.get<LLMProvidersResponse>('/settings/llm/providers'),
    enabled: activeTab === 'llm',
  })

  // Fetch system health
  const { data: healthData } = useQuery({
    queryKey: ['system-health'],
    queryFn: () => api.get<{ status: string }>('/system/health'),
    enabled: activeTab === 'about',
  })

  // Fetch version
  const { data: versionData } = useQuery({
    queryKey: ['system-version'],
    queryFn: () => api.get<{ name: string; version: string }>('/system/version'),
    enabled: activeTab === 'about',
  })

  return (
    <div className="space-y-6">
      <div>
        <p className="text-xs uppercase tracking-widest text-muted-foreground">Settings</p>
        <h2 className="text-2xl font-semibold">System Preferences</h2>
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
            <span className="hidden sm:inline">{tab.label}</span>
          </Button>
        ))}
      </div>

      {/* Tab Content */}
      {activeTab === 'general' && <GeneralTab key={settings ? 'loaded' : 'loading'} settings={settings} loading={settingsLoading} />}
      {activeTab === 'mqtt' && <MQTTTab key={settings ? 'loaded' : 'loading'} settings={settings} />}
      {activeTab === 'homeassistant' && <HomeAssistantTab key={settings ? 'loaded' : 'loading'} settings={settings} />}
      {activeTab === 'llm' && (
        <LLMTab
          providers={llmProviders}
          loading={providersLoading}
          onRefresh={() => refetchProviders()}
        />
      )}
      {activeTab === 'modes' && <ModesTab settings={settings} />}
      {activeTab === 'backup' && <BackupTab />}
      {activeTab === 'about' && <AboutTab health={healthData} version={versionData} />}
    </div>
  )
}

// ============================================================================
// General Tab
// ============================================================================
function GeneralTab({ settings, loading }: { settings?: SystemSettings; loading: boolean }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState({
    system_name: settings?.system_name ?? 'ClimateIQ',
    timezone: settings?.timezone ?? 'UTC',
    temperature_unit: settings?.temperature_unit ?? 'C',
    default_comfort_temp_min: String(settings?.default_comfort_temp_min ?? 20),
    default_comfort_temp_max: String(settings?.default_comfort_temp_max ?? 24),
    default_humidity_min: String(settings?.default_humidity_min ?? 30),
    default_humidity_max: String(settings?.default_humidity_max ?? 60),
    energy_cost_per_kwh: String(settings?.energy_cost_per_kwh ?? 0.12),
    currency: settings?.currency ?? 'USD',
  })

  const updateSettings = useMutation({
    mutationFn: (data: Record<string, unknown>) => api.put<SystemSettings>('/settings', data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
  })

  const validationError = useMemo(() => {
    const tempMin = Number(form.default_comfort_temp_min)
    const tempMax = Number(form.default_comfort_temp_max)
    const humMin = Number(form.default_humidity_min)
    const humMax = Number(form.default_humidity_max)
    if (isNaN(tempMin) || isNaN(tempMax) || tempMin >= tempMax) {
      return 'Min temperature must be less than max temperature.'
    }
    if (isNaN(humMin) || isNaN(humMax) || humMin >= humMax) {
      return 'Min humidity must be less than max humidity.'
    }
    return null
  }, [form.default_comfort_temp_min, form.default_comfort_temp_max, form.default_humidity_min, form.default_humidity_max])

  const handleSave = () => {
    if (validationError) return
    updateSettings.mutate({
      system_name: form.system_name,
      timezone: form.timezone,
      temperature_unit: form.temperature_unit,
      default_comfort_temp_min: Number(form.default_comfort_temp_min),
      default_comfort_temp_max: Number(form.default_comfort_temp_max),
      default_humidity_min: Number(form.default_humidity_min),
      default_humidity_max: Number(form.default_humidity_max),
      energy_cost_per_kwh: Number(form.energy_cost_per_kwh),
      currency: form.currency,
    })
  }

  if (loading) {
    return <Card className="h-64 animate-pulse border-border/60 bg-muted/20" />
  }

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card className="border-border/60">
        <CardHeader>
          <div className="flex items-center gap-2">
            <Globe className="h-5 w-5 text-primary" />
            <CardTitle>System</CardTitle>
          </div>
          <CardDescription>General system configuration</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <label className="text-sm font-medium">System Name</label>
            <Input
              value={form.system_name}
              onChange={(e) => setForm((f) => ({ ...f, system_name: e.target.value }))}
            />
          </div>
          <div>
            <label className="text-sm font-medium">Timezone</label>
            <Input
              value={form.timezone}
              onChange={(e) => setForm((f) => ({ ...f, timezone: e.target.value }))}
              placeholder="e.g. America/New_York"
            />
          </div>
          <div>
            <label className="text-sm font-medium">Temperature Unit</label>
            <div className="flex gap-2">
              <Button
                variant={form.temperature_unit === 'C' ? 'default' : 'outline'}
                size="sm"
                onClick={() => setForm((f) => ({ ...f, temperature_unit: 'C' }))}
              >
                Celsius (°C)
              </Button>
              <Button
                variant={form.temperature_unit === 'F' ? 'default' : 'outline'}
                size="sm"
                onClick={() => setForm((f) => ({ ...f, temperature_unit: 'F' }))}
              >
                Fahrenheit (°F)
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card className="border-border/60">
        <CardHeader>
          <div className="flex items-center gap-2">
            <Thermometer className="h-5 w-5 text-primary" />
            <CardTitle>Comfort & Energy</CardTitle>
          </div>
          <CardDescription>Default comfort ranges and energy settings</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="text-sm font-medium">Min Temp (°C)</label>
              <Input
                type="number"
                step="0.5"
                value={form.default_comfort_temp_min}
                onChange={(e) => setForm((f) => ({ ...f, default_comfort_temp_min: e.target.value }))}
              />
            </div>
            <div>
              <label className="text-sm font-medium">Max Temp (°C)</label>
              <Input
                type="number"
                step="0.5"
                value={form.default_comfort_temp_max}
                onChange={(e) => setForm((f) => ({ ...f, default_comfort_temp_max: e.target.value }))}
              />
            </div>
            <div>
              <label className="text-sm font-medium">Min Humidity (%)</label>
              <Input
                type="number"
                value={form.default_humidity_min}
                onChange={(e) => setForm((f) => ({ ...f, default_humidity_min: e.target.value }))}
              />
            </div>
            <div>
              <label className="text-sm font-medium">Max Humidity (%)</label>
              <Input
                type="number"
                value={form.default_humidity_max}
                onChange={(e) => setForm((f) => ({ ...f, default_humidity_max: e.target.value }))}
              />
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="text-sm font-medium">Energy Cost (per kWh)</label>
              <Input
                type="number"
                step="0.01"
                value={form.energy_cost_per_kwh}
                onChange={(e) => setForm((f) => ({ ...f, energy_cost_per_kwh: e.target.value }))}
              />
            </div>
            <div>
              <label className="text-sm font-medium">Currency</label>
              <Input
                value={form.currency}
                onChange={(e) => setForm((f) => ({ ...f, currency: e.target.value }))}
              />
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="lg:col-span-2">
        <Button onClick={handleSave} disabled={updateSettings.isPending || !!validationError}>
          {updateSettings.isPending ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Check className="mr-2 h-4 w-4" />
          )}
          Save Settings
        </Button>
        {validationError && (
          <span className="ml-3 text-sm text-red-500">{validationError}</span>
        )}
        {updateSettings.isSuccess && !validationError && (
          <span className="ml-3 text-sm text-green-600">Settings saved successfully</span>
        )}
        {updateSettings.isError && (
          <span className="ml-3 text-sm text-red-500">
            Failed to save: {updateSettings.error?.message}
          </span>
        )}
      </div>
    </div>
  )
}

// ============================================================================
// MQTT Tab
// ============================================================================
function MQTTTab({ settings }: { settings?: SystemSettings }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState({
    broker: settings?.mqtt_broker ?? '',
    port: String(settings?.mqtt_port ?? 1883),
    username: settings?.mqtt_username ?? '',
    password: settings?.mqtt_password ?? '',
    use_tls: settings?.mqtt_use_tls ?? false,
  })
  const [showPassword, setShowPassword] = useState(false)
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null)

  const saveMqttConfig = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      api.put('/settings', {
        mqtt_broker: data.broker,
        mqtt_port: data.port,
        mqtt_username: data.username,
        mqtt_password: data.password,
        mqtt_use_tls: data.use_tls,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
  })

  const handleSave = () => {
    saveMqttConfig.mutate({
      broker: form.broker,
      port: Number(form.port),
      username: form.username,
      password: form.password,
      use_tls: form.use_tls,
    })
  }

  const testConnection = useMutation({
    mutationFn: async () => {
      // Trigger sensor discovery as a connection test
      return api.post<{ broker: string; discovered_count: number }>('/sensors/discover')
    },
    onSuccess: (data) => {
      setTestResult({
        success: true,
        message: `Connected to ${data.broker}. Found ${data.discovered_count} devices.`,
      })
    },
    onError: (error) => {
      setTestResult({ success: false, message: error.message })
    },
  })

  return (
    <Card className="border-border/60">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Wifi className="h-5 w-5 text-purple-500" />
          <CardTitle>MQTT Broker</CardTitle>
        </div>
        <CardDescription>
          Configure the MQTT broker for real-time sensor communication. Settings are configured via
          environment variables (CLIMATEIQ_MQTT_BROKER, etc.)
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="text-sm font-medium">Broker Host</label>
            <Input
              value={form.broker}
              onChange={(e) => setForm((f) => ({ ...f, broker: e.target.value }))}
              placeholder="e.g. 192.168.1.100"
            />
          </div>
          <div>
            <label className="text-sm font-medium">Port</label>
            <Input
              type="number"
              value={form.port}
              onChange={(e) => setForm((f) => ({ ...f, port: e.target.value }))}
            />
          </div>
          <div>
            <label className="text-sm font-medium">Username</label>
            <Input
              value={form.username}
              onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))}
              placeholder="Optional"
            />
          </div>
          <div>
            <label className="text-sm font-medium">Password</label>
            <div className="flex gap-2">
              <Input
                type={showPassword ? 'text' : 'password'}
                value={form.password}
                onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
                placeholder="Optional"
              />
              <Button variant="outline" size="icon" onClick={() => setShowPassword(!showPassword)}>
                {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </Button>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <input
            type="checkbox"
            checked={form.use_tls}
            onChange={(e) => setForm((f) => ({ ...f, use_tls: e.target.checked }))}
            className="h-4 w-4 rounded border-border"
          />
          <label className="text-sm font-medium">Use TLS</label>
        </div>

        <div className="flex items-center gap-3">
          <Button onClick={handleSave} disabled={saveMqttConfig.isPending}>
            {saveMqttConfig.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Check className="mr-2 h-4 w-4" />
            )}
            Save MQTT Settings
          </Button>
          <Button
            variant="outline"
            onClick={() => testConnection.mutate()}
            disabled={testConnection.isPending}
          >
            {testConnection.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Wifi className="mr-2 h-4 w-4" />
            )}
            Test Connection
          </Button>
        </div>
        {saveMqttConfig.isSuccess && (
          <span className="text-sm text-green-600">MQTT settings saved successfully</span>
        )}
        {saveMqttConfig.isError && (
          <span className="text-sm text-red-500">
            Failed to save: {saveMqttConfig.error?.message}
          </span>
        )}
        {testResult && (
          <div className={`flex items-center gap-2 text-sm ${testResult.success ? 'text-green-600' : 'text-red-500'}`}>
            {testResult.success ? (
              <Check className="h-4 w-4" />
            ) : (
              <AlertCircle className="h-4 w-4" />
            )}
            {testResult.message}
          </div>
        )}

        <p className="text-xs text-muted-foreground">
          Runtime overrides are saved here. Environment variables take precedence if set.
        </p>
      </CardContent>
    </Card>
  )
}

// ============================================================================
// Home Assistant Tab
// ============================================================================
function HomeAssistantTab({ settings }: { settings?: SystemSettings }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState({
    url: settings?.home_assistant_url ?? '',
    token: settings?.home_assistant_token ?? '',
  })
  const [showToken, setShowToken] = useState(false)
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null)

  const saveHaConfig = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      api.put('/settings', {
        home_assistant_url: data.url,
        home_assistant_token: data.token,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
  })

  const handleSave = () => {
    saveHaConfig.mutate({
      url: form.url,
      token: form.token,
    })
  }

  // Fetch available weather entities from HA
  const { data: weatherEntities } = useQuery<WeatherEntity[]>({
    queryKey: ['weather-entities'],
    queryFn: () => api.get<WeatherEntity[]>('/weather/entities'),
  })

  const testConnection = useMutation({
    mutationFn: async () => {
      return api.post<{ connected: boolean; url: string; error?: string; entity_check?: string }>(
        '/system/test-ha'
      )
    },
    onSuccess: (data) => {
      if (data.connected) {
        setTestResult({
          success: true,
          message: `Connected to Home Assistant at ${data.url}.`,
        })
      } else {
        setTestResult({
          success: false,
          message: data.error ?? 'Connection failed. Check URL and token.',
        })
      }
    },
    onError: (error) => {
      setTestResult({ success: false, message: error.message })
    },
  })

  const updateWeatherEntity = useMutation({
    mutationFn: (entity: string) => api.put('/settings', { weather_entity: entity }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
  })

  return (
    <Card className="border-border/60">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Home className="h-5 w-5 text-blue-500" />
          <CardTitle>Home Assistant</CardTitle>
        </div>
        <CardDescription>
          Connect to Home Assistant for device control. Settings are configured via environment
          variables (CLIMATEIQ_HA_URL, CLIMATEIQ_HA_TOKEN).
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <label className="text-sm font-medium">Home Assistant URL</label>
          <Input
            value={form.url}
            onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
            placeholder="e.g. http://homeassistant.local:8123"
          />
        </div>
        <div>
          <label className="text-sm font-medium">Long-Lived Access Token</label>
          <div className="flex gap-2">
            <Input
              type={showToken ? 'text' : 'password'}
              value={form.token}
              onChange={(e) => setForm((f) => ({ ...f, token: e.target.value }))}
              placeholder="Enter your HA access token"
              className="flex-1"
            />
            <Button variant="outline" size="icon" onClick={() => setShowToken(!showToken)}>
              {showToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </Button>
          </div>
        </div>

        <div>
          <label className="text-sm font-medium">Weather Entity</label>
          <select
            value={settings?.weather_entity ?? ''}
            onChange={(e) => updateWeatherEntity.mutate(e.target.value)}
            className="flex h-11 w-full rounded-xl border border-input bg-transparent px-4 text-sm"
          >
            <option value="">Select a weather entity...</option>
            {weatherEntities?.map((entity) => (
              <option key={entity.entity_id} value={entity.entity_id}>
                {entity.name} ({entity.state})
              </option>
            ))}
          </select>
          <p className="mt-1 text-xs text-muted-foreground">
            Select which Home Assistant weather entity to use for weather data
          </p>
          {updateWeatherEntity.isSuccess && (
            <p className="mt-1 text-xs text-green-600">Weather entity updated</p>
          )}
          {updateWeatherEntity.isError && (
            <p className="mt-1 text-xs text-red-500">
              Failed to update: {updateWeatherEntity.error?.message}
            </p>
          )}
        </div>

        <div className="flex items-center gap-3">
          <Button onClick={handleSave} disabled={saveHaConfig.isPending}>
            {saveHaConfig.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Check className="mr-2 h-4 w-4" />
            )}
            Save HA Settings
          </Button>
          <Button
            variant="outline"
            onClick={() => testConnection.mutate()}
            disabled={testConnection.isPending}
          >
            {testConnection.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Home className="mr-2 h-4 w-4" />
            )}
            Test Connection
          </Button>
        </div>
        {saveHaConfig.isSuccess && (
          <span className="text-sm text-green-600">Home Assistant settings saved successfully</span>
        )}
        {saveHaConfig.isError && (
          <span className="text-sm text-red-500">
            Failed to save: {saveHaConfig.error?.message}
          </span>
        )}
        {testResult && (
          <div className={`flex items-center gap-2 text-sm ${testResult.success ? 'text-green-600' : 'text-red-500'}`}>
            {testResult.success ? (
              <Check className="h-4 w-4" />
            ) : (
              <AlertCircle className="h-4 w-4" />
            )}
            {testResult.message}
          </div>
        )}

        <p className="text-xs text-muted-foreground">
          Runtime overrides are saved here. Environment variables take precedence if set.
          Create a long-lived access token in HA under Profile &gt; Security.
        </p>
      </CardContent>
    </Card>
  )
}

// ============================================================================
// LLM Providers Tab
// ============================================================================
function LLMTab({
  providers,
  loading,
  onRefresh,
}: {
  providers?: LLMProvidersResponse
  loading: boolean
  onRefresh: () => void
}) {
  const queryClient = useQueryClient()
  const [selectedProvider, setSelectedProvider] = useState<string>('anthropic')
  const [selectedModels, setSelectedModels] = useState<Record<string, string>>({})

  // Save LLM config
  const saveLLMConfig = useMutation({
    mutationFn: async (config: { provider: string; model?: string }) => {
      return api.put('/system/config/llm', {
        provider: config.provider,
        model: config.model || 'gpt-4o-mini',
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llm-providers'] })
    },
  })

  // Refresh models for a provider
  const refreshModels = useMutation({
    mutationFn: (provider: string) =>
      api.post<{ provider: string; model_count: number }>(`/settings/llm/providers/${provider}/refresh`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llm-providers'] })
    },
  })

  const currentProvider = providers?.providers?.find((p) => p.provider === selectedProvider)

  return (
    <div className="space-y-6">
      <Card className="border-border/60">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Bot className="h-5 w-5 text-primary" />
              <CardTitle>AI Providers</CardTitle>
            </div>
            <Button variant="ghost" size="sm" onClick={onRefresh} disabled={loading}>
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            </Button>
          </div>
          <CardDescription>Configure LLM providers for the AI assistant</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Provider Selection */}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
            {(providers?.providers ?? []).map((provider) => (
              <button
                key={provider.provider}
                onClick={() => setSelectedProvider(provider.provider)}
                className={`rounded-lg border p-3 text-left text-sm transition-colors ${
                  selectedProvider === provider.provider
                    ? 'border-primary bg-primary/5'
                    : 'border-border/60 hover:border-border'
                }`}
              >
                <div className="font-medium capitalize">{provider.provider}</div>
                <div className="mt-1 flex items-center gap-1">
                  {provider.configured ? (
                    <>
                      <div className="h-1.5 w-1.5 rounded-full bg-green-500" />
                      <span className="text-xs text-green-600">Active</span>
                    </>
                  ) : (
                    <>
                      <div className="h-1.5 w-1.5 rounded-full bg-muted" />
                      <span className="text-xs text-muted-foreground">Not set</span>
                    </>
                  )}
                </div>
                {provider.models.length > 0 && (
                  <div className="mt-1 text-xs text-muted-foreground">
                    {provider.models.length} models
                  </div>
                )}
              </button>
            ))}
            {!providers?.providers?.length && !loading && (
              <p className="col-span-full text-sm text-muted-foreground">
                No providers available. Check backend configuration.
              </p>
            )}
          </div>

          {/* Model Selection */}
          {selectedProvider && (
            <div className="space-y-3 rounded-lg border border-border/60 p-4">
              <h4 className="font-medium capitalize">{selectedProvider} Configuration</h4>
              <div className="flex items-center justify-between gap-2">
                <div className="text-sm text-muted-foreground">
                  Choose the model used for this provider.
                </div>
                <Button
                  onClick={() => {
                    saveLLMConfig.mutate({
                      provider: selectedProvider,
                      model: selectedModels[selectedProvider] || undefined,
                    })
                  }}
                  disabled={saveLLMConfig.isPending}
                >
                  {saveLLMConfig.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    'Save'
                  )}
                </Button>
              </div>

              {/* Refresh Models */}
              <Button
                variant="outline"
                size="sm"
                onClick={() => refreshModels.mutate(selectedProvider)}
                disabled={refreshModels.isPending}
              >
                {refreshModels.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="mr-2 h-4 w-4" />
                )}
                Refresh Models
              </Button>

              {/* Available Models */}
              {currentProvider && currentProvider.models.length > 0 && (
                <div className="space-y-2">
                  <label className="text-sm font-medium">
                    Available Models ({currentProvider.models.length})
                  </label>
                  <div className="max-h-48 space-y-1 overflow-y-auto rounded-lg border border-border/60 p-2">
                    {currentProvider.models.map((model) => (
                      <button
                        key={model.id}
                        onClick={() =>
                          setSelectedModels((prev) => ({ ...prev, [selectedProvider]: model.id }))
                        }
                        className={`flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-sm transition-colors ${
                          selectedModels[selectedProvider] === model.id
                            ? 'bg-primary/10 text-primary'
                            : 'hover:bg-muted/50'
                        }`}
                      >
                        <span>{model.display_name || model.id}</span>
                        {model.context_length && (
                          <span className="text-xs text-muted-foreground">
                            {(model.context_length / 1000).toFixed(0)}k ctx
                          </span>
                        )}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {saveLLMConfig.isSuccess && (
                <p className="text-sm text-green-600">Configuration saved successfully</p>
              )}
              {saveLLMConfig.isError && (
                  <p className="text-sm text-red-500">
                    Failed to save: {saveLLMConfig.error?.message}
                  </p>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

// ============================================================================
// Modes Tab
// ============================================================================
function ModesTab({ settings }: { settings?: SystemSettings }) {
  const queryClient = useQueryClient()
  const currentMode = settings?.current_mode ?? 'learn'

  const changeMode = useMutation({
    mutationFn: (mode: SystemMode) => api.post('/system/mode', { mode }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
  })

  return (
    <div className="space-y-4">
      <Card className="border-border/60">
        <CardHeader>
          <div className="flex items-center gap-2">
            <Brain className="h-5 w-5 text-primary" />
            <CardTitle>System Modes</CardTitle>
          </div>
          <CardDescription>
            Configure how ClimateIQ controls your HVAC system. Current mode:{' '}
            <span className="font-medium capitalize">{currentMode.replace('_', ' ')}</span>
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {(Object.entries(MODE_DESCRIPTIONS) as [SystemMode, string][]).map(([mode, description]) => (
            <div
              key={mode}
              className={`flex items-start gap-4 rounded-lg border p-4 transition-colors ${
                currentMode === mode
                  ? 'border-primary bg-primary/5'
                  : 'border-border/60 hover:border-border'
              }`}
            >
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <h4 className="font-medium capitalize">{mode.replace('_', ' ')}</h4>
                  {currentMode === mode && (
                    <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                      Active
                    </span>
                  )}
                </div>
                <p className="mt-1 text-sm text-muted-foreground">{description}</p>
              </div>
              <Button
                variant={currentMode === mode ? 'default' : 'outline'}
                size="sm"
                onClick={() => changeMode.mutate(mode)}
                disabled={changeMode.isPending || currentMode === mode}
              >
                {changeMode.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : currentMode === mode ? (
                  'Active'
                ) : (
                  'Activate'
                )}
              </Button>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  )
}

// ============================================================================
// Backup Tab
// ============================================================================
function BackupTab() {
  const queryClient = useQueryClient()
  const [restoreMessage, setRestoreMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const { data: backups } = useQuery<
    { backup_id: string; filename: string; created_at: string; size_bytes: number }[]
  >({
    queryKey: ['backups'],
    queryFn: () => api.get('/backup'),
  })

  const createBackup = useMutation({
    mutationFn: async () => {
      const response = await api.post<{ backup_id: string; message: string }>('/backup/export')
      return response
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['backups'] })
    },
  })

  const handleRestore = useCallback(async () => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.json'
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0]
      if (!file) return
      try {
        const formData = new FormData()
        formData.append('file', file)
        const response = await fetch(`${BASE_PATH}/api/v1/backup/import`, {
          method: 'POST',
          body: formData,
        })
        if (!response.ok) {
          const message = await response.text()
          throw new Error(message || 'Failed to restore backup')
        }
        setRestoreMessage({ type: 'success', text: 'Backup restored successfully.' })
        queryClient.invalidateQueries()
      } catch {
        setRestoreMessage({ type: 'error', text: 'Failed to restore backup. Invalid file format.' })
      }
    }
    input.click()
  }, [queryClient])

  return (
    <div className="space-y-6">
      <Card className="border-border/60">
        <CardHeader>
          <div className="flex items-center gap-2">
            <Database className="h-5 w-5 text-primary" />
            <CardTitle>Backup & Restore</CardTitle>
          </div>
          <CardDescription>Create backups of your settings and restore from previous backups</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex gap-3">
            <Button onClick={() => createBackup.mutate()} disabled={createBackup.isPending}>
              {createBackup.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Download className="mr-2 h-4 w-4" />
              )}
              Create Backup
            </Button>
            <Button variant="outline" onClick={handleRestore}>
              <Upload className="mr-2 h-4 w-4" />
              Restore from File
            </Button>
          </div>

          {restoreMessage && (
            <div
              className={`flex items-center gap-2 rounded-lg border p-3 text-sm ${
                restoreMessage.type === 'success'
                  ? 'border-green-200 bg-green-50 text-green-700 dark:border-green-800 dark:bg-green-950 dark:text-green-400'
                  : 'border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-400'
              }`}
            >
              {restoreMessage.type === 'success' ? (
                <Check className="h-4 w-4" />
              ) : (
                <AlertCircle className="h-4 w-4" />
              )}
              {restoreMessage.text}
              <button
                className="ml-auto text-xs underline"
                onClick={() => setRestoreMessage(null)}
              >
                Dismiss
              </button>
            </div>
          )}

          {(backups ?? []).length > 0 && (
            <div className="space-y-2">
              <h4 className="text-sm font-medium">Recent Backups</h4>
              {(backups ?? []).map((backup) => (
                <div
                  key={backup.backup_id}
                  className="flex items-center justify-between rounded-lg border border-border/40 p-3"
                >
                  <div>
                    <p className="text-sm font-medium">{backup.filename}</p>
                    <p className="text-xs text-muted-foreground">
                      {new Date(backup.created_at).toLocaleString()} - {(backup.size_bytes / 1024).toFixed(1)} KB
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

// ============================================================================
// About Tab
// ============================================================================
function AboutTab({
  health,
  version,
}: {
  health?: { status: string }
  version?: { name: string; version: string }
}) {
  return (
    <div className="space-y-6">
      <Card className="border-border/60">
        <CardHeader>
          <div className="flex items-center gap-2">
            <Info className="h-5 w-5 text-primary" />
            <CardTitle>About ClimateIQ</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="rounded-lg border border-border/60 p-4">
              <p className="text-sm text-muted-foreground">Application</p>
              <p className="text-lg font-semibold">{version?.name ?? 'ClimateIQ'}</p>
            </div>
            <div className="rounded-lg border border-border/60 p-4">
              <p className="text-sm text-muted-foreground">Version</p>
              <p className="text-lg font-semibold">{version?.version ?? '0.1.0'}</p>
            </div>
            <div className="rounded-lg border border-border/60 p-4">
              <p className="text-sm text-muted-foreground">System Health</p>
              <div className="flex items-center gap-2">
                {health?.status === 'ok' ? (
                  <>
                    <div className="h-2 w-2 rounded-full bg-green-500" />
                    <p className="text-lg font-semibold text-green-600">Healthy</p>
                  </>
                ) : (
                  <>
                    <div className="h-2 w-2 rounded-full bg-yellow-500" />
                    <p className="text-lg font-semibold text-yellow-600">
                      {health?.status ?? 'Unknown'}
                    </p>
                  </>
                )}
              </div>
            </div>
            <div className="rounded-lg border border-border/60 p-4">
              <p className="text-sm text-muted-foreground">Frontend</p>
              <p className="text-lg font-semibold">React + Vite</p>
            </div>
          </div>

          <div className="rounded-lg border border-border/60 p-4">
            <p className="text-sm text-muted-foreground">Description</p>
            <p className="mt-1 text-sm">
              ClimateIQ is an intelligent HVAC zone management system that uses AI to optimize home
              comfort and energy efficiency. It integrates with Home Assistant and MQTT-based sensors
              to provide real-time monitoring and automated climate control.
            </p>
          </div>

          <div className="rounded-lg border border-border/60 p-4">
            <p className="text-sm font-medium">Tech Stack</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {[
                'FastAPI',
                'PostgreSQL',
                'Redis',
                'React',
                'TanStack Query',
                'Recharts',
                'Tailwind CSS',
                'MQTT',
                'Home Assistant',
                'LLM Integration',
              ].map((tech) => (
                <span
                  key={tech}
                  className="rounded-full border border-border/60 px-2 py-0.5 text-xs"
                >
                  {tech}
                </span>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
