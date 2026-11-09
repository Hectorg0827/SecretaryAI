# Lola — Workstream 4

Lola is the main way people use Lion BI: **ask, understand, decide, act**, all in one conversation. She is part of SecretaryAI itself, not a separate chatbot. The existing department pages are still there for detailed review and admin.

## Where she lives

| Surface | How |
|---|---|
| **Web** (every page) | Floating gold orb, bottom-right. Press **Ctrl/⌘+K** or click it to open the conversation panel |
| **Command center** | `/lola` (also the sidebar's first item). `/chat` now opens this too, so old links keep working. Role-aware briefing beside the conversation |
| **Mobile / PWA** | The same web app. The panel becomes full-screen, the command center stacks vertically, and the sidebar becomes a menu drawer |
| **Desktop (Tauri)** | "Ask Lola" panel on the native dashboard. Text only, same contract. Approvals open Lion BI in the system browser so the privileged webview never runs business actions |

## Code map

```
frontend/src/lola/
  contract.ts         ← types shared with Workstream 1 (see CONTRACT.md)
  transport.ts        ← transport interface + SSE parser + demo-mode switch
  httpTransport.ts    ← real backend: /api/lola/* with honest legacy fallback
  mockTransport.ts    ← scripted demo data for the 9 acceptance workflows (EN/ES)
  store.ts            ← conversation state, honesty rules, latency metrics, orb state
  permissions.ts      ← role-based UI gating (backstop only)
  persona.ts          ← bilingual UI copy, language detection, starter prompts
  settings.ts         ← voice and privacy preferences
  voice/              ← speech-to-text (browser or server fallback), text-to-speech, barge-in
  components/         ← orb, dock/panel, command center, conversation, cards
  __tests__/          ← 48 component and logic tests
frontend/e2e/         ← Playwright acceptance suite
desktop/src/lola/, desktop/src/components/LolaPanel.tsx
```

## Run it

```bash
cd frontend
npm install
npm run dev:demo     # demo data, badged "Demo data" everywhere
npm run dev          # real backend (default)
npm test             # unit and component tests
npm run e2e:lola     # acceptance suite (needs dev:demo running)
```

Demo data turns on **only** with `VITE_LOLA_MOCK=1`, or with `?lola_demo=1` in a development build. A production build never switches to demo data on its own. To simulate an outage in demo mode, add `?lola_outage=QuickBooks` to the URL.

## Voice and privacy defaults

- The mic opens only when you tap it. It is never always-on. "Hands-free follow-ups" is opt-in.
- Speech uses the browser's built-in recognizer where available (Chrome, Edge, Safari). Otherwise it records once, stops after about 1.2 s of silence, and sends the audio to the existing `/api/chat/transcribe` endpoint. Audio is never stored on the device.
- Transcript retention: *Don't keep*, *This session only* (default), or *Save to my account*. Voice recordings: *Never keep* (default) or *30 days*. These choices are sent to the server with every turn, and company policy can be stricter.

## Docs

- [CONTRACT.md](CONTRACT.md) — API and permission contract with Workstream 1, plus open items for them
- [PERSONA.md](PERSONA.md) — Lola's personality and answer style
- [acceptance/REPORT.md](acceptance/REPORT.md) — test results, accessibility, latency, screenshots
