# Phase 5 — external acceptance tests (NOT YET RUN)

These are actual E2E checks, not mocks. The workflows deliberately fail if a
required deployment or credential is missing. Do not claim criterion 5 or
integration acceptance until the runs finish green.

## Owner: browser E2E
1. Deploy the backend and web frontend to real HTTPS addresses, and verify
   the backend's /health response manually.
2. In the SecretaryAI GitHub repository, click **Settings → Secrets and
   variables → Actions → Variables → New repository variable**.
3. Add `E2E_WEB_URL` with the deployed staging *web frontend* HTTPS URL.
4. Add `E2E_EMAIL_DOMAIN` with a test-domain you control and are authorized
   to use for disposable signup accounts. The test creates a new tenant.
5. Click **Actions → Staging E2E and vendor contracts → Run workflow**.
6. Expand **Browser signup / login / dashboard** and retain its green run URL.

## Owner: vendor contracts
1. Obtain an Intuit QuickBooks Online **sandbox** company ID and OAuth access
   token from the vendor developer portal. They must refer to the same company.
2. Obtain a consented Google/Gmail test-account OAuth token with permission
   to read the Gmail profile.
3. Obtain a SendGrid test-account API key with permission to read /user/profile.
4. In GitHub click **Settings → Secrets and variables → Actions → Secrets**.
   Add `QBO_SANDBOX_REALM_ID`, `QBO_SANDBOX_ACCESS_TOKEN`,
   `GMAIL_SANDBOX_ACCESS_TOKEN`, `SENDGRID_SANDBOX_API_KEY`.
5. Click **Actions → Staging E2E and vendor contracts → Run workflow**.
6. Verify **both** jobs succeed. OAuth access tokens expire; reissue before
   rerunning if a vendor returns 401.

Do not paste provider tokens into an issue, PR, or public Actions variable.
The scripts call vendors read-only; no real financial transaction is created.
No run has been recorded as passing until explicitly observed in GitHub.
