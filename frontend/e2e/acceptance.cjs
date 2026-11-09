/**
 * Lola end-to-end acceptance suite.
 *
 *   npm run dev:demo           # in one terminal (VITE_LOLA_MOCK=1)
 *   node e2e/acceptance.cjs    # writes docs/lola/acceptance/{results.json,screenshots/}
 *
 * Runs every workflow at desktop and mobile viewports against the scripted
 * demo transport (same contract as the real backend). The backend itself is
 * stubbed to 503 so nothing can silently succeed.
 */
const { chromium, devices } = require('playwright');
const fs = require('fs');
const path = require('path');
const { asRole } = require('./helpers.cjs');

const BASE = process.env.LOLA_BASE_URL || 'http://localhost:5173';
const OUT = path.resolve(__dirname, '../../docs/lola/acceptance');
const SHOTS = path.join(OUT, 'screenshots');
fs.mkdirSync(SHOTS, { recursive: true });

const VIEWPORTS = {
  desktop: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 },
  mobile: { ...devices['iPhone 13'], deviceScaleFactor: 1 },
};

const results = [];
const latency = [];

function assert(cond, msg) { if (!cond) throw new Error(msg); }

/** Fake mic + speaker so voice flows run headless. Exposes window.__say(text). */
function voiceShim() {
  window.__spoken = [];
  window.__cancelCount = 0;
  class FakeRec {
    constructor() { window.__rec = this; this.lang = ''; }
    start() { window.__recStarted = (window.__recStarted || 0) + 1; }
    stop() { this.onend && this.onend(); }
    abort() { this.onend && this.onend(); }
  }
  window.SpeechRecognition = FakeRec;
  window.webkitSpeechRecognition = FakeRec;
  window.__say = (text) => {
    const r = window.__rec;
    const mk = (t, f) => Object.assign([{ transcript: t }], { isFinal: f });
    r.onresult({ resultIndex: 0, results: [mk(text.split(' ').slice(0, 2).join(' '), false)] });
    r.onresult({ resultIndex: 0, results: [mk(text, true)] });
    r.onend();
  };
  const synth = {
    _q: [],
    speak(u) { window.__spoken.push(u.text); this._q.push(u); if (this._q.length === 1) setTimeout(() => u.onstart && u.onstart(), 10); },
    cancel() { window.__cancelCount++; this._q = []; },
    getVoices() { return []; },
  };
  Object.defineProperty(window, 'speechSynthesis', { value: synth, configurable: true });
  Object.defineProperty(window, 'SpeechSynthesisUtterance', { value: function (t) { this.text = t; }, configurable: true, writable: true });
}

async function ask(page, text) {
  const input = page.locator('#lola-input').first();
  await input.fill(text);
  const t0 = Date.now();
  await input.press('Enter');
  const reply = page.getByTestId('lola-reply').last();
  await reply.locator('p, .lola-caret').first().waitFor({ timeout: 8000 });
  const tFirst = Date.now();
  await page.waitForFunction(() => {
    const r = [...document.querySelectorAll('[data-testid=lola-reply]')].pop();
    return r && r.getAttribute('data-status') !== 'streaming';
  }, null, { timeout: 15000 });
  const tDone = Date.now();
  return { reply, firstMs: tFirst - t0, totalMs: tDone - t0 };
}

async function openPanel(page) {
  await page.getByTestId('lola-launcher').click();
  await page.getByTestId('lola-panel').waitFor();
}

