# Lion BI accelerated integration control — 2026-11-19

**Scope:** staging source, isolated CI and draft-PR preparation. No production changes, main DB migrations/repair, external sends, financial postings, or unreviewed deployments.

## Authority and baseline
- The deployed staging web/backend source is the Pricing + Logistics candidate commit `65751b10ead773aacf3b1accac4c93b0638d97d8`. Existing Railway staging has a separate, **unapplied 16-change patch**; do not accept it as part of these PRs.
- Canonical Finance source recovery is draft PR #217. Its exact HEAD `c2bcc4b97c453ddbcf427ae8e692fc1aff56aad2` passed **local 46-migration replay twice**, synthetic tenant/RLS rollback tests, full CI and CodeQL on 2026-11-19. This is **not** a hosted clean-branch replay, migration history repair, application acceptance or release approval.
- Finance read-only AR candidate is PR #208, draft/unmerged, behind a default-OFF flag. The dedicated isolated Railway UAT app has a staged patch and has **not** deployed.
- This acceleration PR adds only a fast, low-privilege PR hygiene check. Its result is additive evidence and never a substitute for full CI/CodeQL, human DB/security review, authenticated acceptance or explicit release authorization.

## Parallel ownership: reuse existing work, do not duplicate features

| Lane | Existing source of truth | Smallest acceptance unit | Dependency |
| --- | --- | --- | --- |
| A — schema/security | #211, #215, #217, #212, #210, #216 | Fresh empty hosted clone reproduces schema; effective auth tests prove tenant and revocation controls | Local source replay verified; hosted clean replay and independent review next |
| B — Finance AR/AP | #174, #208 | Owner-authenticated QBO sandbox Invoice/Payment exact counts and cents; other roles denied | A, isolated UAT live; no postings |
| C — Operations | #142, #176 | PO → receipt → available/quarantine → claim → landed cost parity | A, source record mapping |
| D — Compliance/CRM | #186, #175 | Versioned NY/NJ/FL pricing posting preview verified by human regulatory reviewer; customer case flow | A, Compliance seven migrations and authority review |
| E — Lola intelligence | #117, #151, #190, #195 | One end-to-end sourced Finance AR answer with source IDs, timestamps, appropriate draft/approval/receipt status | B source contract, auth |
| F — UX/voice | #113, #118 | One full-screen conversational journey with real browser voice fallback, mobile and desktop QA | E API contract |
| Independent QA | #114, #120, #206 | Cross-tenant/regression negative cases, exact-head CI+CodeQL, deployment manifests, rollback evidence | Every lane, not self approval |

## Contract for all work packages
1. **Never implement from an assumption:** inspect the existing PR/commit and source code. Claim complete only when an exact tested commit and evidence exist.
2. **Avoid conflicted ownership:** one lane owns each changed file/API contract; request cross-lane contract review before changing shared schema.
3. **Small integration units:** target reviewable diffs with exact tests; use draft PRs; no self-approval. Integration controller checks ancestry and exact source SHAs.
4. **Fast feedback is not a release gate:** fail fast on syntactic and additive-migration mistakes; full CI, CodeQL, PostgreSQL acceptance and real-provider tests remain mandatory.
5. **Evidence rubric:** `implemented`, `unit-tested`, `isolated-provider-tested`, `staging-authenticated`, `approved`, `deployed`. Do not collapse stages into one “done”.
6. **Safe state:** external financial writes, prices, WhatsApp, email, regulatory submissions and destructive company operations remain OFF until their specific permissions and sign-offs exist.
7. **48-hour release-train target:** integrate candidates at most every two days **only when all gates pass**. Never force a cadence by overriding a failed gate.

## Current prioritized queue (no guessed completion dates)
1. Independent review of #217 and subsequent **separately authorized** hosted disposable clean-branch replay; resolve remaining grant and migration history drift; independently validate cross-tenant auth.
2. Bring isolated Finance AR UAT through approved staging deployment with flag OFF, then authenticated Intuit sandbox owner GET-only reconciliation; publish redacted parity evidence.
3. Complete one vertical Lola AR explanation and proposal using exactly those reconciled sources.
4. Parallel-only lanes: staging Operations data parity, Compliance authority mapping, UX voice/browser performance. Do not route around dependencies.

## Progress measurement
Report every verified integration cycle: exact SHA, planned/accepted end-to-end business journeys, open P0/P1 count, median time PR-ready→QA-approved, failed/retried runs, sandbox source parity and rollback evidence. Targets such as “2–3x faster” are hypotheses until measured.

**Known orchestration limit:** the connected Railway tools can inspect existing cloud-agent tasks but cannot start a new agent task, and Railway Automations is not enabled. GitHub's previous Copilot bot assignment attempt returned 403. Draft issues and work plans are **not** proof that six autonomous engineering agents are running. Launch requires an enabled execution service/tool and a task ID; do not make fictitious claims.
