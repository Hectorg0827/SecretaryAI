"""
Multi-carrier shipment tracking connector.
Supports FedEx, UPS, and DHL via their public tracking APIs.

Company config keys (optional — only needed for authenticated endpoints):
  fedex_api_key, fedex_secret_key
  ups_client_id, ups_client_secret
  dhl_api_key
"""
from __future__ import annotations
import logging
from typing import Optional

log = logging.getLogger(__name__)


class ShipTrackConnector:
    """
    Unified shipment tracking across FedEx, UPS, and DHL.
    Carrier is auto-detected from tracking number format.
    Falls back to the FedEx/UPS/DHL API depending on available credentials.
    """
    def __init__(
        self,
        fedex_api_key: str = "",
        fedex_secret_key: str = "",
        ups_client_id: str = "",
        ups_client_secret: str = "",
        dhl_api_key: str = "",
    ):
        self._fedex_key = fedex_api_key
        self._fedex_secret = fedex_secret_key
        self._ups_id = ups_client_id
        self._ups_secret = ups_client_secret
        self._dhl_key = dhl_api_key

    def detect_carrier(self, tracking_number: str) -> str:
        """Heuristically detect carrier from tracking number format."""
        tn = tracking_number.strip().upper().replace(" ", "")
        if len(tn) == 12 and tn.isdigit():
            return "fedex"
        if len(tn) == 15 and tn.isdigit():
            return "fedex"
        if tn.startswith("1Z"):
            return "ups"
        if len(tn) in (10, 20) and tn.isdigit():
            return "dhl"
        if tn.startswith("JD"):
            return "dhl"
        return "unknown"

    async def track(self, tracking_number: str, carrier: Optional[str] = None) -> dict:
        """
        Track a shipment. Returns a standardised tracking dict:
        {
          tracking_number, carrier, status, description,
          estimated_delivery, last_location, events: [{timestamp, location, description}]
        }
        """
        carrier = carrier or self.detect_carrier(tracking_number)
        try:
            if carrier == "fedex" and self._fedex_key:
                return await self._track_fedex(tracking_number)
            elif carrier == "ups" and self._ups_id:
                return await self._track_ups(tracking_number)
            elif carrier == "dhl" and self._dhl_key:
                return await self._track_dhl(tracking_number)
            else:
                # Return a stub when credentials aren't configured
                return self._stub_result(tracking_number, carrier)
        except Exception as exc:
            log.error("Track %s (%s) failed: %s", tracking_number, carrier, exc)
            return self._stub_result(tracking_number, carrier, error=str(exc))

    async def _track_fedex(self, tn: str) -> dict:
        import httpx
        # FedEx OAuth2 token
        async with httpx.AsyncClient() as client:
            tok_resp = await client.post(
                "https://apis.fedex.com/oauth/token",
                data={"grant_type": "client_credentials", "client_id": self._fedex_key, "client_secret": self._fedex_secret},
                timeout=10,
            )
            tok_resp.raise_for_status()
            token = tok_resp.json()["access_token"]

            track_resp = await client.post(
                "https://apis.fedex.com/track/v1/trackingnumbers",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"trackingInfo": [{"trackingNumberInfo": {"trackingNumber": tn}}]},
                timeout=15,
            )
            track_resp.raise_for_status()
            data = track_resp.json()

        pkg = data.get("output", {}).get("completeTrackResults", [{}])[0].get("trackResults", [{}])[0]
        status = pkg.get("latestStatusDetail", {})
        events = [
            {
                "timestamp": e.get("date", ""),
                "location": e.get("scanLocation", {}).get("city", ""),
                "description": e.get("eventDescription", ""),
            }
            for e in pkg.get("dateAndTimes", [])
        ]
        return {
            "tracking_number": tn,
            "carrier": "fedex",
            "status": status.get("code", "unknown"),
            "description": status.get("description", ""),
            "estimated_delivery": pkg.get("estimatedDeliveryTimeWindow", {}).get("window", {}).get("ends", ""),
            "last_location": pkg.get("lastUpdateTime", ""),
            "events": events,
        }

    async def _track_ups(self, tn: str) -> dict:
        import httpx, base64
        creds = base64.b64encode(f"{self._ups_id}:{self._ups_secret}".encode()).decode()
        async with httpx.AsyncClient() as client:
            tok_resp = await client.post(
                "https://onlinetools.ups.com/security/v1/oauth/token",
                headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "client_credentials"},
                timeout=10,
            )
            tok_resp.raise_for_status()
            token = tok_resp.json()["access_token"]

            track_resp = await client.get(
                f"https://onlinetools.ups.com/api/track/v1/details/{tn}",
                headers={"Authorization": f"Bearer {token}", "transId": "secretaryai", "transactionSrc": "secretaryai"},
                timeout=15,
            )
            track_resp.raise_for_status()
            data = track_resp.json()

        pkg = data.get("trackResponse", {}).get("shipment", [{}])[0].get("package", [{}])[0]
        activity = pkg.get("activity", [])
        events = [{"timestamp": a.get("date", ""), "location": a.get("location", {}).get("address", {}).get("city", ""), "description": a.get("status", {}).get("description", "")} for a in activity]
        return {
            "tracking_number": tn,
            "carrier": "ups",
            "status": pkg.get("currentStatus", {}).get("code", "unknown"),
            "description": pkg.get("currentStatus", {}).get("description", ""),
            "estimated_delivery": pkg.get("deliveryDate", [{}])[0].get("date", "") if pkg.get("deliveryDate") else "",
            "last_location": events[0].get("location", "") if events else "",
            "events": events,
        }

    async def _track_dhl(self, tn: str) -> dict:
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api-eu.dhl.com/track/shipments?trackingNumber={tn}",
                headers={"DHL-API-Key": self._dhl_key},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

        shipment = data.get("shipments", [{}])[0]
        events = [
            {"timestamp": e.get("timestamp", ""), "location": e.get("location", {}).get("address", {}).get("addressLocality", ""), "description": e.get("description", "")}
            for e in shipment.get("events", [])
        ]
        status = shipment.get("status", {})
        return {
            "tracking_number": tn,
            "carrier": "dhl",
            "status": status.get("statusCode", "unknown"),
            "description": status.get("description", ""),
            "estimated_delivery": shipment.get("estimatedTimeOfDelivery", ""),
            "last_location": events[0].get("location", "") if events else "",
            "events": events,
        }

    def _stub_result(self, tn: str, carrier: str, error: str = "") -> dict:
        return {
            "tracking_number": tn,
            "carrier": carrier,
            "status": "credentials_not_configured" if not error else "error",
            "description": error or f"Connect your {carrier.upper()} account in Settings to track shipments",
            "estimated_delivery": "",
            "last_location": "",
            "events": [],
        }

    async def track_multiple(self, tracking_numbers: list[str]) -> list[dict]:
        """Track multiple shipments concurrently."""
        import asyncio
        tasks = [self.track(tn) for tn in tracking_numbers[:10]]
        return await asyncio.gather(*tasks, return_exceptions=False)
