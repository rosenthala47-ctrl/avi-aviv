#!/usr/bin/env python3
"""Run the Customer Risk Rating API with uvicorn.

    python scripts/serve.py                 # in-memory backends, one worker
    python scripts/serve.py --workers 9     # ~9 workers to approach 100 rps
    CRR_DATABASE_URL=postgresql+psycopg://... CRR_REDIS_URL=redis://... python scripts/serve.py

Scaling note: a single-customer score is ~90 ms of mostly Python/pandas CPU, which
is GIL-bound, so throughput scales with worker *processes*, not threads. Roughly
one core per ~11 rps; size ``--workers`` accordingly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)

    import uvicorn

    # Import string so uvicorn can spawn multiple workers.
    uvicorn.run(
        "crr.api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        workers=args.workers,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
