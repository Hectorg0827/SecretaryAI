# SecretaryAI — Solo-Founder Internal Release Approval Policy

**Version:** 1.0  
**Policy owner:** Hector Garcia (`Hectorg0827`)  
**Owner adoption date:** October 27, 2026  
**Status:** Owner approved; repository enforcement alignment pending verification. This document alone does not authorize any merge, staging deployment, or production deployment.

## 1. Purpose and scope

This policy defines a documented internal decision process for a solo-founder software project using AI-assisted development and technical review. It applies to the SecretaryAI repository (`Hectorg0827/SecretaryAI`) and the controlled integration target `program/secretaryai-integration-20261026`, including workstream PRs #75, #76, #77, #78 and #80.

This policy does **not** apply to LYO, any other project, or any customer's production environment. It is an internal engineering approval policy, not a claim of external audit, professional certification, regulatory compliance, or an independent human GitHub review. Any binding contract, law, regulator, or enterprise requirement for outside review takes precedence and must be separately satisfied.

## 2. Roles and independence

- **AI implementer:** Assists with source changes, tests, and remediation. All writes must remain within the explicitly authorized project and branch scope.
- **Independent AI technical challenger:** Reviews actual source and the exact current commit heads, tests security and data integrity, seeks regressions, and reports evidence and limitations. A model/session that authored a particular change may not be presented as independently reviewing that same change. Obtain a separate review of its final state.
- **Hector Garcia, sole release authority:** Evaluates the independent technical findings and GitHub evidence, documents unresolved risks, decides whether to accept an internal integration candidate, and grants or withholds **separate** authorization for each merge and deployment.

AI findings are advisory evidence. Neither AI reviews nor owner release sign-offs are represented as a GitHub `APPROVED` review by an independent human. Do not impersonate other identities, submit sham reviews, or claim a review gate passed when it did not.

## 3. Amendment to the prior review policy

For **SecretaryAI internal controlled integration only**, the previously self-imposed requirement for an eligible independent human GitHub approval on each workstream PR is replaced by the internal evidence-and-owner-sign-off process below, **once applicable repository protections are explicitly aligned**.

This does not retroactively make any previous GitHub review an approval. It does not circumvent an active GitHub ruleset or branch-protection restriction. If GitHub currently requires approving human reviews, stop and present the actual rules to the owner; propose a documented rules change for separate authorization. Do not use an administrator bypass as an undocumented substitute for this policy.

GitHub can require pull requests without requiring an approving review; PR authors cannot approve their own PRs. The intended repository configuration for this policy is: pull requests and required status checks enabled, required human approval count set to zero if legally and operationally permissible, review conversations resolved, and protected-branch deletion/force pushes disabled.

## 4. Mandatory technical merge gates

Every controlled integration proposal must have evidence attached to an owner decision record:

1. **Immutable target inventory:** Record repository, target branch and base SHA, the exact SHA of every PR head, and the intended merge method/order. Recheck heads immediately before execution; any changed head invalidates the previous review and test evidence.
2. **Source and security review:** Inspect architecture compatibility, authentication, authorization, tenant isolation, secret handling, business-data integrity, idempotency, connector safety, financial and inventory semantics, and any outstanding security alerts/review threads. Clearly label what remains unverified.
3. **Exact-head GitHub checks:** All required GitHub CI and CodeQL checks pass at the exact PR heads. Security and dependency audits must cover backend, frontend, desktop and mobile where applicable. Investigate failed, skipped, cancelled, stale, or unavailable checks; never count them as passing.
4. **Disposable combined integration:** Compose all intended workstreams locally or in isolated CI with no push or deployment. Require a conflict-free result, backend tests, relevant connector/finance/logistics regressions, security mutation checks, frontend lint/typecheck/tests/build, mobile and desktop checks, and applicable schema/database execution tests. State whether Redis, PostgreSQL, OAuth providers or other systems were simulated.
5. **Defect and risk disposition:** No unresolved P0/P1 technical defects. P2 issues that materially threaten tenant isolation, authorization, financial correctness, inventory integrity, or secret handling are blockers. Other known issues require an explicit owner risk entry (impact, evidence, mitigation, owner and release deadline). Existing high/critical dependency advisories must be triaged; they are not silently waived because they predate a PR.
6. **Documented owner decision:** Hector records an explicit `APPROVE CONTROLLED INTEGRATION` or `STOP` decision referencing the evidence and exact heads. Approval for review or a policy change is **not** approval to execute the merge.

No actor may execute a controlled merge until the owner separately authorizes that exact operation and applicable GitHub protections are satisfied.

## 5. Merge execution and verification

After explicit controlled-merge authorization: execute only the agreed integration merge without force pushing or deploying. Record the merged SHA, verify the intended contents, and rerun required regression/integration checks on that actual merged SHA. If the merged result fails, stop, preserve evidence, and request a separate remediation decision.

Do not assume the default repository branch, PR base, Railway deployment branch, and actual deployed SHA are the same. Confirm each independently.

## 6. Staging and production are distinct gates

**Staging deployment** requires a separate owner authorization after integration verification and staging safety preflight. It must affect SecretaryAI-Staging only, never LYO or production, and confirm that live customer communications, autonomous financial writes, and other irreversible tools are disabled unless specifically approved.

**Production/customer-ready release** requires a further, separately documented owner authorization, provider-backed testing, credentials/access review, a rollback plan, and business acceptance evidence. In particular:

- Reconcile the 1,200-case PO test: 1,180 good, 20 damaged, correct inventory and stock movements, replay safety, supplier-claim handling, and financial ledger posting/reconciliation without double counting.
- Exercise actual authorized sandbox or staging OAuth/connector paths (including QuickBooks and Outlook refresh), Stripe test webhooks, and tenant isolation. Synthetic tests alone are insufficient.
- Triage mobile dependency vulnerabilities and any remaining high-impact security findings; document mitigations and release-blocking exposures.
- Apply applicable OWASP ASVS L2 and financial control requirements as evidence, not as a self-certified claim.

No internal integration approval authorizes a Railway or Supabase deployment, customer messages, financial transactions, or production promotion.

## 7. Evidence and approval record

For each proposal, preserve a timestamped GitHub issue/PR comment or a committed release-gate record containing:

- Owner identity and decision date/time.
- Target branch and base SHA.
- Current PR numbers and **full** head SHAs.
- Independent technical review links and open findings.
- Exact CI/CodeQL run IDs and statuses.
- Disposable combined-test SHA/method, test counts, environments and limitations.
- Known risk register entries and mitigations.
- Decision: `STOP`, `APPROVE CONTROLLED INTEGRATION`, `APPROVE STAGING DEPLOYMENT`, or `APPROVE PRODUCTION DEPLOYMENT` — only one scope per approval.
- A sentence stating exactly which operation, if any, is authorized.

AI-generated reports may be attached but may not sign the owner's decision. Do not backdate approvals.

## 8. Change control and emergency exceptions

Changing branch protections, rulesets, CI requirements, approval counts, or this policy requires a separate owner request and an auditable record. Any emergency exception must state the specific control, justification, bounded scope, risk, compensating checks and expiry; it must not silently remove tenant/security/financial safeguards.

This policy can be revised prospectively by Hector. Earlier workstream technical reviews and test results remain historical evidence, not automatic authorization for future commits.

---

**Adoption record:** On October 27, 2026, Hector Garcia approved exploring and adopting the SecretaryAI Solo-Founder internal approval model. Repository-rule enforcement alignment and each future controlled merge/deployment remain separate actions requiring verification and explicit authorization.
