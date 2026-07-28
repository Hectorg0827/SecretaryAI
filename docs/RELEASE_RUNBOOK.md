# Release Runbook

Single, canonical release process. As of the workflow consolidation, the CI/CD
surface is exactly four workflows:

| Workflow | Trigger | Produces |
|---|---|---|
| `ci.yml` | every PR + protected-branch push | tests, lint, typecheck, build, fmt/clippy, dep-audit, — the pre-merge gate |
| `codeql.yml` | PR + push + weekly | SAST (python, js/ts) |
| `release-desktop.yml` | `workflow_dispatch` (version input) / `v*` tag | Windows `.exe`/`.msi` + macOS `.dmg`, uploaded as **run artifacts** and to a **draft** GitHub release |
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

## Enabling SIGNED releases (blocked on external credentials)

When the owner provides certificates, add these as repository **secrets** and
re-enable signing in `release-desktop.yml` (the signing env block + Tauri config
`signingIdentity` / `certificateThumbprint`, and set
`bundle.createUpdaterArtifacts: true`):

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
