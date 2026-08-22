#!/usr/bin/env python3
"""Render docs/DATA_DICTIONARY.md from a freshly generated dataset."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from crr.data.dictionary import render_markdown, undocumented  # noqa: E402
from crr.data.synthetic import GeneratorConfig, generate  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "docs" / "DATA_DICTIONARY.md")
    parser.add_argument("-n", "--n-customers", type=int, default=2000)
    parser.add_argument("--strict", action="store_true", help="exit non-zero if any column is undocumented")
    args = parser.parse_args(argv)

    dataset = generate(GeneratorConfig(n_customers=args.n_customers, seed=42, language="mixed"))
    frames = {
        "customers": dataset.customers,
        "narratives": dataset.narratives,
        "events": dataset.events,
        "outcomes": dataset.outcomes,
        "ground_truth": dataset.ground_truth,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_markdown(frames), encoding="utf-8")

    missing = {name: undocumented(frame) for name, frame in frames.items()}
    missing = {k: v for k, v in missing.items() if v}
    if missing:
        print(f"warning: undocumented columns: {missing}", file=sys.stderr)
    print(f"wrote {args.out}")
    return 1 if (missing and args.strict) else 0


if __name__ == "__main__":
    raise SystemExit(main())
