# Gate A: CodeQL log-injection triage and staging auth parity

Scope: draft PR #75 (`workstream/01-core-20261026`), not the integration
branch, PR #74, Railway, production, or LYO.

## Staging authentication parity

Cherry-picked the *behavior* of staging-only auth fix
[`405a5db`](https://github.com/Hectorg0827/SecretaryAI/commit/405a5db77fcbc01aa11421fe9cdb378bbb02abd7)
into the isolated Gate A branch:

- Login queries and verifies `users.hashed_password`.
- Registration writes `users.hashed_password` and `users.full_name`.
- `/auth/me` reads `users.full_name`.
- Synthetic-only tests assert all three call boundaries. These tests do not
  prove live credentials, real SMTP, or Supabase permissions work.

## Security triage: 15 CodeQL inline findings

All 15 PR review threads flagged **CWE-117 / log injection**: attacker-derived
tenant IDs, email IDs, report types or source identifiers reached Python
logging as interpolation arguments. Parameterized `%s` logging does **not**
neutralize embedded CR/LF characters; a malicious value can forge log lines.

| Code scanning ID | File | Original line | Classification and change |
| --- | --- | ---: | --- |
| 116 | `app/api/dashboard.py` | 127 | Tenant ID + source value removed from failure log |
| 117 | `app/api/dashboard.py` | 217 | Tenant ID removed from refresh failure log |
| 118 | `app/scheduler/dashboard_snapshot.py` | 223 | Report type and tenant ID removed from persistence error log |
| 119 | `app/api/dashboard.py` | 450 | Email ID removed from mark-read failure log |
| 120 | `app/api/dashboard.py` | 472 | Tenant ID removed from sales-read failure log |
| 121 | `app/api/dashboard.py` | 536 | Tenant ID removed from prior-period failure log |
| 122 | `app/domain/policy.py` | 157 | Tenant ID removed from policy-load failure log |
| 123 | `app/domain/policy.py` | 174 | Tenant ID removed from feature-load failure log |
| 124 | `app/api/dashboard.py` | 579 | Tenant ID removed from notes-read failure log |
| 125 | `app/api/dashboard.py` | 610 | Tenant ID removed from notes-create failure log |
| 126 | `app/api/dashboard.py` | 649 | Tenant ID removed from notes-update failure log |
| 127 | `app/api/dashboard.py` | 503 | Tenant ID removed from malformed-sales failure log |
| 128 | `app/api/dashboard.py` | 168 | Tenant ID removed from pending-count failure log |
| 129 | `app/api/dashboard.py` | 176 | Tenant ID removed from unread-inbox failure log |
| 130 | `app/scheduler/dashboard_snapshot.py` | 343 | Report type and tenant ID removed from snapshot-read failure log |

Related logs in those **same three files** also stopped interpolating exception
text or identifiers, to avoid recreating the same vulnerability nearby. Log
messages remain fixed event descriptions (such as “Sales data fetch failed”)
and HTTP error semantics remain unchanged. Error objects continue to chain
through exceptions without being emitted as log payloads.

Regression tests enforce constant-only logger calls across the three touched
modules and exercise malicious newline-containing tenant/source/report strings
on synthetic failure paths. Trade-off: the fixed operational logs no longer
carry tenant or record identifiers. Correlation should use trusted request
context/telemetry; do not reintroduce unsanitized request data in log messages.

## Release boundary

- This change alone does **not** authorize merge or deployment.
- Wait for exact-tip PR CI/CodeQL and fresh five-workstream disposable
  integration results before deciding whether to request a separate merge.
- CodeQL workflow success is not, by itself, proof that all code scanning
  alerts have been dismissed; review the current findings and threads.
