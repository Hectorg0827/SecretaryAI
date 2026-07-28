# ADR 0001 — Desktop UI architecture

- **Status:** Accepted (owner-approved 2026)
- **Context finding:** Production-readiness defect #4

## Context

The desktop app (`desktop/`) previously rendered its main UI by embedding the
remote production website `https://app.secretaryai.com` in an `<iframe>` inside
the Tauri webview, and read the auth token from `localStorage`. This is broken
and insecure:

1. The Tauri CSP is `default-src 'self'` with **no `frame-src`**, so the iframe
   is blocked — the main UI never loads in a packaged build (#6).
2. The parent window and the remote iframe are **different origins**, so they do
   not share `localStorage`; the token cannot cross into the embedded app (#4).
3. The Rust sync loop reads the token from the **OS credential vault**, but the
   frontend only ever writes `localStorage` — nothing bridges them, so
   background sync always reports "no auth token" (#5).
4. Loading remote, updateable content into a webview that also exposes native
   Tauri commands is a **privilege-escalation risk**: a compromise of the remote
   site (or a MITM) could reach `shell`, `fs`, `http`, etc.

## Decision

**Bundle the UI natively.** The desktop app ships its React UI *inside* the
application bundle (built to `dist/`, served from `tauri://` / `'self'`) and
talks to the backend HTTP API directly. No remote website is embedded in the
privileged main window.

Consequences / implementation contract:

- **API URL** comes from a single variable, `VITE_API_URL`, resolved in
  `desktop/src/config.ts`. Production builds are **blocked** (fail-closed) if it
  is missing, `http://`, localhost, or a placeholder — enforced in
  `desktop/vite.config.ts`. (Closes #2/#3.)
- **Auth token** is stored in the **OS credential vault** (Keychain / Credential
  Manager) via the existing Rust `store_credential`/`get_credential` commands,
  under the key the sync loop already reads (`secretary-auth` / `token`), instead
  of `localStorage`. A thin desktop auth module wraps this so both the UI and the
  Rust background agent share one source of truth. (Closes #5.)
- **CSP** keeps `default-src 'self'`; `connect-src` allow-lists exactly the API
  origin, Supabase, and the WebSocket endpoint. No `frame-src` for remote apps.
  (Closes #6 by removing the iframe rather than allow-listing it.)
- If any hosted-web-content window is ever needed (e.g. an OAuth consent screen),
  it opens in a **separate window/webview with its own minimal capability set**
  and no access to privileged commands.

## Alternatives considered

- **Keep the remote iframe, harden it** (add `frame-src`, secure token handoff,
  origin isolation). Rejected: weaker trust boundary, depends on a hosted web app
  existing and staying in lockstep, and still mixes remote content with a
  privileged webview.

## Migration plan (tracked)

1. ✅ Unify API URL + fail-closed build guard; route `App.tsx` / `Setup.tsx`
   through `config.ts`. *(this ADR's first increment)*
2. ⬜ Add a desktop auth module that persists the session token to the credential
   vault on login and hydrates it on launch; remove `localStorage` token usage.
3. ⬜ Replace the `<iframe>` in `App.tsx` with the bundled app shell + views
   (reusing the `frontend/` components via a shared build or copied source).
4. ⬜ Tighten `capabilities/default.json` to least privilege and validate every
   privileged Rust command's arguments/authorization.
5. ⬜ End-to-end test: fresh launch → login (token in vault) → dashboard loads
   from bundled assets → background sync authenticates.