const WORKFLOWS = [
  {
    id: 'owner-daily-briefing', role: 'owner', title: 'Owner daily briefing',
    async run(page, vp) {
      await page.goto(`${BASE}/lola`);
      await page.locator('[data-section]').first().waitFor();
      const sections = await page.locator('[data-section]').evaluateAll((els) => els.map((e) => e.getAttribute('data-section')));
      assert(sections.includes('financial_risks') && sections.includes('pending_approvals'), 'owner sees executive sections');
      await page.screenshot({ path: path.join(SHOTS, `${vp}-01-command-center-owner.png`), fullPage: vp === 'mobile' });
      const btn = page.getByRole('button', { name: /brief me/i });
      await btn.click();
      await page.waitForFunction(() => [...document.querySelectorAll('[data-testid=lola-reply]')].pop()?.getAttribute('data-status') === 'complete', null, { timeout: 15000 });
      assert(await page.locator('[data-card-kind=metrics]').count() > 0, 'briefing shows metrics');
      assert(await page.getByText(/QuickBooks Online/).count() > 0, 'briefing cites its source');
      await page.locator('#lola-conversation').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(SHOTS, `${vp}-02-owner-briefing-answer.png`) });
      return { sections: sections.length };
    },
  },
  {
    id: 'ar-collection', role: 'owner', title: 'AR collection workflow + verified execution',
    async run(page, vp) {
      await page.goto(`${BASE}/`);
      await openPanel(page);
      const m = await ask(page, 'Which invoices are overdue?');
      latency.push({ workflow: 'ar-collection', vp, firstMs: m.firstMs, totalMs: m.totalMs });
      assert(await m.reply.getByText('INV-10442').count() === 1, 'overdue invoice table shown');
      await page.screenshot({ path: path.join(SHOTS, `${vp}-03-ar-overdue-invoices.png`) });
      await m.reply.getByRole('button', { name: /approve & run/i }).click();
      await m.reply.getByText(/not confirmed yet/i).waitFor();
      await m.reply.locator('[data-receipt=verified]').waitFor({ timeout: 5000 });
      await page.screenshot({ path: path.join(SHOTS, `${vp}-04-ar-verified-receipt.png`) });
      return {};
    },
  },
  {
    id: 'sales-investigation', role: 'manager', title: 'Sales performance investigation',
    async run(page, vp) {
      await page.goto(`${BASE}/`);
      await openPanel(page);
      const m = await ask(page, 'Why did sales drop last week?');
      latency.push({ workflow: 'sales-investigation', vp, firstMs: m.firstMs, totalMs: m.totalMs });
      assert(await m.reply.locator('[data-card-kind=financial_explanation]').count() === 1, 'explanation card');
      assert(await m.reply.locator('[data-card-kind=chart]').count() === 1, 'chart card');
      await page.screenshot({ path: path.join(SHOTS, `${vp}-05-sales-investigation.png`) });
      return {};
    },
  },
  {
    id: 'container-inquiry', role: 'sales_rep', title: 'Container and delivery inquiry (sales rep)',
    async run(page, vp) {
      await page.goto(`${BASE}/`);
      await openPanel(page);
      const m = await ask(page, 'When will Sunrise Distributors’ order arrive?');
      latency.push({ workflow: 'container-inquiry', vp, firstMs: m.firstMs, totalMs: m.totalMs });
      assert(await m.reply.locator('[data-card-kind=shipment_timeline]').count() === 1, 'timeline');
      assert(await m.reply.locator('[data-step-status=exception]').count() === 1, 'exception highlighted');
      assert(await m.reply.getByText(/needs manager approval|can’t approve/i).count() === 1, 'rep cannot approve customer notice');
      await page.screenshot({ path: path.join(SHOTS, `${vp}-06-container-timeline.png`) });
      return {};
    },
  },
  {
    id: 'warehouse-exception', role: 'back_office', title: 'Warehouse receiving exception',
    async run(page, vp) {
      await page.goto(`${BASE}/lola`);
      await page.locator('[data-section]').first().waitFor();
      const sections = await page.locator('[data-section]').evaluateAll((els) => els.map((e) => e.getAttribute('data-section')));
      assert(!sections.includes('financial_risks') && !sections.includes('performance'), 'back office sees no financials');
      const m = await ask(page, 'Any receiving exceptions today?');
      latency.push({ workflow: 'warehouse-exception', vp, firstMs: m.firstMs, totalMs: m.totalMs });
      await m.reply.getByRole('button', { name: /approve & run/i }).click();
      await m.reply.locator('[data-receipt=unverified]').waitFor({ timeout: 5000 });
      assert(await m.reply.locator('[data-receipt=verified]').count() === 0, 'unverified claim is not shown as done');
      assert(await m.reply.getByText(/awaiting confirmation/i).count() === 1, 'shows awaiting confirmation');
      await page.screenshot({ path: path.join(SHOTS, `${vp}-07-warehouse-unverified-receipt.png`) });
      return { sections };
    },
  },
  {
    id: 'permission-denial', role: 'sales_rep', title: 'Permission denial',
    async run(page, vp) {
      await page.goto(`${BASE}/lola`);
      await page.locator('[data-section]').first().waitFor();
      const sections = await page.locator('[data-section]').evaluateAll((els) => els.map((e) => e.getAttribute('data-section')));
      assert(!sections.some((s) => ['critical_alerts', 'performance', 'financial_risks', 'pending_approvals'].includes(s)), `rep sees only own sections: ${sections}`);
      const html = await page.content();
      assert(!html.includes('$312') && !html.includes('31.4%') && !html.includes('Harbor Wines — $21.8k'), 'no executive figures in DOM');
      await page.screenshot({ path: path.join(SHOTS, `${vp}-08-command-center-sales-rep.png`), fullPage: vp === 'mobile' });
      const m = await ask(page, 'What is our gross margin this month?');
      assert(await m.reply.locator('[data-notice=permission_denied]').count() === 1, 'denied');
      assert(!(await m.reply.innerText()).includes('31.4%'), 'no leak');
      await page.screenshot({ path: path.join(SHOTS, `${vp}-09-permission-denied.png`) });
      await page.goto(`${BASE}/logistics`);
      await page.waitForURL((u) => !u.pathname.startsWith('/logistics'));
      return { sections };
    },
  },
  {
    id: 'connector-outage', role: 'owner', outage: 'QuickBooks', title: 'Connector outage',
    async run(page, vp) {
      await page.goto(`${BASE}/lola`);
      await page.locator('[data-section=performance]').waitFor();
      assert(await page.locator('[data-section=performance][data-availability=unavailable]').count() === 1, 'performance marked unavailable');
      const hero = await page.getByTestId('command-center').innerText();
      assert(!/revenue is up|\$48\.2k|6\.1%/i.test(hero), 'no QuickBooks figures shown while QuickBooks is down');
      const m = await ask(page, 'Which invoices are overdue?');
      assert(await m.reply.locator('[data-notice=connector_outage]').count() === 1, 'outage notice');
      assert(await m.reply.getByText('INV-10442').count() === 0, 'no invoice data while connector down');
      assert(await m.reply.locator('[data-availability=unavailable]').count() >= 1, 'card marked unavailable');
      assert(await m.reply.getByRole('button', { name: /approve/i }).count() === 0, 'no action offered on missing data');
      assert(!/as of/i.test(await m.reply.innerText()), 'unavailable data carries no freshness timestamp');
      const orb = await page.getByTestId('lola-status').first().innerText();
      await page.screenshot({ path: path.join(SHOTS, `${vp}-10-connector-outage.png`) });
      return { orbStatus: orb };
    },
  },
  {
    id: 'voice-interruption', role: 'owner', voice: true, title: 'Voice: turn-taking, barge-in, correction',
    async run(page, vp) {
      await page.goto(`${BASE}/`);
      await openPanel(page);
      assert(await page.evaluate(() => !window.__recStarted), 'mic not started before tap');
      const mic = page.getByTestId('mic-button');
      await mic.click();
      assert((await page.getByTestId('lola-status').innerText()).match(/listening/i), 'orb listening');
      const t0 = Date.now();
      await page.evaluate(() => window.__say('Where is container MSKU 448812?'));
      // Correction while Lola is still working on the first question:
      await page.getByTestId('lola-reply').first().waitFor();
      await mic.click();
      await page.evaluate(() => window.__say('Actually, which invoices are overdue?'));
      await page.waitForFunction(() => [...document.querySelectorAll('[data-testid=lola-reply]')].pop()?.getAttribute('data-status') === 'complete', null, { timeout: 15000 });
      const statuses = await page.getByTestId('lola-reply').evaluateAll((els) => els.map((e) => e.getAttribute('data-status')));
      assert(statuses[0] === 'interrupted' && statuses[1] === 'complete', `first superseded, second complete: ${statuses}`);
      await page.waitForFunction(() => window.__spoken.length > 0);
      const spoken = await page.evaluate(() => window.__spoken.join(' '));
      assert(/five invoices/i.test(spoken), 'speaks the corrected answer');
      assert(!/MSKU/i.test(spoken), 'never speaks the interrupted answer');
      const tSpoken = Date.now();
      // Barge-in: tap the mic while she is speaking.
      const before = await page.evaluate(() => window.__cancelCount);
      await mic.click();
      const after = await page.evaluate(() => window.__cancelCount);
      assert(after > before, 'speech cancelled on barge-in');
      await page.screenshot({ path: path.join(SHOTS, `${vp}-11-voice-correction.png`) });
      latency.push({ workflow: 'voice-correction', vp, firstMs: null, totalMs: tSpoken - t0 });
      return { statuses };
    },
  },
  {
    id: 'keyboard-and-reduced-motion', role: 'manager', reducedMotion: true, title: 'Keyboard access + reduced motion',
    async run(page, vp) {
      await page.goto(`${BASE}/`);
      await page.getByTestId('lola-launcher').waitFor();
      await page.keyboard.press('Control+k');
      await page.getByTestId('lola-panel').waitFor();
      assert(await page.evaluate(() => document.activeElement?.id === 'lola-input'), 'focus moves to input');
      await page.keyboard.type('Any logistics exceptions?');
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => [...document.querySelectorAll('[data-testid=lola-reply]')].pop()?.getAttribute('data-status') === 'complete', null, { timeout: 15000 });
      const anim = await page.evaluate(() => getComputedStyle(document.querySelector('.lola-orb__core')).animationName);
      assert(anim === 'none', `orb animation disabled under reduced motion (got ${anim})`);
      await page.keyboard.press('Escape');
      await page.getByTestId('lola-panel').waitFor({ state: 'detached' });
      await page.waitForFunction(() => document.activeElement?.getAttribute('data-testid') === 'lola-launcher', null, { timeout: 2000 }).catch(() => { throw new Error('focus returns to launcher'); });
      return { orbAnimation: anim };
    },
  },
];

