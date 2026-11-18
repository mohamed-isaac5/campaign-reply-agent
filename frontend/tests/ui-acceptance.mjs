/** Local UI acceptance: isolated DB, no keys/accounts, no real sends.
 * Stop the regular app first. Requires `npx playwright install chromium` once.
 * Run from frontend: npm run test:ui
 */
import { chromium } from 'playwright';
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import { fileURLToPath } from 'node:url';
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(frontend);
const out = fs.mkdtempSync(path.join(os.tmpdir(), 'cra-ui-acceptance-'));
const children = []; let browser;
const check = (value, message) => { if (!value) throw new Error(message); };
const free = port => new Promise((resolve, reject) => { const s = net.createServer(); s.once('error', () => reject(new Error(`Port ${port} is busy. Stop the regular app before running acceptance tests.`))); s.listen(port, '127.0.0.1', () => s.close(resolve)); });
const start = (command, args, cwd, extra, name) => { const log = fs.openSync(path.join(out, name + '.log'), 'a'); const child = spawn(command, args, { cwd, env: { ...process.env, ...extra }, detached: true, stdio: ['ignore', log, log] }); children.push(child); return child; };
const ready = async url => { for (let n = 0; n < 100; n++) { try { if ((await fetch(url)).ok) return; } catch {} await new Promise(r => setTimeout(r, 100)); } throw new Error('Server did not start: ' + url); };
try {
 await free(8791); await free(8792);
 const built = spawnSync('npm', ['run', 'build'], { cwd: frontend, stdio: 'inherit' }); check(built.status === 0, 'Production build failed');
 const venv = path.join(root, '.venv', 'bin', 'python');
 const python = process.env.CRA_TEST_PYTHON || (fs.existsSync(venv) ? venv : 'python3');
 start(python, ['-m', 'uvicorn', 'app:app', '--app-dir', 'backend', '--host', '127.0.0.1', '--port', '8791'], root, { CRA_DB: path.join(out, 'acceptance.db'), CRA_SEED: '1', GMAIL_ENABLED: '0', LLM_PROVIDER: '', LLM_API_KEY: '' }, 'backend');
 start('npm', ['start'], frontend, {}, 'frontend');
 await ready('http://127.0.0.1:8791/api/status'); await ready('http://127.0.0.1:8792/chat');
 browser = await chromium.launch();
 const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
 const pageErrors = []; page.on('pageerror', e => pageErrors.push(e.message));
 const status = await (await page.request.get('http://127.0.0.1:8791/api/status')).json();
 check(status.llm.mode === 'demo' && !status.gmail.connected, 'Acceptance must be isolated from real accounts');
 await page.route('**/api/onboarding', route => route.abort());
 await page.goto('http://127.0.0.1:8792/chat');
 await page.getByRole('button', { name: 'Retry loading setup', exact: true }).waitFor();
 check(await page.getByLabel('Chat message').isDisabled(), 'Input enabled before correct question loaded');
 await page.screenshot({ path: path.join(out, 'onboarding-load-error.png'), fullPage: true });
 await page.unroute('**/api/onboarding');
 await page.getByRole('button', { name: 'Retry loading setup', exact: true }).click();
 await page.getByText('What do you sell, and to whom?', { exact: true }).waitFor();
 check(await page.getByLabel('Chat message').isEnabled(), 'Retry did not restore chat input');
 await page.screenshot({ path: path.join(out, 'onboarding-recovered.png'), fullPage: true });
 const say = async text => { await page.getByLabel('Chat message').fill(text); await page.getByRole('button', { name: 'Send', exact: true }).click(); await page.waitForFunction(() => !document.querySelector('.chat-input input').disabled); };
 await say('I sell workflow software to small sales teams');
 await page.getByText('What facts can I tell a customer?', { exact: false }).waitFor();
 await page.reload();
 await page.getByText('What facts can I tell a customer?', { exact: false }).waitFor();
 check(await page.getByText('Business: I sell workflow software to small sales teams', { exact: true }).isVisible(), 'Saved onboarding answer missing after reload');
 for (const text of ['The subscription is 49.99 USD. Technical support is included.', '49.99 USD', 'Sample renewal campaign, goal renewals, first reminder then renewal offer then follow-up call', 'Friendly and direct', 'Never auto-reply to price questions; drafts only']) await say(text);
 await page.reload();

 await page.getByRole('button', { name: 'Confirm summary and explicit rule' }).waitFor();
 check(!await page.locator('.summary-card input[type=checkbox]').isChecked(), 'Free text enabled a grant');
 await page.screenshot({ path: path.join(out, 'onboarding.png'), fullPage: true });
 await page.getByRole('button', { name: 'Confirm summary and explicit rule' }).click();
 await page.getByText('Summary confirmed', { exact: true }).waitFor();
 await page.request.post('http://127.0.0.1:8791/api/sim/tick', { data: {} });
 const queue = await (await page.request.get('http://127.0.0.1:8791/api/review')).json();
 check(queue[0].kind === 'draft', 'Refusal/default-off did not produce a draft');
 await page.goto('http://127.0.0.1:8792/settings');
 await page.getByLabel(/I explicitly allow/).check(); await page.getByRole('button', { name: 'Save explicit rule' }).click();
 await page.getByText('Price-only enabled', { exact: true }).waitFor();
 for (let n = 0; n < 7; n++) await page.request.post('http://127.0.0.1:8791/api/sim/tick', { data: {} });
 await page.goto('http://127.0.0.1:8792/review'); await page.locator('textarea').first().waitFor();
 const editor = page.locator('textarea').first(); const body = await editor.inputValue();
 await editor.fill(''); check(await page.getByRole('button', { name: 'Approve exact reply (simulation)', exact: true }).first().isDisabled(), 'Empty edited draft remained approvable'); await editor.fill(body);
 await page.route('**/api/review/*', async route => { if (route.request().method() === 'POST') await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: false, detail: 'Deliberate local failure for the acceptance test. No send attempted.' }) }); else await route.continue(); });
 await page.getByRole('button', { name: 'Approve exact reply (simulation)', exact: true }).first().click();
 await page.getByText('Deliberate local failure for the acceptance test. No send attempted.', { exact: true }).first().waitFor();
 await page.screenshot({ path: path.join(out, 'visible-error.png') });
 await page.unroute('**/api/review/*');
 await page.getByRole('button', { name: 'Approve exact reply (simulation)', exact: true }).first().click();
 await page.getByText(/Recorded in simulation/).first().waitFor();
 await page.screenshot({ path: path.join(out, 'receipt.png') });
 const widths = [];
 for (const width of [1280, 390, 320]) {
  await page.setViewportSize({ width, height: 900 });
  for (const route of ['/chat', '/campaigns', '/campaigns/2', '/review', '/settings']) {
   await page.goto('http://127.0.0.1:8792' + route); await page.waitForLoadState('networkidle');
   const extent = await page.evaluate(() => document.documentElement.scrollWidth);
   widths.push({ width, route, scrollWidth: extent }); check(extent <= width, 'Horizontal overflow on ' + route + ' at ' + width);
   if (route === '/campaigns/2') {
    const toggles = page.locator('button.thread-head');
    check(await toggles.count() > 0, 'No thread expanders found');
    let chosen = false;
    for (let i = 0; i < await toggles.count(); i++) {
     const toggle = toggles.nth(i); await toggle.focus(); await page.keyboard.press('Enter');
     check(await toggle.getAttribute('aria-expanded') === 'true', 'Enter did not expand thread');
     if (await page.locator('.action-row a').count()) { chosen = true; break; }
     await page.keyboard.press('Space');
     check(await toggle.getAttribute('aria-expanded') === 'false', 'Space did not collapse thread');
    }
    check(chosen, 'No expanded action available for readability check');
    const reason = page.locator('.action-reason').first();
    const reasonBox = await reason.boundingBox();
    check(reasonBox.width >= Math.min(200, width - 80), 'Expanded reason too narrow: ' + reasonBox.width);
    await page.locator('.action-row').first().scrollIntoViewIfNeeded();
    await page.locator('.action-row').first().evaluate(el => { const rect = el.getBoundingClientRect(); window.scrollBy(0, rect.bottom - window.innerHeight + 125); });
    widths.push({ width, route: route + '#expanded', reasonWidth: reasonBox.width });
    check(await page.evaluate(() => document.documentElement.scrollWidth) <= width, 'Expanded thread overflows');
   }
   await page.screenshot({ path: path.join(out, route.replaceAll('/', '-') + '-' + width + '.png') });
  }
 }
 await page.goto('http://127.0.0.1:8792/campaigns/999');
 await page.getByRole('heading', { name: 'Campaign not found', exact: true }).waitFor();
 await page.getByRole('link', { name: 'Back to Campaigns', exact: true }).waitFor();
 await page.screenshot({ path: path.join(out, 'missing-campaign.png') });
 await page.route('**/api/campaigns/998', route => route.abort());
 await page.goto('http://127.0.0.1:8792/campaigns/998');
 await page.getByRole('heading', { name: 'Campaign could not be loaded' }).waitFor();
 await page.unroute('**/api/campaigns/998');
 const history = Array.from({ length: 15 }, (_, i) => ({ id: 9000+i, contact_name: 'History test '+i, reason: 'Local completed decision', status: 'done' }));
 await page.route('**/api/activity', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify(history) }));
 await page.goto('http://127.0.0.1:8792/review'); await page.locator('.activity-item').last().waitFor();
 check(await page.locator('.activity-item').count() === 15, 'History truncates advertised records');
 await page.unroute('**/api/activity');
 await page.route('**/api/status', async route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ...status, llm: { mode: 'invalid', provider: 'typo', error: 'Unsupported LLM_PROVIDER. No model request is allowed.' } }) }));
 await page.goto('http://127.0.0.1:8792/settings');
 await page.getByText('Configuration error: model requests blocked', { exact: true }).waitFor();
 await page.screenshot({ path: path.join(out, 'invalid-provider.png') });
 await page.unroute('**/api/status');
 check(pageErrors.length === 0, 'Browser runtime errors: ' + pageErrors.join('; '));
 fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify({ passed: true, widths, pageErrors }, null, 2));
 console.log('UI acceptance passed. Screenshots, logs and isolated DB: ' + out);
} catch (error) { console.error(error); process.exitCode = 1; }
finally {
 if (browser) await browser.close();
 for (const child of children.reverse()) { try { process.kill(-child.pid, 'SIGTERM'); } catch {} }
}
