# Lola acceptance report

**Run:** see `run_at` in [`results.json`](results.json) · **Suite:** [`frontend/e2e/acceptance.cjs`](../../../frontend/e2e/acceptance.cjs) · **Browser:** Chromium (Playwright)
**Viewports:** desktop 1440×900 and mobile (iPhone 13 emulation)
**Data:** scripted demo transport that speaks the same contract as the real backend. Every backend call was stubbed to `503`, so nothing could succeed by accident.

## Results: 18 / 18 passed

| Workflow | Role | Desktop | Mobile | What was verified |
|---|---|:-:|:-:|---|
| Owner daily briefing | owner | ✅ | ✅ | All 9 executive sections appear. "Brief me" returns metrics and a chart with the source and freshness shown |
| AR collection | owner | ✅ | ✅ | Overdue table → approve → "Running… not confirmed yet" → **Done · verified** receipt |
| Sales performance investigation | manager | ✅ | ✅ | Explanation card (drivers) plus bar chart |
| Container & delivery inquiry | sales_rep | ✅ | ✅ | Shipment timeline with the customs-hold exception highlighted. Rep cannot approve the customer notice |
| Warehouse receiving exception | back_office | ✅ | ✅ | No financial sections. Approved vendor claim shows **Submitted · awaiting confirmation**, *not* done |
| Approval & verified execution | owner / back_office | ✅ | ✅ | Covered by the two rows above (verified vs. unverified receipts) |
| Permission denial | sales_rep | ✅ | ✅ | Only rep sections. No executive figures anywhere in the DOM. Margin question refused. `/logistics` redirects away |
| Connector outage | owner | ✅ | ✅ | Performance and financial-risk sections marked *Unavailable*. Headline drops QuickBooks figures. Answer shows an outage notice, an empty "Data unavailable" card, no timestamp and no approve button |
| Voice: turn-taking, interruption, correction | owner | ✅ | ✅ | Mic stays off until tapped. Live transcript shown. A correction mid-answer marks the first turn *Interrupted*, and only the corrected answer is spoken. Tapping the mic while Lola speaks stops her |
| Keyboard + reduced motion | manager | ✅ | ✅ | Ctrl/⌘+K opens the panel and focuses the input. Enter sends. Esc closes and returns focus to the launcher. Orb animation is `none` under reduced motion |

## Accessibility (axe-core, scoped to Lola's surfaces)

| Page | Desktop | Mobile |
|---|:-:|:-:|
| Command center (owner, with an answer) | 0 violations | 0 violations |
| Conversation panel (owner, AR answer) | 0 violations | 0 violations |
| Command center (sales rep) | 0 violations | 0 violations |

Also built in:

- Screen reader support: `role=log` with polite live region for the conversation, `aria-live` status line, labelled dialog, a hidden data table behind every chart, and severity always given as text plus icon (never colour alone).
- Touch targets of at least 40px on mobile, safe-area padding, focus trap in the full-screen mobile panel.
- Chart colours checked with the dataviz palette validator against the dark surface `#131C2E`. Gold `#B0802C` and blue `#5E8BE0` pass the lightness, chroma, colour-blind separation (ΔE 24.9) and contrast checks.

## Latency (client pipeline)

| Metric | p50 | p95 |
|---|---|---|
| Enter → first visible response (status/text) | ~30 ms | ~40 ms |
| Enter → complete answer with cards | ~2.1 s | ~3.0 s |

**Caveat:** these numbers measure the client with the demo transport's scripted delays (650 ms per "working" step). They show that the UI adds almost no lag of its own and always shows feedback right away. They are **not** production numbers. Production latency depends on Workstream 1's orchestration and must be measured again against the real `/api/lola/turn` before GA.

## What these tests cannot prove (still to do)

- **Voice quality** (recognition accuracy, how natural the speech sounds) needs real devices and microphones. Headless runs use a simulated mic and speaker and check behaviour only: turn-taking, barge-in, nothing spoken for superseded turns, nothing spoken that wasn't sent.
- **Real screen readers** (VoiceOver, NVDA): axe checks the markup, but a manual pass is still recommended.
- **Live backend**: once `/api/lola/*` ships, run the suite with `VITE_LOLA_MOCK` unset against staging.

## Screenshots

[`screenshots/`](screenshots/) has 11 views at each viewport:

- `desktop-01-command-center-owner.png` / `mobile-01-…` — owner command center
- `…-02-owner-briefing-answer.png` — briefing answer with metrics and chart
- `…-03-ar-overdue-invoices.png`, `…-04-ar-verified-receipt.png` — collections and verified execution
- `…-05-sales-investigation.png`
- `…-06-container-timeline.png`
- `…-07-warehouse-unverified-receipt.png`
- `…-08-command-center-sales-rep.png`, `…-09-permission-denied.png`
- `…-10-connector-outage.png`
- `…-11-voice-correction.png`

## Re-run

```bash
cd frontend
npm run dev:demo          # terminal 1
npm run e2e:lola          # terminal 2 → updates results.json + screenshots
```
