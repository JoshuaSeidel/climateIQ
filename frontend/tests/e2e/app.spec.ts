import { test, expect } from '@playwright/test'
import { mockApi } from './mocks'

test.describe('ClimateIQ UI', () => {
  test('loads dashboard', async ({ page }) => {
    await mockApi(page)
    await page.goto('/')
    await expect(page.getByText('ClimateIQ', { exact: true })).toBeVisible()
    await expect(page.getByRole('link', { name: 'Dashboard' })).toBeVisible()
  })

  test('navigates to zones', async ({ page }) => {
    await mockApi(page)
    await page.goto('/')
    await page.getByRole('link', { name: 'Zones' }).click()
    await expect(page.getByRole('link', { name: 'Zones' })).toBeVisible()
  })

  test('navigates to analytics', async ({ page }) => {
    await mockApi(page)
    await page.goto('/')
    await page.getByRole('link', { name: 'Analytics' }).click()
    await expect(page.getByRole('link', { name: 'Analytics' })).toBeVisible()
  })

  test('navigates to chat', async ({ page }) => {
    await mockApi(page)
    await page.goto('/')
    await page.getByRole('link', { name: 'Chat' }).click()
    await expect(page.getByRole('link', { name: 'Chat' })).toBeVisible()
  })

  test('navigates to settings', async ({ page }) => {
    await mockApi(page)
    await page.goto('/')
    await page.getByRole('link', { name: 'Settings' }).click()
    await expect(page.getByRole('link', { name: 'Settings' })).toBeVisible()
  })
})
