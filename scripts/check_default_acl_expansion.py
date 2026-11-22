#!/usr/bin/env python3
"""Fail-closed default ACL expansion gate. No database connectivity or writes.

Input is a JSON array of rows produced by the documented read-only SQL query.
A reviewed baseline is a JSON array in the same normalized form. Existing
privileges are inventory only; additions require explicit review.
"""
import argparse
import json
from pathlib import Path

ROLES = {"PUBLIC", "anon", "authenticated"}
OBJECT_TYPES = {"r", "S", "f", "T", "n"}


def normalize(rows):
    if not isinstance(rows, list):
        raise ValueError("expected JSON array")
    result = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("expected object row")
        owner, schema, kind, grantee, privilege = (row[k] for k in
            ("owner", "schema", "object_type", "grantee", "privilege"))
        if not all(isinstance(v, str) and v for v in (owner, schema, kind, grantee, privilege)):
            raise ValueError("invalid default ACL entry")
        if kind not in OBJECT_TYPES:
            raise ValueError("unknown object type: " + kind)
        result.add((owner, schema, kind, grantee, privilege))
    return result


def compare(baseline, current):
    old, new = normalize(baseline), normalize(current)
    added = sorted(new - old)
    removed = sorted(old - new)
    exposed = [entry for entry in added if entry[3] in ROLES]
    return added, removed, exposed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--current", required=True, type=Path)
    args = parser.parse_args()
    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        current = json.loads(args.current.read_text(encoding="utf-8"))
        added, removed, exposed = compare(baseline, current)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, "invalid inventory: " + str(exc) + "\n")
    print(json.dumps({"added": added, "removed": removed, "client_exposure_added": exposed}, indent=2))
    if added:
        parser.exit(1, "FAIL: default privileges expanded; independent approval required\n")
    print("PASS: no default privilege expansion")


if __name__ == "__main__":
    main()
