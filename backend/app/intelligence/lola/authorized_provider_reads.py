"""Authorized Gmail/Outlook history transport wrappers (metadata only).

These are backend-only adapters for already employee-owned OAuth connections.
The session's provider identity, mailbox/folder, consent and role must come
from trusted server-side source verification. The mutable reauthorize callback
must re-read current membership/consent; it cannot be a client-supplied bool.
No sender, recipient, subject, message body, token or URL is logged.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable, Mapping

import httpx

from app.intelligence.lola.identity_registry import ResolvedCrossChannelCase
from app.intelligence.lola.incremental_replay import (
    GmailHistoryExpired, ReplayUnavailable, _graph_url,
)
from app.intelligence.lola.replay_persistence import (
    CheckpointUnavailable, MailboxScope, scope_for_verified_case,
)

GRAPH_ORIGIN = "https://graph.microsoft.com/v1.0"


class AuthorizedReadUnavailable(ReplayUnavailable):
    """A scoped provider operation cannot be verified or completed."""


@dataclass(frozen=True)
class VerifiedEmailConnection:
    provider: str
    provider_identity_ref: str = field(repr=False)
    resource_id: str = field(repr=False)
    # Exact independently verified Gmail email or Graph /me user UUID.
    expected_provider_principal: str = field(repr=False)
    company_id: str
    employee_id: str
    mapping_revision: str


def binding_for_resolved_case(
    resolved: ResolvedCrossChannelCase,
    *,
    provider: str,
    resource_id: str,
    expected_principal: str,
) -> VerifiedEmailConnection:
    """Backend connection loader must supply independent principal evidence."""
    scope = scope_for_verified_case(resolved, provider=provider, resource_id=resource_id)
    if (not isinstance(expected_principal, str) or not expected_principal.strip()
            or len(expected_principal) > 256
            or any(ord(x) < 33 for x in expected_principal)):
        raise AuthorizedReadUnavailable("Verified provider principal unavailable")
    return VerifiedEmailConnection(
        provider=provider, provider_identity_ref=scope.provider_identity_ref,
        resource_id=scope.resource_id, expected_provider_principal=expected_principal,
        company_id=scope.company_id, employee_id=scope.employee_id,
        mapping_revision=scope.mapping_revision,
    )


def _verify_current(
    binding: VerifiedEmailConnection,
    reauthorize: Callable[[], ResolvedCrossChannelCase],
) -> MailboxScope:
    if not isinstance(binding, VerifiedEmailConnection) or not callable(reauthorize):
        raise AuthorizedReadUnavailable("Trusted employee connection missing")
    try:
        resolved = reauthorize()
        scope = scope_for_verified_case(
            resolved, provider=binding.provider, resource_id=binding.resource_id,
        )
    except Exception:
        raise AuthorizedReadUnavailable("Employee connection authorization changed") from None
    if (scope.company_id != binding.company_id or scope.employee_id != binding.employee_id
            or scope.mapping_revision != binding.mapping_revision
            or scope.provider_identity_ref != binding.provider_identity_ref):
        raise AuthorizedReadUnavailable("Employee provider ownership or consent changed")
    return scope


class GmailHistoryTransport:
    """Calls only users.getProfile and users.history.list on scoped Gmail OAuth."""

    def __init__(
        self, *,
        binding: VerifiedEmailConnection,
        connector,
        reauthorize: Callable[[], ResolvedCrossChannelCase],
    ):
        if binding.provider != "gmail":
            raise AuthorizedReadUnavailable("Incorrect provider transport")
        self.binding = binding
        self.connector = connector
        self.reauthorize = reauthorize

    def _read_sync(self, start_history_id: str, page_token: str | None) -> Mapping:
        _verify_current(self.binding, self.reauthorize)
        try:
            service = self.connector._get_service()
            profile = service.users().getProfile(userId="me").execute()
            if (not isinstance(profile, Mapping)
                    or not isinstance(profile.get("emailAddress"), str)
                    or profile["emailAddress"].casefold()
                    != self.binding.expected_provider_principal.casefold()):
                raise AuthorizedReadUnavailable("Authenticated Gmail identity mismatch")
            parameters = {
                "userId": "me", "startHistoryId": start_history_id,
                "maxResults": 100,
            }
            if page_token is not None:
                parameters["pageToken"] = page_token
            response = service.users().history().list(**parameters).execute()
            if not isinstance(response, Mapping):
                raise AuthorizedReadUnavailable("Gmail history response malformed")
        except AuthorizedReadUnavailable:
            raise
        except Exception as exc:
            try:
                from googleapiclient.errors import HttpError
                if isinstance(exc, HttpError) and exc.resp.status == 404:
                    raise GmailHistoryExpired("Gmail history cursor expired") from None
            except ImportError:
                pass
            raise AuthorizedReadUnavailable("Authorized Gmail history read failed") from None
        _verify_current(self.binding, self.reauthorize)
        return response

    async def fetch_page(self, start_history_id: str, page_token: str | None) -> Mapping:
        return await asyncio.to_thread(self._read_sync, start_history_id, page_token)




class GmailBootstrapTransport(GmailHistoryTransport):
    """Restricted metadata-only Gmail full sync, using employee OAuth identity."""

    def _read_snapshot_sync(
        self, kind: str, page_token: str | None = None,
        message_id: str | None = None,
    ) -> Mapping:
        _verify_current(self.binding, self.reauthorize)
        try:
            service = self.connector._get_service()
            profile = service.users().getProfile(userId="me").execute()
            if (not isinstance(profile, Mapping)
                    or not isinstance(profile.get("emailAddress"), str)
                    or profile["emailAddress"].casefold()
                    != self.binding.expected_provider_principal.casefold()):
                raise AuthorizedReadUnavailable("Authenticated Gmail identity mismatch")
            if kind == "profile":
                response = profile
            elif kind == "list":
                params = {"userId": "me", "maxResults": 100}
                if page_token is not None:
                    params["pageToken"] = page_token
                response = service.users().messages().list(**params).execute()
            elif kind == "get":
                if (not isinstance(message_id, str)
                        or not message_id or len(message_id) > 256):
                    raise AuthorizedReadUnavailable("Invalid Gmail metadata identity")
                response = service.users().messages().get(
                    userId="me", id=message_id, format="metadata",
                    fields="id,threadId,historyId",
                ).execute()
            else:
                raise AuthorizedReadUnavailable("Unsupported Gmail bootstrap operation")
        except AuthorizedReadUnavailable:
            raise
        except Exception:
            # Metadata 404 may be a concurrent message deletion or a revoked
            # token. Either case invalidates the snapshot; never skip a page.
            raise AuthorizedReadUnavailable("Gmail bootstrap request unavailable") from None
        if not isinstance(response, Mapping):
            raise AuthorizedReadUnavailable("Gmail bootstrap response malformed")
        _verify_current(self.binding, self.reauthorize)
        return response

    async def fetch_profile(self) -> Mapping:
        return await asyncio.to_thread(self._read_snapshot_sync, "profile")

    async def fetch_messages_page(self, page_token: str | None) -> Mapping:
        return await asyncio.to_thread(self._read_snapshot_sync, "list", page_token)

    async def fetch_message_metadata(self, message_id: str) -> Mapping:
        return await asyncio.to_thread(
            self._read_snapshot_sync, "get", None, message_id,
        )

class GraphFolderDeltaTransport:
    """Verifies Graph /me identity, then reads one folder's delta page."""

    def __init__(
        self, *,
        binding: VerifiedEmailConnection,
        connector,
        reauthorize: Callable[[], ResolvedCrossChannelCase],
        http_transport: httpx.AsyncBaseTransport | None = None,
    ):
        if binding.provider != "outlook":
            raise AuthorizedReadUnavailable("Incorrect provider transport")
        self.binding = binding
        self.connector = connector
        self.reauthorize = reauthorize
        self.http_transport = http_transport

    async def fetch_url(self, url: str) -> Mapping:
        _verify_current(self.binding, self.reauthorize)
        try:
            _graph_url(url, self.binding.resource_id)
        except ReplayUnavailable:
            raise AuthorizedReadUnavailable("Untrusted Graph continuation endpoint") from None
        try:
            headers = await self.connector._get_headers()
            if (not isinstance(headers, dict)
                    or not isinstance(headers.get("Authorization"), str)
                    or not headers["Authorization"].startswith("Bearer ")):
                raise AuthorizedReadUnavailable("Verified Graph authorization header missing")
            async with httpx.AsyncClient(
                transport=self.http_transport, follow_redirects=False, timeout=15,
            ) as client:
                profile = await client.get(
                    GRAPH_ORIGIN + "/me?$select=id", headers=headers,
                )
                if profile.status_code != 200:
                    raise AuthorizedReadUnavailable("Graph employee identity read denied")
                principal = profile.json()
                if (not isinstance(principal, dict)
                        or principal.get("id") != self.binding.expected_provider_principal):
                    raise AuthorizedReadUnavailable("Graph OAuth principal mismatched")
                response = await client.get(url, headers=headers)
                if response.status_code == 410:
                    raise AuthorizedReadUnavailable("Graph delta token expired; full baseline required")
                if response.status_code != 200:
                    raise AuthorizedReadUnavailable("Graph mailbox folder read unavailable")
                data = response.json()
                if not isinstance(data, dict):
                    raise AuthorizedReadUnavailable("Graph delta response malformed")
        except AuthorizedReadUnavailable:
            raise
        except Exception:
            raise AuthorizedReadUnavailable("Graph authorized delta read failed") from None
        _verify_current(self.binding, self.reauthorize)
        return data
