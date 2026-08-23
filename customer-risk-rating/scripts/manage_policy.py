#!/usr/bin/env python3
"""Phase 5: inspect and roll back risk-policy versions — the no-code control surface.

This is the operational half of requirement 4c: a risk manager (today, running
this script; eventually the same calls behind an admin UI) can see every policy
version that has ever been active and restore any of them, with no code change
and no deploy. Every version this project has ever loaded is kept in
``config/policy_history/`` (see ``crr.policy``), so nothing here can lose data —
"rollback" is "restore an exact historical file," not a best-effort reconstruction.

Examples
--------
    python scripts/manage_policy.py list
    python scripts/manage_policy.py show 2
    python scripts/manage_policy.py diff 1 2
    python scripts/manage_policy.py rollback 1
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from crr.policy import (  # noqa: E402
    DEFAULT_POLICY_PATH,
    PolicyError,
    list_archived_versions,
    load_policy,
    load_policy_version,
    rollback_to,
)


def _describe(version: int, policy) -> str:
    return (
        f"v{version}: bands Low<={policy.bands.low_max:.0f} Medium<={policy.bands.medium_max:.0f} "
        f"High<={policy.bands.high_max:.0f} | {len(policy.rules)} rules "
        f"({sum(1 for r in policy.rules if r.enabled)} enabled) | "
        f"review bands: {sorted(policy.review_bands) or 'none'}"
    )


def cmd_list(args: argparse.Namespace) -> int:
    versions = list_archived_versions(args.path)
    if not versions:
        print(f"no archived versions for {args.path} — has it ever been loaded? try: load_policy() first")
        return 1
    try:
        active_version = load_policy(args.path).version
    except PolicyError:
        active_version = None
    print(f"policy history for {args.path}:\n")
    for version in versions:
        policy = load_policy_version(version, args.path)
        marker = "  <- ACTIVE" if version == active_version else ""
        print(f"  {_describe(version, policy)}{marker}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    policy = load_policy_version(args.version, args.path)
    print(_describe(args.version, policy))
    print()
    for rule in policy.rules:
        status = "enabled " if rule.enabled else "disabled"
        visible = "visible " if rule.customer_visible else "internal"
        print(f"  [{status}] {rule.id:<28} floor={rule.floor_band or '-':<8} "
              f"review={rule.require_review!s:<5} {visible}  when: {rule.when}")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    from crr.policy import _archive_dir_for  # internal, but this is an operator tool for exactly this file

    left = (_archive_dir_for(Path(args.path).resolve()) / f"v{args.a}.yaml").read_text().splitlines(keepends=True)
    right = (_archive_dir_for(Path(args.path).resolve()) / f"v{args.b}.yaml").read_text().splitlines(keepends=True)
    diff = list(difflib.unified_diff(left, right, fromfile=f"v{args.a}", tofile=f"v{args.b}"))
    print("".join(diff) if diff else f"v{args.a} and v{args.b} are byte-identical")
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    current = load_policy(args.path)
    if current.version == args.version:
        print(f"v{args.version} is already active — nothing to do.")
        return 0
    if not args.yes:
        print(f"About to overwrite {args.path} (currently v{current.version}) with archived v{args.version}.")
        response = input("Type 'yes' to confirm: ").strip().lower()
        if response != "yes":
            print("aborted.")
            return 1
    restored = rollback_to(args.version, args.path)
    print(f"rolled back {args.path}: v{current.version} -> v{restored.version}")
    print("this takes effect on the next request with no deploy (the service reloads on content change).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", type=Path, default=DEFAULT_POLICY_PATH, help="policy file to operate on")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="every archived version, oldest first").set_defaults(func=cmd_list)

    show = subparsers.add_parser("show", help="print one archived version's contents")
    show.add_argument("version", type=int)
    show.set_defaults(func=cmd_show)

    diff = subparsers.add_parser("diff", help="unified diff between two archived versions")
    diff.add_argument("a", type=int)
    diff.add_argument("b", type=int)
    diff.set_defaults(func=cmd_diff)

    rollback = subparsers.add_parser("rollback", help="restore an archived version as the active policy")
    rollback.add_argument("version", type=int)
    rollback.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    rollback.set_defaults(func=cmd_rollback)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PolicyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
