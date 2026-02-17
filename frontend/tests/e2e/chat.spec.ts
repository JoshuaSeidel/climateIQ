import { test, expect } from '@playwright/test'
import { mockApi } from './mocks'

test.describe('Chat page', () => {
  test('loads chat page', async ({ page }) => {
    await mockApi(page)
    await page.goto('/chat')
    await expect(page.getByRole('link', { name: 'Chat' })).toBeVisible()
  })

  test('shows assistant greeting', async ({ page }) => {
    await mockApi(page)
    await page.goto('/chat')
    await expect(page.getByText("I'm ClimateIQ")).toBeVisible()
  })
})
