# Publishing a desktop release (non-technical guide)

This is the short, click-by-click version of `RELEASE_RUNBOOK.md`. Nothing here
needs a terminal.

There are two things that must be true before an installer is useful:

1. **A backend must be running somewhere** (see `RAILWAY_DEPLOYMENT.md`).
   The desktop app is a client; it signs in to that backend.
2. **GitHub must know the backend's address** so it bakes it into the app.

---

## One-time setup (about 5 minutes)

### A. Tell GitHub where the backend is

Go to **Settings → Secrets and variables → Actions → Variables** and add:

| Variable name | Value |
|---|---|
| `API_URL` | Your backend address, starting with `https://` (for example a Railway URL such as `https://api-secretaryai.up.railway.app`). |
| `APP_URL` | Your web-app address, starting with `https://` (optional; used for "open in browser" links). |

The build **refuses to run** if `API_URL` is missing, uses `http://`, or
points at `localhost` — that is on purpose, so a broken app is never shipped.

### B. Make sure Actions is turned on

**Settings → Actions → General** → under *Actions permissions* choose
**Allow all actions and reusable workflows** → Save.

### C. (Optional, later) Signing certificates

Unsigned installers work fine but show a one-time OS warning on first launch.
To remove that warning you need certificates (Apple Developer ID, Windows
Authenticode). When you have them, add them as **Secrets** using the names in
`RELEASE_RUNBOOK.md`; the build turns signing on by itself.

The auto-updater signing key is already set up (its public key is committed in
`desktop/src-tauri/tauri.conf.json`). Add `TAURI_SIGNING_PRIVATE_KEY` as a
secret when you want automatic in-app updates to start working.

---

## Every release (about 20 minutes, mostly waiting)

1. Open **Actions** (top menu of the repo).
2. Click **Release Desktop App** in the left list.
3. Click **Run workflow** (right side) → type the version, e.g. `v1.0.1` → **Run workflow**.
4. Wait ~15 minutes. Three jobs run: *Build (macos-arm)*, *Build (macos-intel)*, *Build (windows)*.
5. When all three are green, you have two ways to get the installers:
   - **Fastest:** click the run → scroll to **Artifacts** at the bottom → download
     `SecretaryAI-installer-windows`, `SecretaryAI-installer-macos-arm`, etc.
   - **For users:** go to **Releases**. You will see a **Draft** release with the
     installers already attached. Click the pencil → **Publish release**.
     (The repo uses "immutable releases", which is why the build creates a
     *draft* first: the files must be attached before publishing, because after
     publishing nothing can be added.)

Once published, `https://github.com/Hectorg0827/SecretaryAI/releases/latest`
shows the `.dmg` and `.exe` files, and the download page in `docs/download.html`
finds them automatically.

---

## If something goes wrong

| What you see | What it means | Fix |
|---|---|---|
| "Release Desktop App" is not in the Actions list | Actions is disabled, or you are on a branch without the workflow | Step B above; the workflow lives on the default branch |
| Build fails with *Production build blocked: VITE_API_URL is missing or unsafe* | Step A was skipped | Add the `API_URL` variable and re-run |
| A release exists but only has "Source code" files | The release was created by hand instead of by the workflow | Delete it, then run the workflow (step "Every release") and publish the draft it creates |
| App installs but sign-in never works | The backend at `API_URL` is not deployed or not reachable | Deploy the backend (`RAILWAY_DEPLOYMENT.md`), check `https://<API_URL>/health` in a browser returns `{"status":"ok"...}` |
