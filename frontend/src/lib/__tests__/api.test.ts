import { describe, it, expect } from 'vitest'
import { BASE_PATH } from '@/lib/api'

describe('API base path', () => {
  it('returns a string', () => {
    expect(typeof BASE_PATH).toBe('string')
  })
})
