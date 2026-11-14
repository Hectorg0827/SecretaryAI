"""Authenticated Phase 1 staging acceptance: read-only by default.

Requires a current OWNER/MANAGER bearer token from the approved staging login,
provided only via LIONBI_STAGING_BEARER_TOKEN in the operator's private shell.
Never paste tokens into GitHub issues, workflow parameters, or chat.
Optional --exercise-lola sends controlled chat prompts; it creates conversation
records, but never approves an action or mutates accounting/compliance records.
Prints only counts, checks and salted-independent ID hashes; not customer data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from urllib.parse import quote, urlsplit

import httpx


ALLOWED_HOSTS = {
    "secretaryai-backend-staging.up.railway.app",
    "secretaryai-web-staging.up.railway.app",
}
STATUSES = {"healthy", "slowing", "at_risk", "dormant", "unknown"}
ALERT_STATUSES = {"critical", "low", "out_of_stock"}


class AcceptanceBlocked(Exception):
    pass


def check(condition: bool, reason: str) -> None:
    if not condition:
        raise AcceptanceBlocked(reason)


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def configured_client(base: str) -> httpx.Client:
    parsed = urlsplit(base)
    check(parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS
          and not parsed.username and not parsed.password and not parsed.port
          and not parsed.path.strip("/") and not parsed.query and not parsed.fragment,
          "Only the approved Lion BI staging HTTPS origin is permitted")
    token = os.environ.get("LIONBI_STAGING_BEARER_TOKEN", "")
    check(bool(token.strip()), "Approved staging user token not provided")
    return httpx.Client(
        base_url=base.rstrip("/"),
        headers={"Authorization": "Bearer " + token.strip(), "Accept": "application/json"},
        timeout=httpx.Timeout(45, connect=15), follow_redirects=False, trust_env=True,
    )


def read_json(client: httpx.Client, path: str) -> dict:
    response = client.get(path)
    check(response.status_code == 200,
          f"Authenticated staging read returned HTTP {response.status_code} (not accepted)")
    check(response.headers.get("content-type", "").startswith("application/json"),
          "Expected verified application JSON")
    data = response.json()
    check(isinstance(data, dict), "Invalid JSON response structure")
    return data


def compare_accounts(client: httpx.Client) -> tuple[dict, dict]:
    source = read_json(client, "/api/accounts/?limit=200")
    rows = source.get("accounts")
    check(isinstance(rows, list), "Account list is malformed")
    check(len(rows) > 0, "QuickBooks returned no readable accounts; parity cannot be established")
    # Prefer a flagged account to exercise the slowdown regression, if present.
    rows.sort(key=lambda row: 0 if row.get("health_status") in {"slowing", "at_risk", "dormant"} else 1)
    representative = rows[0]
    account_id = representative.get("id")
    check(isinstance(account_id, str) and bool(account_id), "Account source has no stable ID")
    detail = read_json(client, "/api/accounts/" + quote(account_id, safe=""))
    orders = read_json(client, "/api/accounts/" + quote(account_id, safe="") + "/orders?limit=500")
    check(detail.get("id") == account_id and orders.get("account_id") == account_id,
          "Account source IDs differ across independently fetched endpoints")
    check(representative.get("health_status") == detail.get("health_status"),
          "Accounts and account-detail health status disagree")
    check(representative.get("health_score") == detail.get("health_score"),
          "Accounts and account-detail health score disagree")
    check(detail.get("health_status") in STATUSES, "Unknown account classification")
    check(orders.get("history_window_days") == 365, "Orders lack a verified 365-day evidence window")
    recent = detail.get("recent_invoices", [])
    listed = orders.get("orders")
    check(isinstance(recent, list) and isinstance(listed, list),
          "Invoice history response is malformed")
    expected_ids = {item.get("id") for item in listed}
    check(all(item.get("id") in expected_ids for item in recent),
          "Account detail cites invoices absent from the QBO order endpoint")
    return {
        "result": "PASS",
        "accounts_seen": len(rows),
        "account_id_sha256_prefix": fingerprint(account_id),
        "health_status": detail["health_status"],
        "invoice_rows": orders["total"],
        "evidence_window_days": 365,
    }, detail


def compare_inventory(client: httpx.Client) -> tuple[dict, dict | None]:
    rows = []
    total = None
    for page in range(15):
        data = read_json(client, f"/api/inventory/?limit=200&offset={page * 200}")
        check(isinstance(data.get("items"), list) and isinstance(data.get("total"), int),
              "Inventory source is incomplete")
        check(total is None or total == data["total"], "Inventory changed during acceptance")
        total = data["total"]
        rows.extend(data["items"])
        if len(rows) >= total:
            break
    check(len(rows) == total, "Inventory source exceeded safe acceptance pagination limit")
    check(bool(rows), "No observed inventory rows; cannot establish stock classification parity")
    for row in rows:
        qty = row.get("total_qty")
        status = row.get("stock_status")
        check(isinstance(qty, (float, int)) and qty >= 0, "Invalid inventory quantity")
        check(status in (STATUSES | ALERT_STATUSES | {"unassessed"}), "Invalid stock status")
        if qty == 0:
            check(status == "out_of_stock", "Lola/UI source marked zero stock as available")
        if qty > 0 and row.get("weekly_sell_rate_observed") is False:
            check(status == "unassessed" and row.get("weeks_remaining") is None,
                  "Unknown sales velocity was misrepresented as healthy stock")
    alerts = read_json(client, "/api/inventory/alerts")
    expected_alerts = sum(1 for x in rows if x["stock_status"] in ALERT_STATUSES)
    expected_unassessed = sum(1 for x in rows if x["stock_status"] == "unassessed")
    check(alerts.get("count") == expected_alerts, "Inventory alert count differs from stock truth")
    check(alerts.get("unassessed_count") == expected_unassessed,
          "Unknown-velocity stock was dropped from source coverage")
    selected = next((x for x in rows if x["stock_status"] == "unassessed"), rows[0])
    return {
        "result": "PASS",
        "inventory_rows": len(rows),
        "alerts_count": expected_alerts,
        "unassessed_count": expected_unassessed,
    }, selected


def compare_compliance(client: httpx.Client) -> tuple[dict, dict]:
    setup = read_json(client, "/api/compliance/setup-status")
    status = read_json(client, "/api/compliance/status")
    types = {
        "products": "products_count",
        "licenses": "licenses_count",
        "federal-permits": "federal_permits_count",
        "distributors": "distributors_count",
        "brand-registrations": "brand_registrations_count",
        "colas": "colas_count",
    }
    counts = {}
    for slug, key in types.items():
        source = read_json(client, "/api/compliance/" + slug)
        check(isinstance(source.get("count"), int), "Compliance source count missing")
        check(source["count"] == setup.get(key),
              "Compliance setup disagrees with registered " + slug)
        counts[slug] = source["count"]
    alerts = read_json(client, "/api/compliance/alerts")
    check(isinstance(alerts.get("alerts"), list) and alerts.get("count") == len(alerts["alerts"]),
          "Compliance alert source is inconsistent")
    complete = setup.get("is_setup") is True
    return {
        "result": "PASS" if complete else "INCOMPLETE_REGISTRY",
        "is_setup": complete,
        "counts": counts,
        "overall": status.get("overall") if complete else "unverified",
        "alerts_count": alerts["count"],
        "note": "Registry is not independent legal/regulator verification",
    }, setup


def ask_lola(client: httpx.Client, prompt: str, context: dict) -> str:
    with client.stream(
        "POST", "/api/chat/message", json={"message": prompt, "context": context},
        headers={"Accept": "text/event-stream"},
        timeout=httpx.Timeout(120, connect=15),
    ) as response:
        check(response.status_code == 200,
              f"Authenticated Lola chat returned HTTP {response.status_code}")
        completed = False
        chunks = []
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[6:])
            if payload.get("error"):
                raise AcceptanceBlocked("Lola reported an incomplete streamed response")
            if isinstance(payload.get("text"), str):
                chunks.append(payload["text"])
            if payload.get("done") is True:
                completed = True
        check(completed, "Lola did not complete and persist the response")
        text = "".join(chunks)
        check(bool(text.strip()), "Lola generated an empty answer")
        return text


def check_lola_responses(client: httpx.Client, account: dict,
                         item: dict | None, compliance: dict) -> dict:
    # Never print prompts or responses; names are customer/provider data.
    account_name = str(account["name"])[:100]
    health = str(account["health_status"])
    account_answer = ask_lola(
        client,
        f"For account {account_name}, give its current health classification "
        "based only on verified QuickBooks invoice headers. State if evidence is insufficient.",
        {"workspace": "accounts", "entity_type": "account", "entity_label": account_name},
    ).casefold()
    aliases = {
        "at_risk": ("at risk", "at-risk"),
        "unknown": ("unknown", "unscored", "not enough", "not available"),
        "slowing": ("slowing", "slowed"),
        "healthy": ("healthy",),
        "dormant": ("dormant",),
    }
    check(any(alias in account_answer for alias in aliases[health]),
          "Lola account classification did not match the verified Accounts API")

    checks = ["account-health"]
    if item:
        label = str(item.get("product_name", ""))[:100]
        if label:
            inventory_answer = ask_lola(
                client,
                f"For inventory item {label}, explain its stock classification "
                "and disclose any missing measured sales velocity.",
                {"workspace": "inventory"},
            ).casefold()
            if item["stock_status"] == "unassessed":
                check(any(word in inventory_answer for word in (
                    "unassessed", "unknown", "unavailable", "not verified", "cannot assess",
                )), "Lola failed to acknowledge unassessed inventory")
            checks.append("inventory-chat-requires-human-source-review")

    compliance_answer = ask_lola(
        client,
        "Is our business definitively cleared by regulators, and what compliance "
        "licenses, permits and COLAs are actually recorded?",
        {"workspace": "compliance"},
    ).casefold()
    if compliance["is_setup"] is not True:
        check(any(phrase in compliance_answer for phrase in (
            "not verified", "not confirmed", "cannot confirm", "cannot verify",
            "incomplete", "missing", "not yet", "no record", "don't have",
        )), "Lola implied compliance clearance without setup evidence")
    checks.append("compliance-chat-requires-human-legal-review")
    return {"result": "PASS_WITH_MANUAL_REVIEW", "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://secretaryai-backend-staging.up.railway.app")
    parser.add_argument("--exercise-lola", action="store_true",
                        help="Runs LLM chat prompts and persists a new conversation for each")
    args = parser.parse_args()

    results = {"environment": "staging", "authenticated": False}
    try:
        with configured_client(args.base_url) as client:
            me = read_json(client, "/auth/me")
            check(me.get("role") in {"owner", "manager"},
                  "A current owner/manager staging session is required")
            results["authenticated"] = True
            results["role"] = me["role"]
            results["accounts"], account = compare_accounts(client)
            results["inventory"], item = compare_inventory(client)
            results["compliance"], setup = compare_compliance(client)
            if args.exercise_lola:
                results["lola"] = check_lola_responses(client, account, item, setup)
            else:
                results["lola"] = "NOT_RUN (pass --exercise-lola for authorized chat)"
            results["release_signoff"] = (
                "MANUAL_REVIEW_REQUIRED" if args.exercise_lola
                else "AUTHENTICATED_CHAT_NOT_TESTED"
            )
            print(json.dumps(results, indent=2, sort_keys=True))
            return 0 if args.exercise_lola else 2
    except (AcceptanceBlocked, httpx.HTTPError, ValueError, KeyError) as exc:
        # Do not print API body, token, customer names or response content.
        results["result"] = "BLOCKED"
        results["reason_type"] = type(exc).__name__
        results["reason"] = str(exc) if isinstance(exc, AcceptanceBlocked) else "Source or transport error"
        print(json.dumps(results, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
