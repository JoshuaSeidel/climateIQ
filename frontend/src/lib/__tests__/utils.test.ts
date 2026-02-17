import { describe, it, expect } from 'vitest'
import { formatHumidity, formatTemperature } from '@/lib/utils'

describe('formatTemperature', () => {
  it('formats Celsius', () => {
    expect(formatTemperature(21.234, 'c')).toBe('21.2°C')
  })

  it('formats Fahrenheit', () => {
    expect(formatTemperature(0, 'f')).toBe('32.0°F')
  })
})

describe('formatHumidity', () => {
  it('formats percent', () => {
    expect(formatHumidity(45.4)).toBe('45%')
  })
})
