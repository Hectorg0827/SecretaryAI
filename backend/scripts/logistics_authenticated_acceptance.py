"""Logistics staging acceptance probe: read-only, authenticated, no fake events.

Runs against the ONLY allowed Phase 1 staging origin with an owner/manager
session. A valid API response is not independent proof of carrier arrival,
customs release, warehouse receipt attribution or QuickBooks reconciliation.

No calls here create, confirm, ingest, send, approve, or change business data.
"""
from __future__ import annotations

import argparse
import json
import sys
from urllib.parse import quote

import httpx

from scripts.phase1_authenticated_acceptance import (
    AcceptanceBlocked, check, configured_client, read_json,
)


def _rows(data: dict, key: str, count_key: str = "count") -> list[dict]:
    rows = data.get(key)
    check(isinstance(rows, list) and all(isinstance(x, dict) for x in rows),
          "Malformed source list")
    check(isinstance(data.get(count_key), int) and
          not isinstance(data.get(count_key), bool) and
          data[count_key] == len(rows), "Source list/count mismatch")
    return rows


def check_logistics_sources(client: httpx.Client) -> dict:
    """Pure GET comparison. Empty recorded journeys BLOCK acceptance."""
    listed = read_json(client, "/api/logistics/journey/shipments")
    shipments = _rows(listed, "shipments")
    check(len(shipments) <= 25,
          "Shipment count exceeds this read-only acceptance audit scope")
    coverage = read_json(client, "/api/logistics/journey/purchasing-coverage")
    orders = _rows(coverage, "orders", "purchase_order_count")
    check(isinstance(coverage.get("not_linked_count"), int),
          "Purchasing coverage is incomplete")
    check(coverage.get("shipment_status_verified_by_po_link") is False and
          coverage.get("inventory_posted_by_this_operation") is False,
          "Purchasing coverage contains unsupported shipment or inventory certification")
    monitor = read_json(client, "/api/logistics/monitor")
    alerts = _rows(monitor, "alerts")
    check(coverage.get("not_linked_count") <= len(orders),
          "Purchasing coverage has impossible counts")

    known_ids = set()
    for shipment in shipments:
        sid = shipment.get("shipment_id")
        check(isinstance(sid, str) and 0 < len(sid) <= 128 and sid not in known_ids,
              "Shipment identifiers are missing or duplicated")
        known_ids.add(sid)
        detail = read_json(
            client, "/api/logistics/journey/shipments/" + quote(sid, safe="")
        )
        check(detail.get("shipment_id") == sid, "Shipment list and detail identities differ")
        stages = detail.get("stages")
        check(isinstance(stages, list) and len(stages) == 14,
              "Fourteen-stage journey projection is incomplete")
        pos = detail.get("po_numbers")
        check(isinstance(pos, list) and all(isinstance(x, str) for x in pos),
              "Shipment PO references invalid")
        summary = read_json(
            client, "/api/logistics/journey/shipments/" +
            quote(sid, safe="") + "/purchasing-crosscheck"
        )
        check(summary.get("shipment_id") == sid and
              isinstance(summary.get("po_results"), list) and
              summary.get("posted_receipts_prove_shipment_received") is False and
              summary.get("shipment_inventory_reconciled") is False and
              summary.get("accounting_reconciled") is False,
              "PO crosscheck falsely certifies shipment-level receipt or accounting")
        check({x.get("po_number") for x in summary["po_results"]} == set(pos),
              "Shipment purchase references disagree with purchasing crosscheck")

    for order in orders:
        links = order.get("linked_shipment_ids")
        check(isinstance(links, list) and all(isinstance(x, str) for x in links),
              "PO-linked shipment ids invalid")
        check(set(links) <= known_ids,
              "PO references a journey not in the complete visible shipment list")
        check(order.get("shipping_status_verified") is False and
              order.get("container_receipt_verified") is False and
              order.get("warehouse_receipt_attributed_to_shipment") is False,
              "PO coverage falsely certifies carrier, container or warehouse arrival")

    return {
        "recorded_shipment_count": len(shipments),
        "saved_po_count": len(orders),
        "unlinked_po_count": coverage["not_linked_count"],
        "monitor_alert_count": len(alerts),
        "read_only_source_parity": "CHECKED",
        "carrier_customs_warehouse_evidence_independently_verified": False,
        "quickbooks_accounting_reconciled": False,
        "acceptance": (
            "BLOCKED_NO_RECORDED_SHIPMENTS" if not shipments
            else "SOURCE_PARITY_OBSERVED_EXTERNAL_EVIDENCE_PENDING"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://secretaryai-backend-staging.up.railway.app")
    args = parser.parse_args()
    outcome: dict = {"environment": "staging", "read_only": True}
    try:
        with configured_client(args.base_url) as client:
            me = read_json(client, "/auth/me")
            check(me.get("role") in {"owner", "manager"},
                  "Current approved staging owner/manager session required")
            outcome.update(check_logistics_sources(client))
            print(json.dumps(outcome, indent=2, sort_keys=True))
            # Source parity is *not* final release certification. This probe
            # always requires independent carrier/WMS and accounting signoff.
            return 2
    except (AcceptanceBlocked, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        outcome["acceptance"] = "BLOCKED"
        outcome["reason_type"] = type(exc).__name__
        outcome["reason"] = (str(exc) if isinstance(exc, AcceptanceBlocked)
                             else "Source or transport failure")
        print(json.dumps(outcome, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
