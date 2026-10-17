# Phase 6 — backup/restore drill evidence

**Status: NOT EXECUTED. This is a template, not a drill result.**

Do not fill PASS before restoring a real managed Postgres backup in an
isolated staging project and comparing real records. The owner must arrange
a funded Supabase instance with backups and a person authorized to run a
restore. Follow `docs/BACKUP_RESTORE.md`.

| Field | Evidence |
|---|---|
| Start date/time (UTC) | NOT PERFORMED |
| Operator / observer | NOT ASSIGNED |
| Source backup and timestamp | NOT AVAILABLE |
| Staging restore destination | NOT AVAILABLE |
| Restore action and provider audit link | NOT AVAILABLE |
| Restore start/end and RTO | NOT MEASURED |
| Source vs restored row counts | NOT COMPARED |
| Tenant-isolation and login smoke | NOT RUN |
| Staging /health status | NOT OBSERVED |
| Rollback / staging teardown | NOT PERFORMED |
| Signed operator approval | NOT PROVIDED |

**Exit criterion 14: FAIL / owner-blocked.** No artificial fixtures, unit
tests or SQL migration validation count as a restore drill.
