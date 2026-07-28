# SecretaryAI

AI operations manager for small importers and distributors. Conversational AI connected to QuickBooks (Desktop + Online), inventory, and email.

## ⬇ Download the desktop app

Download from the [GitHub Releases page](https://github.com/Hectorg0827/SecretaryAI/releases/latest):
- **macOS** → `SecretaryAI_x.y.z_aarch64.dmg` (Apple Silicon) or `*_x64.dmg` (Intel)
- **Windows** → `SecretaryAI_x.y.z_x64-setup.exe` — double-click, Next → Install → Finish

No command line, developer tools, or Docker required on your desktop.

> **Note — builds are currently unsigned.** On first launch you'll see a
> one-time OS warning (Windows SmartScreen → *More info → Run anyway*; macOS →
> right-click → *Open*). This is expected for unsigned apps and doesn't affect
> functionality. Code-signing + notarization (which remove the prompt) are
> planned — see `docs/RELEASE_RUNBOOK.md`.
>
> The backend runs in the cloud; the desktop app connects to it after sign-in.

## Architecture

```
frontend/          React web app + PWA
desktop/           Tauri desktop app (Windows + macOS) for QB Desktop users
backend/           FastAPI cloud backend
database/          Supabase/PostgreSQL schema
docs/              Technical documentation
```

## Quick Start

### Backend
```bash
cd backend
cp .env.example .env          # fill in your keys
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Frontend (web)
```bash
cd frontend
npm install
npm run dev
```

### Desktop app (requires Rust + Tauri CLI)
```bash
# Install Rust: https://rustup.rs
# Install Tauri CLI: cargo install tauri-cli
cd desktop
npm install
npm run tauri:dev

# Build for Windows
npm run tauri:build:windows

# Build for macOS (Intel)
npm run tauri:build:macos

# Build for macOS (Apple Silicon)
npm run tauri:build:macos-arm
```

### Database
Run `database/schema.sql` in your Supabase SQL editor.

## Cross-Platform Support

| Platform | Desktop App | Web App | QB Connection |
|----------|-------------|---------|---------------|
| Windows  | Installer (.exe/.msi) — unsigned (see note above) | ✅ | QB Desktop via Conductor |
| macOS    | App (.dmg) — unsigned/un-notarized | ✅ | QB Desktop via Conductor |
| iOS/Android | PWA (install from browser) | ✅ | QB Online only |

The desktop app targets platform-native credential storage — **Windows
Credential Manager (DPAPI)** and **macOS Keychain** — for secrets. Migrating the
session token off web storage into the vault is in progress (see
`docs/adr/0001-desktop-ui-architecture.md`).

## Environment Variables

See `backend/.env.example` for required configuration.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| AI | Claude API (Anthropic) |
| Backend | Python FastAPI |
| Database | Supabase (PostgreSQL) |
| Desktop | Tauri 2 (Rust + React) |
| Frontend | React + Vite + TailwindCSS |
| QB Desktop | Conductor API |
| QB Online | Intuit OAuth 2.0 REST API |
| Background jobs | Celery + Redis |
| Email | SendGrid |
