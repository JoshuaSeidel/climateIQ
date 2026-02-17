import { test, expect } from '@playwright/test'
import { mockApi } from './mocks'

test.describe('Settings page', () => {
  test('loads settings page', async ({ page }) => {
    await mockApi(page)
    await page.goto('/settings')
    await expect(page.getByRole('link', { name: 'Settings' })).toBeVisible()
  })

  test('switches to LLM Providers tab', async ({ page }) => {
    await mockApi(page)
    await page.goto('/settings')
    await page.getByRole('button', { name: /LLM Providers/i }).click()
    await expect(page.getByRole('heading', { name: /AI Providers/i })).toBeVisible()
  })
})
