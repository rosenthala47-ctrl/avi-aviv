#!/usr/bin/env python3
"""Generate the synthetic Customer Risk Rating dataset used to train the alpha model.

Examples
--------
    # default 10k-customer book, CSV, into data/raw/
    python scripts/generate_synthetic_data.py

    # bigger book, both formats, Hebrew-heavy narratives, fixed seed
    python scripts/generate_synthetic_data.py -n 100000 --format csv --format parquet \
        --language mixed --hebrew-share 0.5 --seed 7

    # clean data for a smoke test: no missingness, no dirty categoricals, no duplicates
    python scripts/generate_synthetic_data.py -n 1000 --clean --out data/interim/smoke
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from crr.data.synthetic import (  # noqa: E402  (path setup must run first)
    GeneratorConfig,
    format_report,
    generate,
    write_dataset,
)


def _parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:  # pragma: no cover - argparse surfaces the message
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="generate_synthetic_data",
        description="Generate a synthetic customer risk rating dataset.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-n", "--n-customers", type=int, default=10_000, help="number of customers to generate")
    parser.add_argument("--seed", type=int, default=42, help="random seed; identical seeds reproduce identical files")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "raw", help="output directory")
    parser.add_argument(
        "--format",
        dest="formats",
        action="append",
        choices=["csv", "parquet", "jsonl"],
        help="output format; repeat the flag for several (default: csv)",
    )
    parser.add_argument("--as-of", type=_parse_date, default=dt.date(2026, 1, 1), help="most recent snapshot date")
    parser.add_argument("--cohort-months", type=int, default=12, help="months over which snapshots are spread")
    parser.add_argument("--outcome-horizon-months", type=int, default=12, help="performance window for the label")

    parser.add_argument("--default-rate", type=float, default=0.055, help="target 12-month default prevalence")
    parser.add_argument("--financial-crime-rate", type=float, default=0.015, help="target confirmed financial-crime prevalence")

    parser.add_argument("--language", choices=["en", "he", "mixed"], default="en", help="narrative language")
    parser.add_argument("--hebrew-share", type=float, default=0.35, help="share of Hebrew narratives when --language mixed")

    parser.add_argument("--events-per-customer", type=float, default=12.0, help="mean events in the trailing window")
    parser.add_argument("--event-window-days", type=int, default=180, help="length of the trailing event window")
    parser.add_argument("--no-events", action="store_true", help="skip the event stream entirely")

    parser.add_argument("--no-pii", action="store_true", help="omit the synthetic PII columns")
    parser.add_argument("--clean", action="store_true", help="disable missingness, categorical noise and duplicates")
    parser.add_argument("--duplicate-rate", type=float, default=0.004, help="share of near-duplicate records")

    parser.add_argument("--quiet", action="store_true", help="suppress the run report")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )

    cfg = GeneratorConfig(
        n_customers=args.n_customers,
        seed=args.seed,
        as_of=args.as_of,
        cohort_months=args.cohort_months,
        outcome_horizon_months=args.outcome_horizon_months,
        target_default_rate=args.default_rate,
        target_financial_crime_rate=args.financial_crime_rate,
        language=args.language,
        hebrew_share=args.hebrew_share,
        events_per_customer=args.events_per_customer,
        event_window_days=args.event_window_days,
        generate_events=not args.no_events,
        include_pii=not args.no_pii,
        inject_missingness=not args.clean,
        inject_categorical_noise=not args.clean,
        duplicate_rate=0.0 if args.clean else args.duplicate_rate,
    )

    try:
        cfg.validate()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    dataset = generate(cfg)
    manifest = write_dataset(dataset, args.out, formats=tuple(args.formats or ["csv"]))

    if not args.quiet:
        print()
        print(format_report(manifest))
        print(f"\nOutput directory: {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
