import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e', workers: 1, retries: 0, reporter: 'list',
  outputDir: '../data/e2e',
  use: {
    baseURL: 'http://127.0.0.1:4200', trace: 'off', screenshot: 'only-on-failure',
    launchOptions: { executablePath: '/usr/bin/google-chrome', args: ['--no-sandbox', '--disable-dev-shm-usage'] }
  }
});

