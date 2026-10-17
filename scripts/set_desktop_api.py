#!/usr/bin/env python3
"""
Point the desktop bundle's Content-Security-Policy at the backend it is being
built against.

Why this exists
---------------
`desktop/src-tauri/tauri.conf.json` ships a CSP whose `connect-src` lists
`https://api.secretaryai.com` explicitly. The desktop UI talks to the backend
with the webview's own `fetch()` (see desktop/src/auth.ts — it does NOT use
tauri-plugin-http, which would bypass the webview CSP), so `connect-src` is
enforced against every API call the app makes.

The consequence, before this script existed: build the installer with
API_URL=https://something-else and the app installs, launches, renders the
sign-in form -- and every request is blocked by the CSP. Release builds have
the webview inspector disabled, so there is no console to see the violation in.
It presents as "sign in silently does nothing", which is a very expensive bug
to chase.

So the release workflow rewrites the CSP to match the API URL it is baking in.

Usage
-----
    python3 scripts/set_desktop_api.py https://api.example.com
    python3 scripts/set_desktop_api.py --check       # verify, change nothing

Idempotent: running it twice with the same URL is a no-op.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from urllib.parse import urlparse

CONF = pathlib.Path(__file__).resolve().parent.parent / "desktop" / "src-tauri" / "tauri.conf.json"

# The hosts the shipped CSP must always keep, regardless of which backend is
# targeted: Supabase is reached directly by the client SDK.
KEEP = ("https://*.supabase.co",)


def build_connect_src(api_url: str) -> str:
    parsed = urlparse(api_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SystemExit(
            f"error: expected an https:// URL with a host, got {api_url!r}"
        )
    origin = f"https://{parsed.netloc}"
    ws_origin = f"wss://{parsed.netloc}"
    parts = ["'self'", origin, ws_origin, *KEEP]
    # Preserve order, drop duplicates (an api URL equal to a KEEP entry).
    seen: list[str] = []
    for p in parts:
        if p not in seen:
            seen.append(p)
    return "connect-src " + " ".join(seen)


def rewrite(api_url: str) -> bool:
    conf = json.loads(CONF.read_text())
    csp = conf["app"]["security"]["csp"]
    if "connect-src" not in csp:
        raise SystemExit("error: tauri.conf.json CSP has no connect-src directive")

    new_directive = build_connect_src(api_url)
    new_csp = re.sub(r"connect-src[^;]*", new_directive, csp, count=1)

    if new_csp == csp:
        print(f"CSP already targets {api_url} — nothing to do")
        return False

    conf["app"]["security"]["csp"] = new_csp
    CONF.write_text(json.dumps(conf, indent=2) + "\n")
    print("CSP connect-src set to:")
    print(f"  {new_directive}")
    return True


# The hosts baked into the shipped CSP that do not resolve. A build for any
# other backend must rewrite the directive, so `--check` calls them out.
_UNRESOLVED_DOMAIN = "secretaryai.com"


def _is_under(source_expression: str, domain: str) -> bool:
    """
    True when a CSP source expression's host is `domain` or a subdomain of it.

    Deliberately not `domain in source_expression`: a substring test matches
    `https://secretaryai.com.example.net` and `https://not-secretaryai.com`
    too. It makes no practical difference to this advisory message, but the
    habit is how host-check bypasses get written, and CodeQL is right to flag
    it. Compare the parsed host instead.
    """
    host = urlparse(source_expression).netloc.lower()
    if not host:
        return False
    host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host  # strip any port
    return host == domain or host.endswith("." + domain)


def check() -> int:
    """Report the CSP's connect-src without changing anything."""
    conf = json.loads(CONF.read_text())
    csp = conf["app"]["security"]["csp"]
    m = re.search(r"connect-src[^;]*", csp)
    directive = m.group(0).strip() if m else "<none>"
    print(f"current: {directive}")
    hosts = [h for h in directive.split()[1:] if h.startswith(("https://", "wss://"))]
    stale = [h for h in hosts if _is_under(h, _UNRESOLVED_DOMAIN)]
    if stale:
        print(
            "note: the CSP still lists "
            + ", ".join(stale)
            + " — a build for a different backend must run this script, or the "
              "app's requests will be blocked at runtime."
        )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("api_url", nargs="?", help="https:// base URL of the backend")
    ap.add_argument("--check", action="store_true", help="print the current CSP and exit")
    args = ap.parse_args()

    if args.check:
        return check()
    if not args.api_url:
        ap.error("api_url is required unless --check is given")
    rewrite(args.api_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
