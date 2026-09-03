#!/usr/bin/env python3
"""Refresh the sanctions-screening watchlist from a real source list.

Parses OFAC's SDN list, the UN Security Council's Consolidated List, or the
EU's Financial Sanctions Files export (see crr.screening.parsers for each
source's schema) and atomically replaces that source's rows in the workflow
database — see WorkflowStore.replace_watchlist_source. The Streamlit app
reads whatever is in that table on every page load; it never fetches these
lists itself; running this script (by hand, or on a cron) is how the data
gets there.

Two ways to get the source file: a live fetch (--url, or the built-in
default for ofac/un), or a file already on disk (--file) — see
crr.screening.ingest's module docstring for why the second is not just a
fallback: a lot of real regulated deployments require it regardless of
connectivity. Whichever the app itself runs behind (an outbound-restricted
network, or one that can reach the publishers directly) is an operational
choice this script does not make for you.

Examples
--------
    python scripts/refresh_watchlists.py --source ofac
    python scripts/refresh_watchlists.py --source un --url https://scsanctions.un.org/resources/xml/en/consolidated.xml
    python scripts/refresh_watchlists.py --source eu --file /path/to/downloaded_fsf_export.xml
    python scripts/refresh_watchlists.py --source all   # ofac + un only — eu has no default URL, see below
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from crr.screening import SOURCES, fetch_and_parse, load_and_parse  # noqa: E402
from crr.workflow.db import create_session_factory, resolve_database_url  # noqa: E402
from crr.workflow.store import WorkflowStore  # noqa: E402


def refresh_one(store: WorkflowStore, source: str, *, url: str | None, file: str | None) -> None:
    if file:
        print(f"[{source}] parsing {file} ...")
        records = load_and_parse(source, file)
    else:
        target = url or "the source's default URL"
        print(f"[{source}] fetching {target} ...")
        records = fetch_and_parse(source, url)
    if not records:
        print(f"[{source}] parsed 0 records — refusing to replace existing data with an empty list.")
        print(f"[{source}]   (a genuinely empty upstream list would need --force; this script has no such flag "
              f"yet, so investigate the schema/URL before re-running.)")
        return
    n = store.replace_watchlist_source(source, records)
    sample = ", ".join(r.name for r in records[:3])
    print(f"[{source}] replaced with {n} records (e.g. {sample}{', ...' if n > 3 else ''})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, choices=(*SOURCES, "all"),
                        help="which list to refresh, or 'all' for every source with a usable default")
    parser.add_argument("--url", default=None, help="override the source's default fetch URL")
    parser.add_argument("--file", default=None, help="parse a file already on disk instead of fetching")
    args = parser.parse_args()

    if args.file and args.url:
        parser.error("--url and --file are mutually exclusive — pick one")
    if args.file and args.source == "all":
        parser.error("--file applies to a single --source, not 'all'")

    store = WorkflowStore(create_session_factory(resolve_database_url()))

    sources = [s for s in SOURCES if s != "eu"] if args.source == "all" else [args.source]
    if args.source == "all":
        print("Note: 'eu' is skipped by --source all — it has no stable default URL "
              "(see crr/screening/ingest.py). Refresh it explicitly with --source eu --url/--file.")

    for source in sources:
        try:
            refresh_one(store, source, url=args.url, file=args.file)
        except Exception as exc:  # noqa: BLE001 — one source's failure should not abort the others
            print(f"[{source}] FAILED: {exc}", file=sys.stderr)

    print("\nCurrent watchlist status:")
    for row in store.watchlist_source_status():
        print(f"  {row['source']:<14} {row['count']:>6} entries   last refreshed {row['last_refreshed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
