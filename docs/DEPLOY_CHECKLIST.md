# Deploy SecretaryAI — owner's checklist

This is the step-by-step for getting a working backend online. It is written
for someone who does not write code. Everything is a click or a copy-paste.

**Why this blocks everything:** the desktop app has the backend's web address
baked into it when it is built. Until a real backend exists, the app installs
fine and then fails the moment anyone tries to sign in. Exit criteria 1–7 and
9 all wait on this.

**Time:** about 90 minutes of your attention, plus waiting on builds.

**Nothing here has been done for you.** No agent can create accounts, enter a
card, or point a domain. Steps marked **[you]** need you specifically.

---

## Before you start — what you need to buy or fetch

| # | Thing | Where | Cost |
|---|---|---|---|
| 1 | Supabase account (the database) | supabase.com | Free tier works to start; ~$25/mo for production |
| 2 | Railway account (runs the server) | railway.app | ~$5–20/mo, needs a card |
| 3 | Anthropic API key (the AI) | console.anthropic.com | Pay per use |
| 4 | SendGrid API key (sends the briefing emails) | sendgrid.com | Free tier ~100 emails/day |
| 5 | Intuit app credentials (QuickBooks) | developer.intuit.com | Free |

Steps 4 and 5 can wait — the backend starts without them, it just won't send
email or connect QuickBooks until they're set.

---

## Part A — Create the database (Supabase)

1. **[you]** Go to **supabase.com** and sign in.
2. Click **New project**. Name it `secretaryai`. Choose a region near your
   customers. Set a database password and **save it somewhere safe** — you
   cannot see it again, and you need it in Part C.
3. Wait for the project to finish provisioning (about 2 minutes).
4. In the left sidebar click **SQL Editor**, then **New query**.
5. Now apply the database structure. There are **eight files and the order
   matters** — the later files build on the first. For each file below,
   in order: open it on GitHub, click the **Copy raw file** button, paste it
   into the Supabase SQL Editor, and click **Run**. Wait for "Success" before
   starting the next one.
   1. `database/schema.sql`
   2. `database/schema_v2_additions.sql`
   3. `database/schema_v3_integrations.sql`
   4. `database/schema_v4_connector_secret_digest.sql` (migrates existing plaintext connector secrets to SHA-256 digests; apply before deploying the upgraded API)
   5. `database/schema_v5_stock_ledger.sql` (internal stock-ledger tables and protected posting procedure; **only after its exact-commit PostgreSQL CI and staging migration validation pass**).
   6. `database/schema_v6_purchasing_receiving.sql` (tenant-scoped purchase orders, separate approvals and exact-once receipts; **test the migration and rollback plan on staging first**).
   7. `database/schema_v7_purchasing_evidence.sql` (internal unverified procurement evidence metadata, **not** actual uploaded or verified documents).
   8. `database/schema_v8_evidence_integrity.sql` (private-quarantine byte-integrity records only; no malware or accounting authorization).
> **Migration safety:** For a real existing database, schedule a backup
> and test the chosen migration in staging before applying it in production.
> If your deployment already runs the Supabase migration files, do **not**
> paste the equivalent `database/schema_v5_stock_ledger.sql` into SQL Editor
> again. Never apply the manual, root-Supabase and backend-Supabase copies
> sequentially. This feature is not a publicly usable receiving workflow yet. Apply V6 only after V5, using the same chosen migration path.

6. In the left sidebar click **Settings → API**. Leave this tab open — you
   need three values from it in Part C: the **Project URL**, the **anon**
   key, and the **service_role** key.

> The `service_role` key bypasses all row-level security. Treat it like a
> password. Never put it in the desktop app, the website, or a chat message.

---

## Part B — Run the server (Railway)

You create **four** things here. Three of them are the application; people
routinely forget the third one and then wonder why no emails ever arrive.

1. **[you]** Go to **railway.app**, sign in with GitHub, and add a payment
   method (**Settings → Billing**). The free trial runs out and the services
   stop without warning.
