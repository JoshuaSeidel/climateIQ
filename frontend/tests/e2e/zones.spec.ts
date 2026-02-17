import { test, expect } from '@playwright/test'
import { mockApi } from './mocks'

test.describe('Zones page', () => {
  test('loads zone list view', async ({ page }) => {
    await mockApi(page)
    await page.goto('/zones')
    await expect(page.getByRole('link', { name: 'Zones' })).toBeVisible()
  })

  test('can open create zone form', async ({ page }) => {
    await mockApi(page)
    await page.goto('/zones')
    await page.getByRole('button', { name: /add zone/i }).click()
    await expect(page.getByRole('heading', { name: /Create Zone/i })).toBeVisible()
  })
})
