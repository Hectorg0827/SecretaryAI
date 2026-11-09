"""Deterministic entity resolution for Lola BI.

Matching order: exact source ID → exact normalized name → exact email (for
customers) → all query tokens contained in exactly one name. Anything matching
more than one entity is returned as ``ambiguous`` with candidates; Lola must
ask the user rather than pick one. No fuzzy/edit-distance guessing.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Mapping

_SUFFIXES = {"inc", "llc", "ltd", "co", "corp", "corporation", "company", "the", "sa", "de", "cv"}


def normalize_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(t for t in text.split() if t not in _SUFFIXES)


@dataclass
class EntityMatch:
    status: str  # "matched" | "ambiguous" | "not_found"
    entity: dict | None = None
    method: str | None = None
    candidates: list[dict] = field(default_factory=list)

    def describe(self, *, label_key: str = "name") -> dict:
        return {
            "status": self.status, "method": self.method,
            "entity": {"id": self.entity["id"], "name": self.entity.get(label_key)} if self.entity else None,
            "candidates": [{"id": c["id"], "name": c.get(label_key)} for c in self.candidates[:10]],
        }


def resolve(query: object, records: Iterable[Mapping], *, name_keys: tuple[str, ...] = ("name",),
            email_key: str | None = None) -> EntityMatch:
    rows = [dict(r) for r in records if isinstance(r.get("id"), str) and r.get("id")]
    if not isinstance(query, str) or not query.strip():
        return EntityMatch("not_found")
    q = query.strip()

    by_id = [r for r in rows if r["id"] == q]
    if len(by_id) == 1:
        return EntityMatch("matched", by_id[0], "source_id")

    nq = normalize_name(q)
    if nq:
        exact = [r for r in rows if any(normalize_name(r.get(k)) == nq for k in name_keys)]
        if len(exact) == 1:
            return EntityMatch("matched", exact[0], "exact_name")
        if len(exact) > 1:
            return EntityMatch("ambiguous", candidates=exact, method="exact_name")

    if email_key and "@" in q:
        email = q.lower()
        hits = [r for r in rows if isinstance(r.get(email_key), str) and r[email_key].strip().lower() == email]
        if len(hits) == 1:
            return EntityMatch("matched", hits[0], "exact_email")
        if len(hits) > 1:
            return EntityMatch("ambiguous", candidates=hits, method="exact_email")

    tokens = set(nq.split())
    if tokens:
        contains = [r for r in rows
                    if any(tokens <= set(normalize_name(r.get(k)).split()) for k in name_keys)]
        if len(contains) == 1:
            return EntityMatch("matched", contains[0], "all_tokens")
        if len(contains) > 1:
            return EntityMatch("ambiguous", candidates=contains, method="all_tokens")
    return EntityMatch("not_found")
