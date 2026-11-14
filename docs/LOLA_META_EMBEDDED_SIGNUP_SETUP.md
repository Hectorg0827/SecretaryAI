# Lola · Meta WhatsApp Business App Coexistence implementation
**Staging-only setup checklist — November 2026**
**Meta approval: NOT GRANTED. No real number or credentials connected.**

## 1. Meta App permissions (external administrator action)

Create/identify the appropriate Meta Business Portfolio and Meta Developers app with WhatsApp Business Platform/Tech Provider use case. In Meta App Dashboard → App Review → Permissions and Features, request Advanced Access for:

- whatsapp_business_management — manage authorized WABAs, numbers, templates and subscriptions
- whatsapp_business_messaging — exchange authorized WhatsApp Business messages

Request permission review with the required descriptions, business-facing screencasts and compliance details. **A local setting cannot grant Meta permissions.** Official reference:
https://developers.facebook.com/docs/whatsapp/solution-providers/app-review/sample-submission/

## 2. Embedded Signup and employee number

Configure Meta Embedded Signup for **WhatsApp Business App Coexistence** using a Meta app ID and Configuration ID; where required select the whatsapp_business_app_onboarding feature. Each employee must themselves complete signup on an eligible existing WhatsApp Business App number, and choose optional history sharing.

The client must capture FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING, authorization code, WABA ID, and phone number ID. The server, not the browser, must exchange the code and independently validate the assets using Meta Graph (including is_on_biz_app and platform_type). Do not assign a phone to an employee based on self-entered phone/email or a client-submitted WABA ID.

If the Meta Business Account gives corporate administrators access to employee communications or delegated tokens, we cannot promise employee confidentiality; privacy signoff is required **before** registering the number.

Official overview: https://developers.facebook.com/documentation/business-messaging/whatsapp/overview/

## 3. Owner-specific consent and data isolation

The current PR #133 implements a private connection-request registry with company_id and user_id scope, a globally unique Meta phone-number ID constraint, employee-only consent and withdrawal endpoints, and optional history sharing defaulting off. **Local consent does not connect Meta or grant API access.**

The Meta asset verification and external policy clearance must be established by trusted server-side checks. No manager can consent, connect, read or retrieve on behalf of another employee through the Lola application.

## 4. Webhook setup

Official Meta App Dashboard → Webhooks → WhatsApp Business Account object. Configure an HTTPS callback and verification token; the server must validate Meta's verification challenge and X-Hub-Signature-256 HMAC before processing any payload.

Required subscriptions for the planned design:
- messages: normal incoming business messages and status updates
- smb_message_echoes: messages sent using the existing WhatsApp Business App
- account_update: connection/partner disconnect signals

Optional and separately consented:
- history: one-time message history, limited and not guaranteed available
- smb_app_state_sync: app state/contacts

Register the app with each authorized WABA. Do not subscribe personal employee numbers to a company-wide inbox. Never trust a webhook-provided phone ID without matching it against a server-side, Meta-verified employee asset mapping.

Official reference: https://developers.facebook.com/docs/graph-api/webhooks/getting-started/

## 5. Messaging policy and templates

Meta's Business Messaging Policy, updated September 23, 2026:
- Recipient WhatsApp messaging opt-in must be documented; opt-out enforced.
- A **verified inbound customer message** opens or resets the 24-hour freeform reply window. Outbound WhatsApp Business App echoes do not open it.
- At or after 24 hours since the latest verified customer message, send only an approved, purpose-matched WhatsApp message template.
- Even inside the 24-hour window, Lola's design requires explicit employee review before sending.
- The U.S. is **not an approved jurisdiction for messages promoting alcohol** under Meta's alcohol vertical list; a human approval or template cannot override that restriction.
- Unknown intent/classification, age verification where required, recipient country, and evidence quality must block a send.

Template status is queried through GET /<WABA_ID>/message_templates with whatsapp_business_management; do not assume draft/pending/paused templates are approved.

Official references:
https://whatsappbusiness.com/policy/
https://developers.facebook.com/docs/graph-api/reference/whats-app-business-account/message_templates/

## AI and employee privacy approval gate

WhatsApp Business Solution Terms contain **AI Provider**, AI training, and individual WhatsApp user profiling restrictions. Lola's cross-channel AI-based relationship memory is **not automatically permitted** merely because the Cloud API is technically available.

Require specific written Meta or BSP/Meta-approved legal determination for:
- Employee-private AI analysis of WhatsApp message content outside WhatsApp
- Cross-channel identity linking and per-employee relationship memory
- AI inference provider processing and no-training contracts
- WABA/Business Manager admin access and the requested no-management-surveillance architecture

Tracked in issue #135:
https://github.com/Hectorg0827/SecretaryAI/issues/135

Official terms:
https://www.whatsapp.com/legal/business-solution-terms/

## Implementation and release gates

Already in PR #133:
- Employee-private consent registry and APIs; no Meta activation
- Signed Meta webhook HMAC validation and fail-closed employee routing primitives (offline only)
- Message policy decision engine and tests (no sending)
- UI accurately says WhatsApp not connected, with employee-only connection request and withdrawal

Not yet connected: Meta app credentials, granted Advanced Access, Embedded Signup JS/Graph code exchange, actual WABA webhook subscriptions, live history/sync, message-send API, token custody or remote revocation.

Before enabling these functions: obtain Meta/BSP determination; run independent staging acceptance with test-only employee numbers; verify user isolation **including Meta Business Manager administrators**; obtain separate production authorization.

**Never paste App Secret, access tokens, phone credentials, webhook verify tokens, or customer chats in GitHub, issue comments or this document.**