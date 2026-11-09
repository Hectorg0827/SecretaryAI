#!/usr/bin/env python3
"""Regenerate Lola's published integration contracts for Workstreams 2–4.

    python scripts/export_lola_contracts.py          # write docs/lola/*.json
    python scripts/export_lola_contracts.py --check  # exit 1 if they are stale

Writes:
  docs/lola/contracts.schema.json — JSON Schema for every shared model
  docs/lola/examples.json         — one valid example response per status
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.lola.contracts import CONTRACT_MODELS, CONTRACT_VERSION  # noqa: E402
from app.lola.mocks import example_responses  # noqa: E402

OUT = ROOT / "docs" / "lola"


def render() -> dict[str, str]:
    schema = {
        "contract_version": CONTRACT_VERSION,
        "models": {m.__name__: m.model_json_schema() for m in CONTRACT_MODELS},
    }
    examples = {
        "contract_version": CONTRACT_VERSION,
        "examples": {k: json.loads(v.model_dump_json()) for k, v in example_responses().items()},
    }
    dump = lambda d: json.dumps(d, indent=2, sort_keys=True) + "\n"  # noqa: E731
    return {"contracts.schema.json": dump(schema), "examples.json": dump(examples)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for name, text in render().items():
        path = OUT / name
        if args.check:
            if not path.exists() or path.read_text() != text:
                stale.append(name)
        else:
            OUT.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    if stale:
        print("Stale Lola contracts: " + ", ".join(stale) + " — run scripts/export_lola_contracts.py")
        return 1
    print("Lola contracts " + ("are current" if args.check else f"written to {OUT}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
