#!/usr/bin/env python3
"""Stage source-verified historical SQL in a disposable local Supabase checkout.

No network access, credentials, database operations or source-file mutations.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    rows = json.loads((base / "manifest.json").read_text())["migrations"]
    assert len(rows) == 46
    versions = [r["version"] for r in rows]
    assert versions == sorted(versions) and len(set(versions)) == 46
    assert sum(r["origin"] == "main-supabase-migration-history" for r in rows) == 40
    assert sum(r["origin"] == "github-finance-rc" for r in rows) == 6
    assert versions[:5] == [
        "20240101000000", "20240601000000", "20240901000000",
        "20261017000000", "20261017010000",
    ]
    target = args.target.resolve() / "supabase" / "migrations"
    target.mkdir(parents=True, exist_ok=True)
    assert not list(target.iterdir()), "Target has pre-existing migration files"
    for r in rows:
        name = r["filename"]
        assert re.fullmatch(r"\d{14}_[A-Za-z0-9_]+\.sql", name)
        assert r["version"] == name[:14]
        source = base / "migrations" / name
        content = source.read_bytes()
        assert len(content) > 20 and b"\x00" not in content
        if "git_blob_sha" in r:
            digest = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
            assert digest == r["git_blob_sha"], "Original Git blob mismatch: " + name
        else:
            digest = hashlib.md5(content).hexdigest()
            if digest != r["stored_sql_md5"]:
                assert content.endswith(b"\n") and hashlib.md5(content[:-1]).hexdigest() == r["stored_sql_md5"], "SQL history mismatch: " + name
        shutil.copyfile(source, target / name)
    assert len(list(target.glob("*.sql"))) == 46
    print("PASS: 46 verified distinct versions in a disposable local directory")

if __name__ == "__main__":
    main()