2. Click **New Project → Empty project**. Name it `secretaryai`.
3. **Add Redis.** Click **New → Database → Add Redis**. Before considering production secure, verify the Redis service has durable persistence/AOF enabled. A revoked JWT must remain revoked after a Redis restart. If your Railway Redis plan cannot guarantee that, do **not** launch; use a durable revocation store or change the Redis deployment. See `docs/BACKUP_RESTORE.md`.
4. **Add the API service.** Click **New → GitHub Repo → SecretaryAI**.
   - Open the new service, go to **Settings**.
   - Under **Source**, set **Root Directory** to `backend`
   - Railway will find the `Dockerfile` by itself. Do not set a start command.
   - Go to **Variables** and enter everything from the Part C table.
   - Go to **Settings → Networking** and click **Generate Domain**.
     **Write down the address it gives you** (something like
     `secretaryai-production-a1b2.up.railway.app`). That is your API address
     and you need it in Parts C, D and E.
5. **Add the background worker.** Click **New → GitHub Repo → SecretaryAI**
   again (yes, the same repo — it runs different code).
   - **Settings → Source → Root Directory**: `backend`
   - **Settings → Deploy → Custom Start Command**:
     `celery -A celery_app worker --loglevel=info --concurrency=2`
   - **Variables**: the same values as the API service.
   - Do **not** generate a domain for this one.
6. **Add the scheduler.** Click **New → GitHub Repo → SecretaryAI** a third
   time.
   - **Settings → Source → Root Directory**: `backend`
   - **Settings → Deploy → Custom Start Command**:
     `celery -A celery_app beat --loglevel=info`
   - **Variables**: the same values again.
   - No domain.

> **Do not skip step 6.** The worker in step 5 only does work when something
> tells it to. The scheduler is the thing that tells it to. Without it the
> 7 AM morning briefing, the 2 AM overnight scan and the sync checks never
> run at all — the product looks alive and silently does nothing. This is
> exit criterion 7, and the older `RAILWAY_DEPLOYMENT.md` guide omits it.

---

## Part C — Environment variables

Put these in the **Variables** tab of **all three** services (API, worker,
scheduler). Railway lets you paste many at once with its **Raw Editor**.

### Required — the server refuses to start without these six

| Variable | What to put | Where it comes from |
|---|---|---|
| `SECRET_KEY` | A random 64-character hex string | Generate one at `https://generate-secret.vercel.app/64` — must be at least 32 characters or startup fails with an error saying so |
| `SUPABASE_URL` | `https://xxxx.supabase.co` | Part A step 6, "Project URL" |
| `SUPABASE_ANON_KEY` | Long string starting `eyJ...` | Part A step 6, "anon" |
| `SUPABASE_SERVICE_ROLE_KEY` | Long string starting `eyJ...` | Part A step 6, "service_role" |
| `DATABASE_URL` | `postgresql://postgres:YOUR-PASSWORD@db.xxxx.supabase.co:5432/postgres` | Supabase → Settings → Database; put the Part A step 2 password where it says `YOUR-PASSWORD` |
| `ANTHROPIC_API_KEY` | `sk-ant-api03-...` | console.anthropic.com → API keys |

### Required for this to behave like production

| Variable | What to put |
|---|---|
| `REDIS_URL` | `${{Redis.REDIS_URL}}` — type it exactly like that, braces included; Railway substitutes the real address |
| `DEBUG` | `false` |
| `CORS_ORIGINS` | `https://YOUR-API-ADDRESS` (from Part B step 4). The built-in default points at `app.secretaryai.com`, which does not exist — leaving it will block the app's requests |
| `FRONTEND_URL` | `https://YOUR-API-ADDRESS` for now; change it if you later put the web app on its own domain |

### Needed for email (exit criterion 7)

| Variable | What to put |
|---|---|
| `SENDGRID_API_KEY` | From sendgrid.com → Settings → API Keys |
| `FROM_EMAIL` | The address briefings come from. It must be a sender you have verified in SendGrid, or SendGrid rejects every send |

### Needed for QuickBooks Online (exit criterion 6)

