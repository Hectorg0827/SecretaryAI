# Release Runbook

Single, canonical release process. As of the workflow consolidation, the CI/CD
surface is exactly four workflows:

| Workflow | Trigger | Produces |
|---|---|---|
| `ci.yml` | every PR + protected-branch push | tests, lint, typecheck, build, fmt/clippy, dep-audit, — the pre-merge gate |
| `codeql.yml` | PR + push + weekly | SAST (python, js/ts) |
| `release-desktop.yml` | `workflow_dispatch` (version input) / `v*` tag | Windows `.exe`/`.msi` + macOS `.dmg`, **SHA-256 checksums**, a **CycloneDX SBOM**, and a **signed build-provenance attestation** — uploaded as run artifacts + to a **draft** GitHub release |
| `docker-publish.yml` | push to default branch + `release: published` | backend + frontend Docker images (GHCR) |

> The previous `release.yml` and `tauri-build.yml` were removed — they raced with
> `release-desktop.yml` (duplicate releases / conflicting asset names, defect
> #13) and `release.yml` was additionally invalid YAML.

## Desktop release (current, unsigned)

1. Ensure the release commit is green on `ci.yml` and `codeql.yml`.
2. Actions → **Release Desktop App** → **Run workflow** → set the version
   (e.g. `v1.0.3`). The build fails closed if `VITE_API_URL` is missing/localhost.
3. Download installers from the run's **Artifacts**, or publish the created
   **draft** release (required because the repo uses *immutable releases* — a
   draft stays mutable while assets attach, then you publish it).
4. `docker-publish.yml` builds the backend/frontend images when the release is
   published.

## Enabling SIGNED releases (wired — just add the secrets)

Signing is now **wired into `release-desktop.yml`** and activates automatically
when the matching repository **secrets** are present — no workflow edit needed.
When a secret is absent that path stays off (unsigned build still works). Add
secrets in **Settings → Secrets and variables → Actions**; never paste values in
chat/PRs. What each enables:
- macOS signing + notarization ← the `APPLE_*` secrets (tauri-action signs/
  notarizes/staples automatically).
- Windows Authenticode ← `WINDOWS_CERTIFICATE` (+ password): the workflow imports
  the PFX and injects its thumbprint into `tauri.conf.json` at build time.
- Signed auto-updater artifacts ← `TAURI_SIGNING_PRIVATE_KEY` (+ password): the
  workflow flips `createUpdaterArtifacts` on (closes #11).

Required secret names:

| Secret | For |
|---|---|
| `WINDOWS_CERTIFICATE`, `WINDOWS_CERTIFICATE_PASSWORD`, `WINDOWS_CERTIFICATE_THUMBPRINT` | Authenticode signing (removes SmartScreen "unknown publisher") |
| `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID` | macOS signing + notarization |
| `TAURI_SIGNING_PRIVATE_KEY`, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | signed auto-updater artifacts |

Verification once signed:
- Windows: `signtool verify /pa /all /v <installer>.exe`.
- macOS: `codesign --verify --deep --strict`, `spctl -a -vvv`, `stapler validate`.
- Updater: test N→N+1 with a real signed draft; confirm a tampered/invalid
  signature is rejected (fail-closed).

## Rollback
- Desktop: users can reinstall the previous version's artifact; do not delete
  the prior release. A failed auto-update must not brick the app (test rollback).
- Backend: redeploy the previous GHCR image tag.

## Version source of truth
Backend uses `app.__version__`. Desktop/frontend versions live in
`tauri.conf.json` / `package.json`. These must all match the release tag —
reconcile before tagging (tracked: #24).

## Security dependency remediation checkpoint — 2026-10-17/18

Existing SecretaryAI PR #74, no merge or deployment. The front-end
`frontend/package-lock.json` was updated by one branch-restricted
GitHub Actions remediation after a clean `npm ci`, lint, typecheck,
Vitest and production build. Only semver-compatible `npm audit fix`
changes were committed; `--force` was deliberately **not** used.

- Frontend full-graph advisory count reduced **21 -> 11**:
  **11 high/9 moderate/1 low -> 6 high/5 moderate** (from
  remediation run `39087191570`). The dependency audit is NOT CLEAN.
- Remaining findings involve dev-tool transitive `braces` /
  `chokidar` /`tailwindcss`, `esbuild`/`vite`, and
  `postcss-selector-parser`; a production `react-router-dom`
  chain also has moderate findings. The `braces` high-severity
  advisory `GHSA-vfj7-8cjw-p6xm` explicitly lists **no patched
  release** as of Oct 17, 2026. No speculative override or silent
  suppress/allowlist is authorized.
- CI runs **separate** runtime-only frontend, full frontend,
  desktop and backend audits, and reports a red audit job if any
  is not clean. Ordinary PR CI still permits coding progress;
  `release-desktop.yml` and `docker-publish.yml` now enforce
  **hard, fail-closed** package security gates before building or
  publishing distributable artifacts.
- Release remains blocked until each remaining issue is fixed by a
  compatible, tested upgrade or has a *specific, owner-approved,
  time-limited risk decision* with verified package/runtime exposure.
  This commit does NOT grant an exception or production-release signoff.
- Need final exact-tip CI/CodeQL and production-closure audit proof
  after all changes. The prior release candidates' green badges do
  not certify the latest dependency lockfile.

