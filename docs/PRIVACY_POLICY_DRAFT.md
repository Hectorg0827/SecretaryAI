# SecretaryAI Privacy Policy — COUNSEL REVIEW DRAFT

**DRAFT ONLY — NOT APPROVED, NOT EFFECTIVE, NOT PUBLISHED.**
Prepared October 17, 2026 from `docs/PRIVACY_AND_DATA_FLOW.md`.
Verify actual deployment architecture, controller/processor roles, vendors,
privacy rights and laws with counsel before publication.

## Who we are
SecretaryAI is an AI operations manager for importers and distributors. The
operating company's legal name, contact details, privacy contact, effective
date and coverage jurisdictions are **not confirmed** and must be completed.

## Data we process
- Registration: names, work email, role, password hashes and MFA information.
- Accounting: invoices, customer/vendor records, payment/balance and inventory
  information from QuickBooks or other customer-authorized integrations.
- Email: authorized Gmail content and email drafts/triage needed for features.
- AI/workflow: prompts, output, action approvals, timestamps and audit events.
- Computer-use: user-consented screen content; screenshots are described in
  the technical data-flow documentation as generally transient.
- Diagnostic data: error and operational telemetry; sensitive headers,
  cookies, bodies and personal identifiers are intended to be scrubbed.

## Purposes
Deliver integrations, display dashboards and briefings, prepare proposed
actions, apply user approvals, process subscriptions, detect abuse, safeguard
data and troubleshoot. Do not promise anonymization or zero AI-provider
retention without vendor-contract verification.

## Storage and vendors
Supabase (PostgreSQL), Redis, local encrypted desktop cache, Anthropic (AI),
Intuit/Conductor (QuickBooks), Google (Gmail), SendGrid (outbound email),
Stripe (billing), Sentry (telemetry) and hosting providers may process data.
Confirm exact vendors, subprocessors, hosting regions, transfer mechanisms
and contracts before launch; this is not a guaranteed exhaustive list.

## Security
The intended protections include tenant-scoped access, encrypted integration
tokens, password hashing, role controls, logged approvals and time-limited
revocation state. No network system is perfectly secure. Redis outages may
temporarily block authentication because revocation checks fail closed.

## Retention, deletion and export
Retention periods for company records, billing, logs and backups are **not yet
finalized**. Some audit/billing records may be retained for legal obligations.
A complete deletion/export implementation including vendor deletions is
identified as unfinished in the engineering documentation. Do **not** claim
a self-service deletion capability exists until independently verified.
Customer support and counsel must define a workable interim process.

## Privacy choices and requests
Users can choose which supported integrations they connect and can turn off
computer-use screen capture. Requests to access, correct, export or delete
personal data should be directed to the privacy contact **once supplied**.
Describe how identity is verified and how deadlines/legal exceptions are
handled before publishing.

## International processing and children's data
Cross-border processing may occur. Obtain legal review of transfer safeguards,
jurisdiction-specific notices and the relevant age/business-user policy.
No assumption of COPPA/GDPR/CCPA compliance is made by this draft.

## Changes and contact
The effective-date change-notification process and verified privacy support
contact must be added before publication.

## Publication blockers
Owner and counsel must approve: legal identity and contacts; lawful bases and
roles; retention schedule; vendor agreements and cross-border transfers;
cookies/tracking and marketing disclosures; rights request workflow; final
privacy links in the app and website.
