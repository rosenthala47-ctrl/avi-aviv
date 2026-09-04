#!/usr/bin/env python3
"""Phase 5: simulate a proposed risk policy against recent scoring history.

Requirement 4c's "before it goes live" tool. Shows a risk manager exactly which
customers would change band or review status if a proposed edit to
``risk_policy.yaml`` were made active — without touching the live file, and
without re-running the ML model (only the policy-driven post-model logic is a
function of the policy; the model's probabilities are replayed from what was
already stored).

Two modes:

    --database-url postgresql+psycopg://...   (production)
        Pulls real scoring history from ``ScoreRepository.recent()`` — the
        actual last N days of production traffic.

    (no --database-url given)                  (self-contained demo)
        No production history exists in this environment, so a labelled DEMO
        MODE scores a fresh sample of customers from ``--data`` under the
        CURRENT policy into an in-memory repository, then simulates against
        that. The simulation code path exercised is identical either way —
        only where the historical scores come from differs.

Examples
--------
    # demo mode: self-contained, no infrastructure needed
    python scripts/simulate_policy.py --proposed config/risk_policy_proposed.yaml

    # production mode: real history
    python scripts/simulate_policy.py --proposed config/risk_policy_proposed.yaml \\
        --database-url postgresql+psycopg://user:pass@host/db --days 90
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from crr.api.projections import assessment_to_stored  # noqa: E402
from crr.api.repository import InMemoryScoreRepository, SqlAlchemyScoreRepository, create_session_factory  # noqa: E402
from crr.api.scoring import ModelBundle, ScoringService  # noqa: E402
from crr.policy import DEFAULT_POLICY_PATH, load_policy  # noqa: E402
from crr.rules.simulate import format_report, simulate  # noqa: E402

PII = ["full_name", "national_id", "email", "phone", "address_line", "split"]


def _demo_history(
    service: ScoringService, data_dir: Path, sample_size: int, days: int
) -> InMemoryScoreRepository:
    """Score a fresh sample under the CURRENT policy, spread over the window,
    so there is something realistic to simulate a proposed policy against."""
    customers = pd.read_csv(data_dir / "customers.csv")
    if customers.empty:
        raise SystemExit(f"no customers found in {data_dir}; run scripts/generate_synthetic_data.py first")
    sample = customers.sample(n=min(sample_size, len(customers)), random_state=7)

    repository = InMemoryScoreRepository()
    now = dt.datetime.now(dt.UTC)
    for offset, (_, row) in enumerate(sample.iterrows()):
        record = row.to_dict()
        for key in PII:
            record.pop(key, None)
        payload = {k: (None if pd.isna(v) else v) for k, v in record.items()}
        assessment = service.score(payload, explain=False)
        # Spread scored_at across the window so `recent()` and any future
        # time-bucketed reporting has realistic variety, not a single instant.
        assessment.scored_at = now - dt.timedelta(seconds=offset * (days * 86_400 // max(len(sample), 1)))
        repository.save(assessment_to_stored(assessment))
    return repository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--proposed", type=Path, required=True, help="path to the proposed risk_policy.yaml")
    parser.add_argument("--current", type=Path, default=DEFAULT_POLICY_PATH, help="path to the current/live policy")
    parser.add_argument("--days", type=int, default=90, help="lookback window in days")
    parser.add_argument("--database-url", default="", help="production score-history backend; omit for demo mode")
    parser.add_argument("--data", type=Path, default=REPO_ROOT / "data" / "raw", help="demo mode: customer sample source")
    parser.add_argument("--model-dir", type=Path, default=REPO_ROOT / "models")
    parser.add_argument("--sample-size", type=int, default=2000, help="demo mode: customers to score")
    args = parser.parse_args(argv)

    current_policy = load_policy(args.current)
    proposed_policy = load_policy(args.proposed)
    print(f"current policy:  {args.current}  (version {current_policy.version})")
    print(f"proposed policy: {args.proposed}  (version {proposed_policy.version})")

    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=args.days)

    if args.database_url:
        repository = SqlAlchemyScoreRepository(create_session_factory(args.database_url))
        scores = repository.recent(since, limit=200_000)
        print(f"queried {len(scores):,} scores from the last {args.days} days at {args.database_url}\n")
        if not scores:
            print("no scoring history in that window — nothing to simulate against.")
            return 0
    else:
        print("\nDEMO MODE: no --database-url given, so there is no real production history to query.")
        print(f"Scoring {args.sample_size:,} fresh customers under the CURRENT policy to simulate against.\n")
        service = ScoringService(ModelBundle.load(args.model_dir), policy=current_policy)
        repository = _demo_history(service, args.data, args.sample_size, args.days)
        scores = repository.recent(since, limit=200_000)

    report = simulate(current_policy, proposed_policy, scores)
    print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
