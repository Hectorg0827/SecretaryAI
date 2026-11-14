# Logistics / Lola coordinated staging acceptance

**Current status: PREVIEW ONLY — not deployed.**

This preview integrates the newer authenticated Lola backend, its tenant-scoped compliance registry, the compliance badge UI fix, and the Logistics Control Tower. It intentionally **does not** merge the independent Pricing branch or change Railway, the production environment, QBO, warehouses or suppliers.

## Evidence gates

1. Full CI and CodeQL must pass at the exact preview SHA.
2. Resolve the existing Railway staging patch (compliance UI) and choose a coordinated backend + web release commit. Do not overwrite other running staging branches or pinned deployments.
3. Confirm the approved Supabase staging project and that the `logistics_events` ledger is server-only; tenant and role enforcement is in backend APIs.
4. Obtain an authorized owner/manager staging login. Keep its token in a **private shell environment variable** `LIONBI_STAGING_BEARER_TOKEN` — never in ChatGPT, PRs, issue comments, CI inputs, or screenshots.
5. After an approved staging deployment, run these *GET-only* probes from the repo's `backend` directory:

   ```bash
   python scripts/phase1_authenticated_acceptance.py
   python scripts/logistics_authenticated_acceptance.py
   ```

   The logistics probe checks 14-stage event projections, tenant-scoped PO coverage, receipt crosschecks, and alerts. **Exit code 2 is intentional:** a result can establish read-only API consistency but cannot certify carrier, customs, WMS or accounting evidence.
6. Have a reviewer verify the same PO, BOL/container, shipment ID and lot/receipt document in the carrier or forwarder source, warehouse system and actual purchasing system. Independently check damaged, quarantine and sellable stock, and verify whether accounting reconciliation really exists.
7. Run negative paths: owner/manager role access; sales/viewer denial; cross-tenant 404; source outage 503; unknown and duplicate event; immutable confirmation; bad SKU/quantity; fake customs release; one PO across several containers; 1,001-event paging; unverified ETA.
8. Only after independent evidence and reviewer signoff may this feature be marked accepted. A green test suite, dashboard or presence of a purchase order is **not** final business certification.

### Baseline observed before integration
- Staging: **2 saved purchase orders, 1 posted receipt document, 0 logistics events**.
- Therefore **live shipment acceptance has not been completed**. Do not seed invented shipments into the real company merely to force a green test.

The executable smoke probe is deliberately non-mutating; review actual authorization context before invoking. The separate Phase 1 chat exercise writes conversation records and is not part of this GET-only smoke probe.