| Variable | What to put |
|---|---|
| `INTUIT_CLIENT_ID` | developer.intuit.com → your app → Production keys |
| `INTUIT_CLIENT_SECRET` | Same place |
| `INTUIT_REDIRECT_URI` | `https://YOUR-API-ADDRESS/auth/qbo/callback` — must match **character for character** what you enter in the Intuit portal, or the connection fails with a redirect_uri mismatch |
| `INTUIT_ENVIRONMENT` | `production` |
| `INTUIT_WEBHOOK_VERIFIER_TOKEN` | Intuit portal → Webhooks. QuickBooks webhooks are rejected until this is set |

**[you]** Also go to developer.intuit.com → your app → **Redirect URIs** and
add the exact same `https://YOUR-API-ADDRESS/auth/qbo/callback` there.

### Required — billing (the server now refuses to start without these)

The paywall is live, so these are no longer optional in production. If
`BILLING_ENABLED` is `true` (the default) and `DEBUG` is `false`, the server
**refuses to start** unless both keys are present, and the deploy log says
exactly which one is missing.

| Variable | What to put |
|---|---|
| `STRIPE_SECRET_KEY` | Stripe Dashboard → Developers → API keys → Secret key |
| `STRIPE_WEBHOOK_SECRET` | Stripe Dashboard → Developers → Webhooks → add an endpoint pointing at `https://YOUR-API-ADDRESS/webhooks/stripe`, then copy its **Signing secret** |
| `STRIPE_PUBLISHABLE_KEY` | Same API keys page → Publishable key (safe to expose) |

> **Why it refuses rather than warning.** Without the secret key, billing used
> to fall back to a mock mode: checkout returned a fake
> `mock-stripe.example.com` URL and webhooks were ignored. That looks exactly
> like working software until you notice no money has arrived. Without the
> webhook secret, subscriptions never activate in the database, so customers
> who *did* pay still get refused.
>
> If you want to run free for a pilot, set `BILLING_ENABLED=false`. The paywall
> switches off with it, and the log says so on every start.

---

## Part D — Prove it actually works

Do not skip this. "The deploy went green" and "the product works" are
different claims.

**The quick version, in your browser:**

1. Open `https://YOUR-API-ADDRESS/health` in a private/incognito window.
2. You want to see exactly this, with `"status":"ok"`:
   ```json
   {"status":"ok","version":"1.0.6","checks":{"redis":"ok","db":"ok"}}
   ```
3. If you see `"status":"degraded"` instead, look at which dependency says
   `"error"`:
   - `"redis":"error"` → `REDIS_URL` is wrong. Check it is literally
     `${{Redis.REDIS_URL}}`.
   - `"db":"error"` → the Supabase keys are wrong, **or** the three SQL files
     in Part A step 5 were not all applied.
4. If you see Railway's "Application failed to respond" page, open the API
   service → **Deployments** → click the latest → **View logs**. A missing
   required variable prints a message naming exactly which one.

**The thorough version** (for whoever helps you with technical things) —
from a laptop, *not* from the server:

```
bash scripts/smoke_test.sh https://YOUR-API-ADDRESS
```

It checks DNS, the health of each dependency, that authentication is actually
enforced, that the API documentation is not publicly exposed, and that the
TLS certificate is valid. It prints `RESULT: GREEN` and exits 0 only when all
of that holds. **When it prints GREEN, exit criterion 1 is met** — and not
before.

---

## Part E — Build and publish the installers

Only once Part D is green. Building against a backend that isn't working
produces installers that have to be thrown away, because the address is baked
in permanently.

1. **[you]** Go to the repository on GitHub → **Settings** →
   **Secrets and variables** → **Actions** → the **Variables** tab →
   **New repository variable**.
   - Name: `API_URL`
   - Value: `https://YOUR-API-ADDRESS` (no trailing slash)
   - Click **Add variable**.

   The release workflow now **refuses to run** without this, instead of
   quietly building against a dead address the way it used to.

