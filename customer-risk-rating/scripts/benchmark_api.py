#!/usr/bin/env python3
"""Measure single-score latency against the phase 4 exit criterion (p99 < 150 ms).

Drives the real scoring service (feature build + both models + optional SHAP +
composite + reason codes) over many customers and reports the latency
distribution. Applies the same GC tuning the API applies at startup, because the
p99 tail is GC pauses, not compute, and a benchmark under different GC settings
than production would measure the wrong thing.

A note on what this does and does not prove. It measures per-request CPU latency
in-process, which is what the p99 target is about. It does not stand up a real
server behind a network load generator at a sustained 100 rps — that needs
infrastructure this environment does not have. The concurrency probe estimates
throughput by running the same work across threads; treat it as indicative.

Examples
--------
    python scripts/benchmark_api.py
    python scripts/benchmark_api.py --requests 2000 --explain
"""

from __future__ import annotations

import argparse
import gc
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from crr.api.scoring import ModelBundle, ScoringService  # noqa: E402

TARGET_P99_MS = 150.0
PII = ["full_name", "national_id", "email", "phone", "address_line", "split"]


def _payloads(data_dir: Path, n: int) -> list[dict]:
    frame = pd.read_csv(data_dir / "customers.csv")
    rows = []
    for _, row in frame.head(n).iterrows():
        record = row.to_dict()
        for key in PII:
            record.pop(key, None)
        rows.append({k: (None if pd.isna(v) else v) for k, v in record.items()})
    return rows


def _percentiles(samples: list[float]) -> dict[str, float]:
    array = np.array(samples)
    return {
        "p50": float(np.percentile(array, 50)),
        "p95": float(np.percentile(array, 95)),
        "p99": float(np.percentile(array, 99)),
        "max": float(array.max()),
        "mean": float(array.mean()),
    }


def _measure(service: ScoringService, payloads: list[dict], explain: bool) -> dict[str, float]:
    latencies = []
    for payload in payloads:
        started = time.perf_counter()
        service.score(payload, explain=explain)
        latencies.append((time.perf_counter() - started) * 1000.0)
    return _percentiles(latencies)


def _throughput_probe(service: ScoringService, payloads: list[dict], explain: bool, threads: int) -> float:
    """Rough sustained throughput via threads. LightGBM and numpy release the GIL
    for their compute, so threads overlap meaningfully; pure-Python stretches do
    not, so this under- rather than over-states real multi-process throughput."""
    barrier = threading.Barrier(threads)
    counts = [0] * threads
    duration = 3.0

    def worker(slot: int) -> None:
        barrier.wait()
        end = time.perf_counter() + duration
        i = 0
        while time.perf_counter() < end:
            service.score(payloads[i % len(payloads)], explain=explain)
            i += 1
        counts[slot] = i

    workers = [threading.Thread(target=worker, args=(s,)) for s in range(threads)]
    start = time.perf_counter()
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    return sum(counts) / (time.perf_counter() - start)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=REPO_ROOT / "data" / "raw")
    parser.add_argument("--model-dir", type=Path, default=REPO_ROOT / "models")
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=50)
    parser.add_argument("--threads", type=int, default=8, help="concurrency for the throughput probe")
    parser.add_argument("--no-gc-tuning", action="store_true", help="show the untuned tail for comparison")
    args = parser.parse_args(argv)

    service = ScoringService(ModelBundle.load(args.model_dir))
    payloads = _payloads(args.data, args.requests)
    if not payloads:
        print("no customer data found; run scripts/generate_synthetic_data.py first", file=sys.stderr)
        return 2

    for payload in payloads[: args.warmup]:
        service.score(payload, explain=True)

    if not args.no_gc_tuning:
        gc.collect()
        gc.freeze()
        gc.set_threshold(50_000, 500, 1000)

    print("=" * 74)
    print("PHASE 4 LATENCY — single-customer scoring")
    print(f"  {len(payloads):,} requests   GC tuning: {'off' if args.no_gc_tuning else 'on (production setting)'}")
    print("=" * 74)

    print(f"\n  {'path':<22}{'p50':>8}{'p95':>8}{'p99':>8}{'max':>8}  target p99 < {TARGET_P99_MS:.0f}ms")
    results = {}
    for label, explain in (("score only", False), ("score + explanation", True)):
        stats = _measure(service, payloads, explain)
        results[label] = stats
        verdict = "PASS" if stats["p99"] < TARGET_P99_MS else "OVER"
        print(f"  {label:<22}{stats['p50']:>7.1f}{stats['p95']:>7.1f}{stats['p99']:>7.1f}"
              f"{stats['max']:>7.1f}  [{verdict}]")

    print(f"\n  throughput probe ({args.threads} threads):")
    for label, explain in (("score only", False), ("score + explanation", True)):
        rps = _throughput_probe(service, payloads, explain, args.threads)
        print(f"    {label:<22}{rps:>7.0f} req/s")

    print("\n" + "=" * 74)
    print("PHASE 4 EXIT CRITERION")
    decision_ok = results["score only"]["p99"] < TARGET_P99_MS
    print(f"  [{'PASS' if decision_ok else 'FAIL'}] real-time decision path p99 "
          f"{results['score only']['p99']:.0f}ms < {TARGET_P99_MS:.0f}ms")
    explain_ok = results["score + explanation"]["p99"] < TARGET_P99_MS
    print(f"  [{'PASS' if explain_ok else 'INFO'}] explained path p99 "
          f"{results['score + explanation']['p99']:.0f}ms "
          f"({'under' if explain_ok else 'over'} target; explanations can also be served off the hot path)")
    print("=" * 74)
    return 0 if decision_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
