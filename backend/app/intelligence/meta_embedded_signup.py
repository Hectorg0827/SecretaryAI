"""Server-side validation for official Meta Coexistence Embedded Signup.

Only usable after Meta App Review, employee consent and written AI policy
clearance. The token is used transiently for Graph asset verification and
is never returned to the browser, persisted or used for message access.
"""
from __future__ import annotations

import hmac
import hashlib
import re
from dataclasses import dataclass

import httpx

GRAPH_ORIGIN = "https://graph.facebook.com"
GRAPH_VERSION = "v27.0"
_META_ID = re.compile(r"^[0-9]{5,35}$")
_META_CODE = re.compile(r"^[A-Za-z0-9_.+|/-]{10,4096}$")


class MetaVerificationError(Exception):
    """Do not expose Meta errors or credentials to client responses."""


@dataclass(frozen=True)
class VerifiedMetaAsset:
    waba_id: str
    phone_number_id: str
    coexistence_confirmed: bool


def _meta_id(value: str) -> bool:
    return isinstance(value, str) and bool(_META_ID.fullmatch(value))


async def verify_meta_coexistence_signup(
    *,
    authorization_code: str,
    claimed_waba_id: str,
    claimed_phone_number_id: str,
    meta_app_id: str,
    meta_app_secret: str,
) -> VerifiedMetaAsset:
    """Verify browser-issued Meta assets against Graph using a one-time code.

    Claimed IDs are never accepted without Graph API confirmation.
    No tokens are written to any storage. No messages are read or sent.
    """
    if not (_meta_id(claimed_waba_id) and _meta_id(claimed_phone_number_id)
            and _meta_id(meta_app_id)
            and isinstance(meta_app_secret, str) and len(meta_app_secret) >= 16
            and isinstance(authorization_code, str)
            and bool(_META_CODE.fullmatch(authorization_code))):
        raise MetaVerificationError("Invalid Embedded Signup result")

    try:
        async with httpx.AsyncClient(
            base_url=GRAPH_ORIGIN, timeout=12, follow_redirects=False
        ) as client:
            exchange = await client.get(f"/{GRAPH_VERSION}/oauth/access_token", params={
                "client_id": meta_app_id,
                "client_secret": meta_app_secret,
                "code": authorization_code,
            })
            exchange.raise_for_status()
            access_token = exchange.json().get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise MetaVerificationError("Meta exchange did not confirm access")

            # App secret proof binds this token request to our configured app.
            appsecret_proof = hmac.new(
                meta_app_secret.encode(), access_token.encode(), hashlib.sha256
            ).hexdigest()
            headers = {"Authorization": f"Bearer {access_token}"}
            waba_numbers = await client.get(
                f"/{GRAPH_VERSION}/{claimed_waba_id}/phone_numbers",
                params={"fields": "id", "limit": 200,
                        "appsecret_proof": appsecret_proof},
                headers=headers,
            )
            waba_numbers.raise_for_status()
            response = waba_numbers.json()
            if not isinstance(response, dict):
                raise MetaVerificationError("Invalid WABA result")
            ids = [
                str(number["id"]) for number in (response.get("data") or [])
                if isinstance(number, dict) and number.get("id") is not None
            ]
            if (claimed_phone_number_id not in ids
                    or len(ids) != len(set(ids))
                    or response.get("paging", {}).get("next")):
                raise MetaVerificationError("WABA ownership was not confirmed")

            # Coexistence eligibility is checked on the individual phone node
            # rather than assuming every WABA-listed number is a Business App.
            phone = await client.get(
                f"/{GRAPH_VERSION}/{claimed_phone_number_id}",
                params={"fields": "id,is_on_biz_app,platform_type",
                        "appsecret_proof": appsecret_proof},
                headers=headers,
            )
            phone.raise_for_status()
            details = phone.json()
            if (not isinstance(details, dict)
                    or str(details.get("id")) != claimed_phone_number_id
                    or details.get("is_on_biz_app") is not True
                    or details.get("platform_type") != "CLOUD_API"):
                raise MetaVerificationError("Meta did not confirm Coexistence eligibility")

            return VerifiedMetaAsset(
                waba_id=claimed_waba_id,
                phone_number_id=claimed_phone_number_id,
                coexistence_confirmed=True,
            )
    except MetaVerificationError:
        raise
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        raise MetaVerificationError("Meta verification unavailable") from None
