#!/usr/bin/env python3
"""Phase 5 exit criteria, measured and judged: "A policy change takes effect
within 60 seconds with no deploy, is fully audit-logged, and can be rolled back
to any prior version."

Each criterion is checked against the real ``crr.policy``/``crr.rules`` code —
not asserted by inspection — using a private, isolated policy file and archive
so this script is safe to run repeatedly and never touches the committed
``config/policy_history/``.

Examples
--------
    python scripts/verify_rule_engine.py
"""

from __future__ import annotations

import io
import logging
import sys
import tempfile
import time
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

import crr.policy as policy_module  # noqa: E402
from crr.policy import PolicyError, list_archived_versions, load_policy, rollback_to  # noqa: E402
from crr.rules.engine import RuleEngine  # noqa: E402

MAX_RELOAD_SECONDS = 60.0


def check_hot_reload(path: Path) -> tuple[bool, str]:
    """A policy edit takes effect on the next load, well under 60 seconds, with
    no process restart — timed against the real filesystem and loader."""
    load_policy(path)  # establish v1
    raw = yaml.safe_load(path.read_text())
    raw["version"] = 2
    raw["bands"]["Low"]["max_score"] = 20
    started = time.perf_counter()
    path.write_text(yaml.safe_dump(raw))
    reloaded = load_policy(path)
    elapsed = time.perf_counter() - started
    ok = reloaded.version == 2 and reloaded.bands.low_max == 20.0 and elapsed < MAX_RELOAD_SECONDS
    return ok, f"edit -> reloaded in {elapsed * 1000:.1f}ms (limit {MAX_RELOAD_SECONDS:.0f}s), no restart"


def check_audit_logged(path: Path) -> tuple[bool, str]:
    """A policy version change emits a structured audit event."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    audit_logger = logging.getLogger("crr.audit")
    audit_logger.addHandler(handler)
    audit_logger.setLevel(logging.INFO)
    try:
        raw = yaml.safe_load(path.read_text())
        raw["version"] = 3
        raw["bands"]["Low"]["max_score"] = 15
        path.write_text(yaml.safe_dump(raw))
        load_policy(path)
    finally:
        audit_logger.removeHandler(handler)
    logged = "policy.changed" in stream.getvalue()
    return logged, "policy.changed audit event " + ("emitted" if logged else "MISSING")


def check_rollback(path: Path) -> tuple[bool, str]:
    """Every version ever loaded can be restored exactly."""
    versions = list_archived_versions(path)
    if 1 not in versions:
        return False, f"v1 missing from archive (have {versions})"
    restored = rollback_to(1, path)
    ok = restored.version == 1 and restored.bands.low_max == 25.0
    return ok, f"archive holds {versions}; rollback_to(1) -> v{restored.version}, low_max={restored.bands.low_max}"


def check_raise_only() -> tuple[bool, str]:
    """A rule engine constructed with an always-firing, LOW-floor rule can
    never lower any model band — the property the whole engine exists for."""
    from crr.policy import Rule

    weak_rule = Rule(id="ALWAYS_FIRES", description="verify", when="1 == 1", floor_band="Low",
                      require_review=False, reason_code="X", enabled=True)
    engine = RuleEngine((weak_rule,))
    bands = ("Low", "Medium", "High", "Extreme")
    for band in bands:
        if engine.apply(band, {}).final_band != band:
            return False, f"a Low-floor rule lowered {band}!"
    return True, f"tested all {len(bands)} bands against a Low-floor rule: none lowered"


def main() -> int:
    print("=" * 74)
    print("PHASE 5 EXIT CRITERIA — rule engine and the no-code control surface")
    print("=" * 74)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        policy_path = tmp_path / "risk_policy.yaml"
        policy_path.write_text((REPO_ROOT / "config" / "risk_policy.yaml").read_text())
        # Isolate the archive so this script never touches the committed one.
        policy_module.DEFAULT_ARCHIVE_DIR = tmp_path / "policy_history"

        checks = []
        try:
            checks.append(("policy change takes effect within 60s, no deploy", *check_hot_reload(policy_path)))
            checks.append(("policy change is fully audit-logged", *check_audit_logged(policy_path)))
            checks.append(("any prior version can be rolled back to", *check_rollback(policy_path)))
        except PolicyError as exc:
            print(f"\nERROR during verification: {exc}")
            return 2

    checks.append(("rules can only raise risk, never lower it", *check_raise_only()))

    print()
    all_ok = True
    for label, ok, detail in checks:
        all_ok &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        print(f"         {detail}")
    print("=" * 74)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
