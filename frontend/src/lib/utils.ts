import { type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import clsx from 'clsx'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export const formatTemperature = (value: number, unit: 'c' | 'f' = 'c') => {
  return unit === 'c' ? `${value.toFixed(1)}°C` : `${((value * 9) / 5 + 32).toFixed(1)}°F`
}

/** Convert a Celsius value to the target unit (no formatting). */
export const toDisplayTemp = (celsius: number, unit: 'c' | 'f'): number => {
  return unit === 'c' ? celsius : (celsius * 9) / 5 + 32
}

/** Convert a value in the display unit back to Celsius for storage. */
export const toStorageCelsius = (value: number, unit: 'c' | 'f'): number => {
  return unit === 'c' ? value : ((value - 32) * 5) / 9
}

/** Return the temperature unit symbol string. */
export const tempUnitLabel = (unit: 'c' | 'f'): string => {
  return unit === 'c' ? '°C' : '°F'
}

export const formatHumidity = (value: number) => `${value.toFixed(0)}%`
