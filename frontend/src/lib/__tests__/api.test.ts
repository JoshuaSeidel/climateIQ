import { describe, it, expect } from 'vitest'
import { BASE_PATH, formatApiError } from '@/lib/api'

describe('API base path', () => {
  it('returns a string', () => {
    expect(typeof BASE_PATH).toBe('string')
  })
})

describe('formatApiError', () => {
  it('uses a string detail', () => {
    expect(formatApiError('{"detail":"Zone not found"}', 404)).toBe('Zone not found')
  })

  it('joins validation errors into readable text', () => {
    const body = JSON.stringify({
      detail: [{ loc: ['body', 'target_temp_c'], msg: 'Input should be greater than or equal to 4.4' }],
    })
    expect(formatApiError(body, 422)).toBe(
      'target_temp_c: Input should be greater than or equal to 4.4',
    )
  })

  it('falls back to the raw text, then to the status', () => {
    expect(formatApiError('Bad gateway', 502)).toBe('Bad gateway')
    expect(formatApiError('', 500)).toBe('API request failed: 500')
    expect(formatApiError('{"other":1}', 400)).toBe('{"other":1}')
  })
})
