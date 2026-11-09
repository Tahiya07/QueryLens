import { test, expect } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';

test('register, upload sample, profile and analyze revenue', async ({ page }) => {
  const email = `journey-${Date.now()}@example.com`;
  await page.goto('/');
  await page.getByRole('button', { name: 'New to QueryLens? Create an account' }).click();
  await page.getByPlaceholder('Work email').fill(email);
  await page.getByPlaceholder('Password (10+ characters)').fill('correct-horse-battery');
  await page.getByRole('button', { name: 'Create account' }).click();
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible();
  await page.getByLabel('Upload dataset').setInputFiles(path.resolve(__dirname, '../../sample-data/sales.csv'));
  await expect(page.getByRole('heading', { name: 'sales.csv' })).toBeVisible();
  await page.getByRole('button', { name: 'Find the highest value' }).click();
  await expect(page.getByText(/Calculated total revenue by product/)).toBeVisible();
  await expect(page.getByRole('cell', { name: 'Desk Lamp' }).first()).toBeVisible();
  const screenshots = path.resolve(__dirname, '../../screenshots');
  await fs.mkdir(screenshots, { recursive: true });
  await page.screenshot({ path: path.join(screenshots, 'analysis-workspace.png'), fullPage: true });
});
