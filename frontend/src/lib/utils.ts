import { type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import clsx from 'clsx'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export const formatTemperature = (value: number, unit: 'c' | 'f' = 'c') => {
  return unit === 'c' ? `${value.toFixed(1)}°C` : `${((value * 9) / 5 + 32).toFixed(1)}°F`
}

export const formatHumidity = (value: number) => `${value.toFixed(0)}%`
