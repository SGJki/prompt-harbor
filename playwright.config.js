import { defineConfig } from '@playwright/test';

const launchOptions = process.env.PROMPT_HARBOR_BROWSER
  ? { executablePath: process.env.PROMPT_HARBOR_BROWSER }
  : {};

export default defineConfig({
  testDir: './browser-tests',
  timeout: 30000,
  use: {
    browserName: 'chromium',
    headless: true,
    launchOptions,
  },
  reporter: [['line']],
});
