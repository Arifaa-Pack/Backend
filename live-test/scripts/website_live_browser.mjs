// Live browser test (20 Nov): Arifa's real website (Arifa-Pack, next dev on :3100, with the WP-C3 adapter patch
// docs/handoff/website-adapter/arifa-site-actions.patch) talking to the live Kairox stack (:38000) with the real
// AI. A real Chromium tab; the visitor's words go to the assistant's own handle() (the site has no text box
// and a headless browser no microphone). Records, per turn: the API answer and its actions, where the page
// went, the chat log, a screenshot. Output: live-test/results/website_live/browser_run.json + screens/*.png
//
//   PW_DIR=<folder with node_modules/playwright-core> CHROME=<chrome.exe> node live-test/scripts/website_live_browser.mjs
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(path.join(process.env.PW_DIR, 'package.json'));
const { chromium } = require('playwright-core');
const here = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(here, '../results/website_live');
const SHOTS = path.join(OUT, 'screens');
fs.mkdirSync(SHOTS, { recursive: true });
const SITE = process.env.SITE || 'http://localhost:3100';

const STEPS = [
  { start: '/', say: 'Hi, show me kitchen foil', expect: /\/products\?.*family=foil|\/products\/foil/ },
  { say: 'do you have 8325?', expect: /8325/, inPage: '.qv.is-open, [data-product]' },
  { say: 'where are your certificates?', expect: /\/quality/ },
  { start: '/ar', say: 'أرني حاويات ألمنيوم', expect: /\/ar\/products.*family=containers/ },
  { start: '/products', say: 'I need containers with lids for biryani takeaway, which one fits 1 kg?' },
  { say: 'We need about 20,000 pieces a month for our restaurants in Dubai. Please prepare a quotation.' },
];

const browser = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'en-US' });
const page = await context.newPage();
const consoleErrors = [];
page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300)); });
page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${String(e).slice(0, 300)}`));
let lastApi = null;
page.on('response', async (r) => {
  const u = r.url();
  if (!u.includes('/v1/public/sessions/') || !u.endsWith('/messages')) return;
  try {
    const body = await r.json();
    lastApi = { status: r.status(), reply: body.reply, handed_over: body.handed_over,
      actions: (body.actions || []).map((a) => ({ type: a.type, layer: a.layer, family: a.family, product_id: a.product_id, page: a.page, section: a.section })) };
  } catch { lastApi = { status: r.status() }; }
});

async function ready() {
  await page.waitForLoadState('domcontentloaded');
  await page.waitForFunction(() => typeof window.__arifaHandle === 'function', null, { timeout: 90_000 });
  await page.mouse.click(5, 5); // a first click: the page may speak
}

const results = [];
let i = 0;
for (const step of STEPS) {
  i += 1;
  if (step.start) {
    await page.goto(SITE + step.start, { waitUntil: 'domcontentloaded', timeout: 120_000 });
  }
  await ready();
  const before = page.url();
  const logBefore = await page.$$eval('[data-assist-log] li', (els) => els.length).catch(() => 0);
  lastApi = null;
  const t0 = Date.now();
  page.evaluate((t) => window.__arifaHandle(t), step.say).catch(() => {}); // navigation may end this context
  // the API answer
  while (!lastApi && Date.now() - t0 < 120_000) await page.waitForTimeout(250);
  const msApi = Date.now() - t0;
  // the page's move (a navigation, an open product, or nothing)
  let moved = null;
  if (step.expect) {
    try {
      await page.waitForURL(step.expect, { timeout: 45_000 });
      moved = `url ${page.url().replace(SITE, '')}`;
    } catch {
      if (step.inPage && (await page.$(step.inPage))) moved = `in page ${step.inPage}`;
      else if (step.expect.test(await page.content())) moved = 'product shown in the page';
    }
  } else {
    await page.waitForTimeout(8_000);
    if (page.url() !== before) moved = `url ${page.url().replace(SITE, '')}`;
  }
  const msMoved = Date.now() - t0;
  await page.waitForTimeout(2_500);
  const log = await page.$$eval('[data-assist-log] li', (els) => els.map((e) => e.textContent.trim()).filter(Boolean)).catch(() => []);
  const shot = path.join(SHOTS, `${String(i).padStart(2, '0')}.png`);
  await page.screenshot({ path: shot });
  results.push({
    step: i, start: step.start || null, said: step.say, from: before.replace(SITE, ''), url_after: page.url().replace(SITE, ''),
    api: lastApi, ms_api: msApi, expected: step.expect ? String(step.expect) : null,
    moved, ms_until_moved: moved ? msMoved : null, passed: step.expect ? !!moved : lastApi?.status === 200,
    chat_log_new: log.slice(logBefore), screenshot: path.relative(OUT, shot),
  });
  console.log(`${i}. ${step.say.slice(0, 50)} -> api ${lastApi?.status} ${msApi} ms, actions ${JSON.stringify(lastApi?.actions || [])}, moved: ${moved}`);
}

const summary = { site: SITE, when: new Date().toISOString(), passed: results.filter((r) => r.passed).length, of: results.length, consoleErrors: [...new Set(consoleErrors)].slice(0, 20), results };
fs.writeFileSync(path.join(OUT, 'browser_run.json'), JSON.stringify(summary, null, 2));
console.log(`passed ${summary.passed}/${summary.of}; console errors: ${summary.consoleErrors.length}`);
await browser.close();
