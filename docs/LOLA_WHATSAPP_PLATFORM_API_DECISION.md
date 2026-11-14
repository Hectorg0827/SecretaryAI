# Lola WhatsApp Business integration — platform decision (13 November 2026)

**Status: Conditional integration, staging-only; live WhatsApp messages/sends prohibited until explicit Meta policy approval and independent security signoff.**

## Recommended supported interface

Use **Meta WhatsApp Business Platform (Cloud API)**, with **WhatsApp Business App Coexistence** where each employee's existing WhatsApp Business app number is eligible. Never use a scraped WhatsApp Web session, phone mirroring, unofficial reverse-engineered socket API, consumer WhatsApp account, employee device backup, or QR-code-based impersonation.

Meta's WhatsApp Business Platform overview explicitly provides API solutions for WhatsApp Business app users using the same number:
- https://developers.facebook.com/documentation/business-messaging/whatsapp/overview/
- Technical coexistence reference (archived Meta developer documentation reproduction, verify against current portal before activation): https://support2.chatarchitect.com/books/meta-whatsapp/page/onboarding-whatsapp-business-app-users-aka-coexistence-developer-documentation

App coexistence involves Meta Embedded Signup and explicit consent to connect the number. One-time history sync is optional, time-limited and cannot promise unrestricted full-history access. The **messages**, **history**, **smb_app_state_sync**, **smb_message_echoes** and **account_update** webhook topics are relevant; their exact schema, eligibility, and Meta app review requirements must be reverified before implementation goes live.

## Product/terms constraints — the critical gate

Current WhatsApp Terms for WhatsApp Business Platform (effective 2026-09-23) incorporate WhatsApp's Business Messaging Policy and the **Meta Terms for WhatsApp Business Platform**:
- https://www.whatsapp.com/legal/WhatsApp-Terms-for-WhatsApp-Business-Platform?lang=en
- https://www.facebook.com/legal/Meta-Terms-for-WhatsApp-Business-Platform
- https://www.whatsapp.com/legal/business-solution-terms/
- https://whatsappbusiness.com/policy/

The WhatsApp Business Solution Terms' AI Providers clause restricts providers/developers whose primary offering via the Platform is a general-purpose AI assistant. AI used incidentally in legitimate business messaging is different, but Lola is itself primarily an AI assistant, so **do not treat this carve-out as approval**. The precise applicability of the platform terms to ingestion of an employee's business messages into Lola's off-WhatsApp intelligence engine requires written determination from Meta or an authorized solution partner before ingesting real message content. Regional exceptions must NOT be presumed for U.S. numbers.

The legacy Business Solution Terms also restrict using WhatsApp Business Solution Data to build or augment profiles about individual WhatsApp users, subject to an exception relating to message-thread content. **Lola's desired cross-channel identity linking/relationship memory therefore needs specific written clarification**: merely using a supported API does not establish the legality of merging WhatsApp contacts with email/project histories.
See https://www.whatsapp.com/legal/business-solution-terms/.

The terms prohibit using WhatsApp Business Solution Data (including derived forms) to train/improve third-party AI models. Vendor DPAs, no-training protections, clear retention controls and independent review are mandatory. Message handling for inference may also require separate consent, legal and Meta review.

Messaging policy: outbound customer-initiated replies within the 24-hour service window may be sent without a template; outside it, approved templates and other applicable opt-in/marketing rules apply. Lola must not autonomously message contacts; later private-send flows must require the individual employee's approval. See https://whatsappbusiness.com/policy/.

## Owner-and-consent boundary

Each employee keeps their own account/number and voluntarily authorizes a connection through Meta's official Embedded Signup. Do not automatically enroll all company numbers or give the company owner a dashboard of employee message data.

- A registered `phone_number_id` (not sender-provided names) is bound to one authenticated `company_id + user_id` in an employee-private connection registry.
- Enrollment requires **verified Meta business ownership** plus a documented employee consent and history-sharing choice. A claimed phone number alone is not proof.
- Disconnection immediately stops webhook processing and revokes delegated tokens; no retroactive sync without explicit permission.
- A manager can see authorized shared CRM/order facts only, never employee message text, private summaries, embeddings, usage-derived insights or reminder content.
- Meta Business Manager/WABA administrators and technical infrastructure operators may retain privileged platform-level access even if Lion BI blocks management UI. This **must be assessed before promising absolute confidentiality**. Prefer separately employee-controlled business assets and independently scoped technical access where supported; verify whether organization-owned WABAs violate the no-management-access requirement.
- Contacts are matched through employees' confirmed private identity links. Do not correlate employees merely because they share a customer.
- No historical-message sync by default. No media download by default. No group/Status/calls coverage claims. No general business analytics or training use of private message data.

## Staged implementation

1. **Now**: build only a pure, testable parser and HMAC-SHA256 signature verifier for officially signed Meta webhooks; no network listener, token, or data ingestion. Test field allowlist and cross-employee phone mapping.
2. **After written policy determination**: implement Meta partner onboarding and permission review (`whatsapp_business_management`, `whatsapp_business_messaging`), with employee-controlled Meta Embedded Signup and an ownership-verified `phone_number_id` registry; add replay-safe dedupe.
3. **After staging security signoff**: signed webhook listener with private-only routing, no company fan-out, verified sender contact, source timestamps, stripped PII in logs, privacy-preserving queue.
4. Test Joan's email request and WhatsApp draft with independent test numbers, then opt-in reminders and (separately) approved outbound drafts.

**Until gates 2–4 are satisfied, WhatsApp activity in Lola remains user-entered, labeled "manual", with no claim of WhatsApp account/API connection.**

## Written policy clearance questions for Meta / authorized BSP

Before collecting actual WhatsApp data, request a case-specific answer to all of the following:

1. Can an **employee's existing WhatsApp Business app number** use the official Coexistence API via Lion BI as Tech Provider/BSP, where Lola's main assistant runs in Lion BI (not within WhatsApp)?
2. Is processing that employee's WhatsApp message text/attachments for **private business commitment reminders** via an AI service (under no-training controls) permitted under the AI Providers clause?
3. Can the employee privately link a verified WhatsApp business correspondent to the same person's email address for thread/project continuity, or is that prohibited user profiling under the Business Solution Terms?
4. What Meta Business Account/WABA ownership and assignment configuration prevents corporate managers from independently reading employee chats or using delegated tokens?
5. Are message history sync and `smb_message_echoes` supported for our specific U.S. employee Business App numbers, and what employee/customer consent is required?
6. Must Lola obtain a separate Meta app review or permissions beyond `whatsapp_business_messaging` and `whatsapp_business_management`, and what is the allowed retention period?
7. Can the provider access history at all if employee declines optional history sharing? Lola must never treat missing history as absence of reply.
8. Can future **employee-approved** outbound follow-ups use the normal 24-hour customer-service window/approved templates, with no automated sends?

Record the written authorization and policy version in a reviewed, time-limited configuration before any staging API ingestion or customer message processing. A BSP's marketing statement is **not equivalent** to permission for Lola's distinct AI use case.
