# Backup & Restore

Recovery objectives, backup scope, and a restore drill for SecretaryAI.

## Objectives (targets)
- **RPO** (max data loss): ≤ 24h (daily), ≤ 1h once PITR is enabled.
- **RTO** (max downtime): ≤ 4h.

## What to back up
| Data | Mechanism | Frequency |
|---|---|---|
| Supabase/Postgres (all app data, auth, tokens, audit) | Supabase automated backups + Point-In-Time Recovery | continuous/daily |
| Object storage (uploads/ingested files, if used) | provider bucket versioning | continuous |
| Secrets / config | secret manager (out of band; NOT in git) | on change |
| Redis (cache, revocation, OAuth state) | **not** backed up — ephemeral by design | n/a |

Encryption at rest is provided by the managed providers; integration tokens are
additionally app-encrypted before storage.

## Enabling backups (owner action)
1. Supabase → Database → **Backups**: confirm daily backups; enable **PITR** on
   a paid tier for ≤1h RPO.
2. Verify backup encryption + retention (≥ 30 days recommended).
3. Store the restore credentials in the secret manager, not in the repo.

## Restore drill (run quarterly — REQUIRED before GA)
1. Provision a **staging** Supabase project (never restore into production first).
2. Restore the latest backup (or PITR to a chosen timestamp) into staging.
3. Point a staging backend at it (`SUPABASE_URL`/keys) and run:
   - `GET /health` → 200,
   - login as a seed user,
   - read dashboard/accounts (tenant-scoped data present),
   - `pytest` smoke subset.
4. Record: backup timestamp, restore duration (→ validates RTO), row counts vs
   expected, and any failures. File the drill report in the incident/ops log.
5. Roll back staging.

## Recovery procedures
- **Corruption/bad deploy:** redeploy the previous known-good GHCR image; if data
  is affected, PITR to just before the event.
- **Accidental deletion:** PITR to the timestamp before deletion.
- **Full region loss:** restore backup into a new project; update DNS/secrets.

> Status: procedures documented; the quarterly restore drill has **not yet been
> executed** — tracked as a G5 item in `PRODUCTION_READINESS.md` and required
> before a production release.
