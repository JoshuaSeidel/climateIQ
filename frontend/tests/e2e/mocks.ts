import type { Page } from '@playwright/test'

export const mockApi = async (page: Page) => {
  await page.route('**/api/v1/**', async (route) => {
    const url = route.request().url()

    if (url.endsWith('/api/v1/zones')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      })
    }

    if (url.includes('/api/v1/analytics/')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: [], total_estimated_kwh: 0 }),
      })
    }

    if (url.endsWith('/api/v1/weather/entities')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { entity_id: 'weather.home', name: 'Home', state: 'sunny' },
        ]),
      })
    }

    if (url.endsWith('/api/v1/settings')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          system_name: 'ClimateIQ',
          current_mode: 'learn',
          timezone: 'UTC',
          temperature_unit: 'F',
          default_comfort_temp_min: 20,
          default_comfort_temp_max: 24,
          default_humidity_min: 35,
          default_humidity_max: 55,
          energy_cost_per_kwh: 0.12,
          currency: 'USD',
          weather_entity: 'weather.home',
          mqtt_broker: 'localhost',
          mqtt_port: 1883,
          mqtt_username: '',
          mqtt_password: '',
          mqtt_use_tls: false,
          home_assistant_url: 'http://homeassistant.local:8123',
          home_assistant_token: 'test-token',
          llm_settings: {},
          default_schedule: null,
          last_synced_at: null,
        }),
      })
    }

    if (url.endsWith('/api/v1/settings/llm/providers')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ providers: [] }),
      })
    }

    if (url.includes('/api/v1/weather/')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          source: 'cache',
          cached: true,
          stale: false,
          cache_age_seconds: 30,
          fetched_at: new Date().toISOString(),
          data: {
            state: 'sunny',
            temperature: 20,
            humidity: 50,
            pressure: 1013,
            wind_speed: 10,
            wind_bearing: 180,
            visibility: 10,
            temperature_unit: '°C',
            pressure_unit: 'hPa',
            wind_speed_unit: 'km/h',
            visibility_unit: 'km',
            attribution: '',
            entity_id: 'weather.home',
            last_updated: new Date().toISOString(),
          },
        }),
      })
    }

    if (url.includes('/api/v1/schedules/')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      })
    }

    if (url.endsWith('/api/v1/system/health')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok' }),
      })
    }

    if (url.endsWith('/api/v1/system/version')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ name: 'ClimateIQ', version: '0.1.0' }),
      })
    }

    if (url.endsWith('/api/v1/backup')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      })
    }

    if (url.endsWith('/api/v1/backup/export')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ backup_id: 'mock-backup', message: 'Backup created' }),
      })
    }

    if (url.includes('/api/v1/chat')) {
      const method = route.request().method()
      if (method === 'POST') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            message: 'All systems are operating normally.',
            session_id: 'mock-session',
            timestamp: new Date().toISOString(),
            actions_taken: [],
            suggestions: [],
            metadata: {},
          }),
        })
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      })
    }

    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({}),
    })
  })
}
