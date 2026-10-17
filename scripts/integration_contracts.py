"""Real sandbox/API contract checks for QuickBooks, Gmail, and SendGrid.

No mocks. Fails for missing credentials, HTTP errors, or unexpected JSON.
Never print secrets or response bodies. Operates read-only; no customer data
is mutated. Executed manually in the E2E workflow when credentials exist.
"""
import json
import os
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def need(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"BLOCKED: missing required secret {name}")
    return value


def get_json(name: str, url: str, token: str) -> dict:
    req = Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=25) as response:
            if response.status != 200:
                raise RuntimeError(f"{name} HTTP {response.status}")
            data = json.load(response)
    except (HTTPError, URLError) as exc:
        # Do not display response content; it may contain sensitive data.
        raise RuntimeError(f"{name} request failed: {type(exc).__name__}") from None
    if not isinstance(data, dict):
        raise AssertionError(f"{name} returned non-object JSON")
    return data


def main() -> None:
    realm = quote(need("QBO_SANDBOX_REALM_ID"), safe="")
    qbo = get_json(
        "QuickBooks sandbox",
        f"https://sandbox-quickbooks.api.intuit.com/v3/company/{realm}/companyinfo/{realm}",
        need("QBO_SANDBOX_ACCESS_TOKEN"),
    )
    assert isinstance(qbo.get("CompanyInfo"), dict), "QuickBooks contract missing CompanyInfo"
    print("PASS: QuickBooks sandbox CompanyInfo contract")

    gmail = get_json(
        "Gmail API",
        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        need("GMAIL_SANDBOX_ACCESS_TOKEN"),
    )
    assert isinstance(gmail.get("emailAddress"), str), "Gmail profile contract missing emailAddress"
    print("PASS: Gmail profile contract")

    sg = get_json(
        "SendGrid API",
        "https://api.sendgrid.com/v3/user/profile",
        need("SENDGRID_SANDBOX_API_KEY"),
    )
    assert isinstance(sg, dict) and sg, "SendGrid profile contract empty"
    print("PASS: SendGrid profile contract")


if __name__ == "__main__":
    main()