2. **[you]** Go to the **Actions** tab → **Release Desktop App** in the left
   sidebar → **Run workflow** button on the right.
   - Leave the branch as the default.
   - In the **version** box type the next version, e.g. `v1.0.7`.
   - Click the green **Run workflow**.

   *This step has to be you.* The automation account cannot start workflows —
   it has no permission to — and cannot push version tags.

3. Wait roughly 20–30 minutes. Three builds run in parallel (Windows, Mac
   Intel, Mac Apple Silicon). If one goes red, click it and read the first
   red step.

4. **[you]** Go to the **Releases** page. You will find a **draft** release
   with the installers attached to it. Check that four files are there:
   - `*_x64-setup.exe` (Windows installer)
   - `*_x64_en-US.msi` (Windows, for IT departments)
   - `*_aarch64.dmg` (Mac, M1/M2/M3)
   - `*_x64.dmg` (Mac, Intel)

5. **[you]** Click **Edit** on the draft, scroll down, click
   **Publish release**.

   > Publishing is one-way. This repository has immutable releases turned on,
   > so once published the files are frozen and nothing can be added. That is
   > why the workflow builds a draft first. Confirm step 4 before you click.

6. Check criterion 2 yourself: open the release link in a private browser
   window where you are not signed in to GitHub, and download one installer.
   If it downloads, criterion 2 is met.

---

## What is still blocked after all this

Finishing this checklist gets you exit criteria 1, 2 and makes 3, 4, 5
testable. These still need something only you can supply:

| Criterion | What it needs from you |
|---|---|
| 6 — QuickBooks syncs real data | One real importer willing to connect their books |
| 7 — Briefing matches QuickBooks | The same customer confirming the numbers |
| 9 — A real card is charged | Stripe account, live keys, and a real purchase |
| 12 — Installers are signed | Apple Developer ID ($99/yr) and a Windows Authenticode certificate (~$200–400/yr). Until then both operating systems show a scary warning on first launch |
| 14 — A restore has been done | A human watching a database restore, start to finish |

---

*Written against commit `9197f0a`. The required-variable list is taken from*
*`backend/app/config.py` and matches `backend/.env.example`. The schema file*
*order is the order `scripts/validate_schema.sh` applies them in, which CI*
*verifies on every commit.*

## V8 quarantined file upload / integrity-only check (2026-10-17)

- A new private-storage path `POST /api/logistics/evidence/{id}/upload`
  streams a maximum 8 MiB, validates PDF/PNG/JPEG signatures against the
  claimed extension, compares actual SHA-256 bytes to the existing immutable
  claim, requires `purchasing-evidence-quarantine` to exist with
  `public=false`, and writes with `upsert=false`.
- The service stores a distinct append-only `purchasing_evidence_integrity_checks`
  record through `attest_purchasing_evidence_integrity`, with actor,
  observed length, MIME and digest. Its `malware_scan_status=not_scanned`,
  `safe_to_view=false`, `financial_review_status=not_reviewed` and
  `accounting_reconciled=false` are *non-negotiable* truthful statuses.
- Read-only `GET /api/logistics/evidence/{id}/integrity` returns the current
  metadata without a download link or permission to open quarantine files.
- `database/schema_v8_evidence_integrity.sql` and the two alternative
  Supabase migrations contain the same schema. Run **one** migration path,
  after V7, not all copies. Staging migration/dry-run, bucket creation,
  bucket privacy, object lifecycle/retention, proxy upload size limits and
  antivirus scanning are **not performed here**.
- If object upload succeeds but SQL attestation fails, an orphaned private
  object can remain. Do not overwrite on retry. A restricted recovery and
  forensic deletion process is required before production.
- Neither checksum matching nor file signature is a malware scan or proof
  that freight values/invoices are authentic. Files are never approved for
  viewing, payment, landed costs, or QuickBooks posting.
- New real Postgres test `scripts/test_evidence_integrity.sql` and
  backend tests `backend/tests/test_evidence_upload_integrity.py`.
  The latest exact-commit CI and CodeQL **must pass**, then real private
  bucket integration and security/performance testing remain necessary.

