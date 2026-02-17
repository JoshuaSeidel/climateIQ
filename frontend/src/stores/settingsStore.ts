import { create } from 'zustand'

export type TemperatureUnit = 'celsius' | 'fahrenheit'
export type FanMode = 'auto' | 'on'

type SettingsState = {
  temperatureUnit: TemperatureUnit
  notifications: boolean
  fanMode: FanMode
  comfortRange: { min: number; max: number }
  updateSettings: (updates: Partial<SettingsState>) => void
  /** Hydrate the store from backend settings (call when settings are fetched). */
  hydrate: (backend: {
    temperature_unit?: string
    default_comfort_temp_min?: number
    default_comfort_temp_max?: number
  }) => void
}

export const useSettingsStore = create<SettingsState>((set) => ({
  temperatureUnit: 'celsius',
  notifications: true,
  fanMode: 'auto',
  comfortRange: { min: 20, max: 24 },
  updateSettings: (updates) =>
    set((state) => ({
      ...state,
      ...updates,
      comfortRange: updates.comfortRange ?? state.comfortRange,
    })),
  hydrate: (backend) =>
    set((state) => ({
      ...state,
      temperatureUnit:
        backend.temperature_unit === 'F' || backend.temperature_unit === 'fahrenheit'
          ? 'fahrenheit'
          : 'celsius',
      comfortRange: {
        min: backend.default_comfort_temp_min ?? state.comfortRange.min,
        max: backend.default_comfort_temp_max ?? state.comfortRange.max,
      },
    })),
}))