(async () => {
  const browser = await chromium.launch();
  const a11y = [];
  for (const [vp, opts] of Object.entries(VIEWPORTS)) {
    for (const w of WORKFLOWS) {
      const ctx = await browser.newContext({ ...opts, reducedMotion: w.reducedMotion ? 'reduce' : 'no-preference' });
      await asRole(ctx, w.role, { outage: w.outage });
      if (w.voice) await ctx.addInitScript(voiceShim);
      const page = await ctx.newPage();
      const errors = [];
      page.on('pageerror', (e) => errors.push(e.message));
      const t0 = Date.now();
      try {
        const detail = await w.run(page, vp);
        assert(errors.length === 0, `page errors: ${errors.join('; ')}`);
        results.push({ id: w.id, title: w.title, role: w.role, viewport: vp, pass: true, ms: Date.now() - t0, detail });
      } catch (e) {
        await page.screenshot({ path: path.join(SHOTS, `FAIL-${vp}-${w.id}.png`) }).catch(() => {});
        results.push({ id: w.id, title: w.title, role: w.role, viewport: vp, pass: false, ms: Date.now() - t0, error: e.message });
      }
      await ctx.close();
    }
    // Accessibility scans
    for (const [role, url, panel] of [['owner', '/lola', false], ['owner', '/', true], ['sales_rep', '/lola', false]]) {
      const ctx = await browser.newContext(opts);
      await asRole(ctx, role);
      const page = await ctx.newPage();
      await page.goto(`${BASE}${url}`);
      if (panel) { await openPanel(page); await ask(page, 'Which invoices are overdue?'); }
      else { await page.locator('[data-section]').first().waitFor(); await ask(page, role === 'owner' ? 'Where is container MSKU 448812?' : 'Which of my accounts need a call?'); }
      // Scope to Lola's surfaces (pre-existing pages are out of this workstream's scope).
      await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
      const v = await page.evaluate(async () => {
        const roots = ['[data-testid=command-center]', '[data-testid=lola-panel]', '[data-testid=lola-launcher]'].map((s) => document.querySelector(s)).filter(Boolean);
        const out = [];
        for (const r of roots) {
          // eslint-disable-next-line no-undef
          const res = await axe.run(r, { resultTypes: ['violations'] });
          for (const x of res.violations) out.push({ id: x.id, impact: x.impact, nodes: x.nodes.length, target: x.nodes[0]?.target?.join(' '), summary: x.nodes[0]?.failureSummary?.split('\n')[1] });
        }
        return out;
      });
      a11y.push({ viewport: vp, role, page: url + (panel ? ' (panel)' : ''), violations: v });
      await ctx.close();
    }
  }
  await browser.close();

  const pct = (arr, p) => { const s = [...arr].sort((a, b) => a - b); return s.length ? s[Math.min(s.length - 1, Math.floor(p * s.length))] : null; };
  const firsts = latency.map((l) => l.firstMs).filter((x) => x != null);
  const totals = latency.map((l) => l.totalMs).filter((x) => x != null);
  const summary = {
    run_at: new Date().toISOString(),
    base_url: BASE,
    transport: 'demo (scripted, same contract; backend stubbed to 503)',
    passed: results.filter((r) => r.pass).length,
    failed: results.filter((r) => !r.pass).length,
    latency_ms: { first_feedback_p50: pct(firsts, 0.5), first_feedback_p95: pct(firsts, 0.95), complete_p50: pct(totals, 0.5), complete_p95: pct(totals, 0.95), samples: latency },
    accessibility: a11y,
    results,
  };
  fs.writeFileSync(path.join(OUT, 'results.json'), JSON.stringify(summary, null, 2));
  for (const r of results) console.log(`${r.pass ? 'PASS' : 'FAIL'}  ${r.viewport.padEnd(7)} ${r.id}${r.error ? '  — ' + r.error : ''}`);
  for (const a of a11y) console.log(`A11Y  ${a.viewport.padEnd(7)} ${a.role.padEnd(10)} ${a.page.padEnd(14)} ${a.violations.length ? a.violations.map((v) => `${v.id}(${v.impact}) ${v.target} ${v.summary ?? ''}`).join(' | ') : 'no violations'}`);
  console.log('latency', JSON.stringify(summary.latency_ms, (k, v) => (k === 'samples' ? undefined : v)));
  process.exit(summary.failed ? 1 : 0);
})();
