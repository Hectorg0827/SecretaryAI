# Coordinated Lion BI Phase 1 staging candidate — Pricing + Logistics + Lola

**Candidate scope:** QA-only. This GitHub integration is **not** deployed by pushing the branch.

## Exact source provenance

- Pricing Phase 1 web + backend integration: `3cc656633e963ce31dd30b1a327cdb3735103fe8`
- Logistics Control Tower + role-scoped Lola + read-only purchasing coverage: `bb4542901bd95398f03ecc1539a99ead5a48bfa7`
- Both share the authenticated Phase 1 backend foundation `5591cf8b5fb61f8a0446b727ca0071fad59303b2`; both include the same compliance UI integrity patch.
- The three overlapping compliance UI paths have **identical blob hashes**, so no version was discarded. Logistics changes use distinct paths from Pricing beyond the identical UI files.

## Gates before any Railway deployment

1. Full CI and CodeQL **green on the exact combined commit**; the CI and security scans of each source branch are not a substitute.
2. Inspect the *entire* Railway staging patch immediately before committing. This staging environment is shared across workstreams, so a change in another tool/session may appear while working.
3. The staged backend AND web source must both be pinned to the **exact same combined commit**. No third-party service, environment, worker, cron, secret, variable, production, or LYO change is permitted.
4. Preserve and record rollback IDs for the current live backend/web deployments. Do not change the current production environment.
5. Deploy only after explicit separate approval for the reviewed Railway patch; verify both services report successful new deployments at the exact candidate commit.
6. Check unauthenticated denial and authenticated owner/manager, sales/viewer, and wrong-tenant authorization. Check Lola's pricing/compliance/logistics answers against the specific authoritative record; UI context is only a hint, not proof.
7. Run both `backend/scripts/phase1_authenticated_acceptance.py` and `backend/scripts/logistics_authenticated_acceptance.py` in the authorized operator environment. Keep bearer tokens out of GitHub, chat, logs, and PRs.
8. Shipment acceptance remains blocked until carrier, broker, WMS and posted warehouse receipt provenance match the actual PO/container/lot and inventory/QuickBooks ledger. **Do not create synthetic shipping records in the real tenant.**
9. Pricing acceptance requires shipment cost allocation, warehousing, freight, FX, duties, margin and role-based access parity. A green widget/route is not accounting certification. No automatic price-book or ERP write.

## Previous staging evidence

Read-only staging snapshot on 2026-11-14: 2 purchase orders, 1 posted warehouse receipt document and **0 logistics events**. Event table RLS enabled and raw authenticated SELECT/INSERT restricted.

**Never commit another workstream's staged Railway change under this release name.** If a mixed patch is present, prepare a new unified candidate preserving both intended features, or stop until the owner coordinates. No Railway deployment occurs from this document.
