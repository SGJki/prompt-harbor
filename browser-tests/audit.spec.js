import { test, expect } from '@playwright/test';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import http from 'node:http';

const root = join(import.meta.dirname, '..');

async function waitFor(url, deadline = Date.now() + 8000) {
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.status) return;
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 40));
  }
  throw new Error(`gateway did not start: ${url}`);
}

test('audit desk supports navigation, filters, details, refresh and SSE invalidation', async ({ page }) => {
  const temp = mkdtempSync(join(tmpdir(), 'prompt-harbor-browser-'));
  const upstream = http.createServer((request, response) => {
    request.resume();
    const body = JSON.stringify({ ok: true, model: 'browser-model' });
    response.writeHead(200, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body) });
    response.end(body);
  });
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve));
  const gatewayPort = await new Promise(resolve => {
    const probe = http.createServer();
    probe.listen(0, '127.0.0.1', () => { const port = probe.address().port; probe.close(() => resolve(port)); });
  });
  const database = join(temp, 'browser.db');
  const config = join(temp, 'prompt-harbor.ini');
  writeFileSync(config, '');
  const gateway = spawn(process.env.PYTHON || 'python3', [join(root, 'prompt_harbor.py'), 'start', '--config', config, '--database', database, '--listen', `127.0.0.1:${gatewayPort}`, '--upstream', `http://127.0.0.1:${upstream.address().port}`, '--sse-keepalive', '0.05'], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  try {
    await waitFor(`http://127.0.0.1:${gatewayPort}/`);
    const post = body => fetch(`http://127.0.0.1:${gatewayPort}/v1/responses`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: 'Bearer browser-secret' }, body: JSON.stringify(body) });
    await post({ model: 'browser-model', input: 'first' });

    await page.goto(`http://127.0.0.1:${gatewayPort}/`);
    await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible();
    await expect(page.getByText('Total calls')).toBeVisible();

    await page.getByRole('tab', { name: 'Configuration' }).click();
    await page.locator('[data-config-field="api_call_limit"]').fill('123');
    const configResponse = page.waitForResponse(response => response.url().endsWith('/api/config') && response.request().method() === 'PUT');
    await page.getByRole('button', { name: 'Save changes' }).click();
    const saved = await configResponse;
    expect(saved.status()).toBe(200);
    expect(saved.headers()['content-security-policy']).toContain("default-src 'none'");
    expect((await saved.json()).fields.api_call_limit.value).toBe(123);

    await page.getByRole('tab', { name: 'Calls' }).click();
    await expect(page.locator('.call-row')).toHaveCount(1);
    await page.locator('[data-filter="q"]').fill('browser-model');
    await expect(page.locator('.call-row')).toHaveCount(1);
    await page.locator('[data-filter="q"]').fill('no-match');
    await expect(page.locator('.empty')).toContainText('No calls');
    await page.locator('[data-filter="q"]').fill('browser-model');
    await page.waitForTimeout(250);
    await page.locator('.call-row').first().evaluate(el => el.click());
    await expect(page.locator('#call-detail')).toContainText('Request headers');
    await expect(page.locator('body')).not.toContainText('Authorization');

    await page.getByRole('tab', { name: 'Runtime Sessions' }).click();
    await expect(page.locator('[data-session-calls]')).toHaveCount(1);
    await page.locator('[data-session-calls]').click();
    await expect(page.getByRole('heading', { name: 'Calls' })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'Calls' })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByRole('tab', { name: 'Runtime Sessions' })).toHaveAttribute('aria-selected', 'false');
    await expect(page.locator('[data-filter="session"]')).not.toHaveValue('all');

    await page.getByRole('button', { name: 'Refresh' }).click();
    await expect(page.locator('.call-row')).toHaveCount(1);
    await post({ model: 'browser-model-2', input: 'second' });
    await page.getByRole('tab', { name: 'Overview' }).click();
    await expect(page.locator('.call-row')).toHaveCount(2);

    let failed = true;
    await page.route(/\/api\/calls\/\d+$/, async route => {
      if (failed) { failed = false; await route.abort(); } else await route.continue();
    });
    await page.getByRole('tab', { name: 'Calls' }).click();
    await page.locator('.call-row').first().evaluate(el => el.click());
    await expect(page.locator('#call-detail')).toContainText('Retry');
    await page.getByRole('button', { name: 'Retry' }).click();
    await expect(page.locator('#call-detail')).toContainText('Request headers');
  } finally {
    gateway.kill('SIGTERM');
    await new Promise(resolve => gateway.once('exit', resolve));
    await new Promise(resolve => upstream.close(resolve));
    rmSync(temp, { recursive: true, force: true });
  }
});
