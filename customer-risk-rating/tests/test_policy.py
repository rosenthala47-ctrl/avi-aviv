"""Tests for the risk-policy loader: hot reload, immutable versions, the
version archive, rollback, and fail-safe degradation on a broken edit.

The two properties that matter most here, both because they are the exit
criterion for phase 5 and because a system that gets either one wrong fails
silently: a version number must mean one exact, permanent thing (immutability),
and a bad edit to the live policy file must degrade to "serve the last
known-good policy, loudly" rather than take every scoring request down with it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from crr.policy import (
    DEFAULT_POLICY_PATH,
    PolicyError,
    list_archived_versions,
    load_policy,
    load_policy_or_fallback,
    load_policy_version,
    rollback_to,
)

_BASE_POLICY_TEXT = Path("config/risk_policy.yaml").read_text(encoding="utf-8")
#: Derived, not hardcoded: the real file's version number changes over the
#: project's life (it just did, for phase 7), and a test suite that hardcodes
#: "1" for what the shipped file happens to be versioned at breaks on every
#: such change for reasons unrelated to what it is actually testing.
_BASE_VERSION: int = yaml.safe_load(_BASE_POLICY_TEXT)["version"]


@pytest.fixture(autouse=True)
def isolated_archive(tmp_path, monkeypatch):
    """Every test in this module gets its own archive directory, transparently.

    Without this, a policy file named "risk_policy.yaml" (the ``policy_file``
    fixture below matches the real file's stem on purpose, to test realistic
    paths) archives into the SAME ``config/policy_history/risk_policy/``
    directory the real, committed policy uses whenever a function's
    ``archive_root`` is left at its default — polluting the real project
    directory and colliding between tests that pick the same version number
    for different content. Isolating this for every test, rather than
    remembering to pass an explicit archive_root per call, is what actually
    makes the tests independent of each other and of the committed archive.
    """
    archive_dir = tmp_path / "archive"
    monkeypatch.setattr("crr.policy.DEFAULT_ARCHIVE_DIR", archive_dir)
    return archive_dir


@pytest.fixture
def policy_file(tmp_path: Path) -> Path:
    """A private copy of the real policy — content only; where it archives to
    is handled by ``isolated_archive`` above."""
    target = tmp_path / "risk_policy.yaml"
    target.write_text(_BASE_POLICY_TEXT)
    return target


def _bump(path: Path, version: int, **overrides) -> None:
    """Rewrite ``path`` as a valid policy at ``version``, starting fresh from
    the known-good base text every time — never from whatever ``path``
    currently contains, which a prior step in the same test may have left as
    something deliberately broken (malformed YAML, a conflicting version)."""
    raw = yaml.safe_load(_BASE_POLICY_TEXT)
    raw["version"] = version
    for dotted_key, value in overrides.items():
        section, field = dotted_key.split(".")
        raw.setdefault(section, {})[field] = value
    path.write_text(yaml.safe_dump(raw, sort_keys=False))


# --------------------------------------------------------------------------
# Basic loading
# --------------------------------------------------------------------------


def test_loads_the_real_policy_without_error():
    policy = load_policy(DEFAULT_POLICY_PATH, use_cache=False)
    assert policy.version >= 1
    assert len(policy.rules) > 0
    assert policy.review_bands


def test_missing_file_raises_policy_error(tmp_path: Path):
    with pytest.raises(PolicyError, match="not found"):
        load_policy(tmp_path / "nope.yaml")


def test_non_mapping_yaml_is_rejected(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a\n- list\n")
    with pytest.raises(PolicyError, match="mapping"):
        load_policy(bad)


def test_band_thresholds_must_strictly_increase(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(Path("config/risk_policy.yaml").read_text())
    _bump(bad, 1)
    raw = yaml.safe_load(bad.read_text())
    raw["bands"]["Medium"]["max_score"] = 10  # below Low's 25
    bad.write_text(yaml.safe_dump(raw))
    with pytest.raises(PolicyError, match="strictly increase"):
        load_policy(bad)


def test_composite_weights_must_sum_to_one(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    raw = yaml.safe_load(Path("config/risk_policy.yaml").read_text())
    raw["composite"]["credit_weight"] = 0.9
    raw["composite"]["financial_crime_weight"] = 0.9
    bad.write_text(yaml.safe_dump(raw))
    with pytest.raises(PolicyError, match="sum to 1"):
        load_policy(bad)


def test_unknown_floor_band_is_rejected(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    raw = yaml.safe_load(Path("config/risk_policy.yaml").read_text())
    raw["rules"][0]["action"]["floor_band"] = "Catastrophic"
    bad.write_text(yaml.safe_dump(raw))
    with pytest.raises(PolicyError, match="floor_band"):
        load_policy(bad)


def test_unknown_review_band_is_rejected(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    raw = yaml.safe_load(Path("config/risk_policy.yaml").read_text())
    raw["review"]["require_for_bands"] = ["NotABand"]
    bad.write_text(yaml.safe_dump(raw))
    with pytest.raises(PolicyError, match="require_for_bands"):
        load_policy(bad)


# --------------------------------------------------------------------------
# review_bands / customer_visible parsing
# --------------------------------------------------------------------------


def test_review_bands_parsed_from_the_real_policy():
    policy = load_policy(DEFAULT_POLICY_PATH, use_cache=False)
    assert policy.review_bands == frozenset({"High", "Extreme"})


def test_compliance_sensitive_rules_are_not_customer_visible():
    policy = load_policy(DEFAULT_POLICY_PATH, use_cache=False)
    by_id = {r.id: r for r in policy.rules}
    assert by_id["SANCTIONS_MATCH"].customer_visible is False
    assert by_id["PEP_HIGH_RISK_JURISDICTION"].customer_visible is False
    assert by_id["UNVERIFIED_SOURCE_OF_FUNDS"].customer_visible is False


def test_credit_rules_default_to_customer_visible():
    policy = load_policy(DEFAULT_POLICY_PATH, use_cache=False)
    by_id = {r.id: r for r in policy.rules}
    assert by_id["SEVERE_ARREARS"].customer_visible is True
    assert by_id["THIN_FILE_HIGH_EXPOSURE"].customer_visible is True


# --------------------------------------------------------------------------
# Hot reload: an edit takes effect on the next load, no restart
# --------------------------------------------------------------------------


def test_content_change_is_picked_up_on_next_load(policy_file):
    first = load_policy(policy_file)
    assert first.bands.low_max == 25.0

    _bump(policy_file, _BASE_VERSION + 1, **{"bands.Low": {"max_score": 20}})
    second = load_policy(policy_file)
    assert second.bands.low_max == 20.0
    assert second.version == _BASE_VERSION + 1


def test_unchanged_file_returns_the_cached_object(policy_file):
    first = load_policy(policy_file)
    second = load_policy(policy_file)
    assert first is second  # same object, not merely equal -- proves the cache hit


def test_cache_can_be_bypassed(policy_file):
    first = load_policy(policy_file, use_cache=True)
    second = load_policy(policy_file, use_cache=False)
    assert first is not second
    assert first.bands.low_max == second.bands.low_max


# --------------------------------------------------------------------------
# Version immutability
# --------------------------------------------------------------------------


def test_reusing_a_version_number_with_different_content_is_rejected(policy_file):
    load_policy(policy_file)  # establishes the base version's content hash
    raw = yaml.safe_load(policy_file.read_text())
    raw["bands"]["Low"]["max_score"] = 1  # different content, SAME version number
    policy_file.write_text(yaml.safe_dump(raw))
    with pytest.raises(PolicyError, match="already recorded with different content"):
        load_policy(policy_file)


def test_reloading_identical_content_under_the_same_version_is_fine(policy_file):
    load_policy(policy_file)
    load_policy(policy_file, use_cache=False)  # force a real re-parse, not a cache hit
    # No error: identical bytes under the same version number is just a reload.


def test_genuine_version_bump_is_accepted(policy_file):
    load_policy(policy_file)
    _bump(policy_file, _BASE_VERSION + 1, **{"bands.Low": {"max_score": 22}})
    second = load_policy(policy_file)
    assert second.version == _BASE_VERSION + 1


def test_immutability_survives_across_a_simulated_restart(policy_file, monkeypatch):
    """The in-memory record is process-local; the guarantee must also hold via
    the durable filesystem archive, or it would only be a guarantee between
    restarts, not the "immutable" claim requirement 4c depends on."""
    load_policy(policy_file)  # archives the base version to disk

    # Simulate a fresh process: clear every in-memory cache/record.
    monkeypatch.setattr("crr.policy._CACHE", {})
    monkeypatch.setattr("crr.policy._LAST_GOOD", {})
    monkeypatch.setattr("crr.policy._VERSION_CONTENT", {})

    raw = yaml.safe_load(policy_file.read_text())
    raw["bands"]["Low"]["max_score"] = 24  # different content, still valid, SAME version
    policy_file.write_text(yaml.safe_dump(raw))

    with pytest.raises(PolicyError, match="already recorded with different content"):
        load_policy(policy_file)


# --------------------------------------------------------------------------
# Archive / rollback
# --------------------------------------------------------------------------


def test_first_load_archives_the_version(policy_file):
    load_policy(policy_file)
    assert list_archived_versions(policy_file) == [_BASE_VERSION]


def test_archive_is_never_overwritten_by_a_later_load(policy_file, isolated_archive):
    load_policy(policy_file)
    archived_path = isolated_archive / "risk_policy" / f"v{_BASE_VERSION}.yaml"
    original_content = archived_path.read_text()
    load_policy(policy_file, use_cache=False)  # reload the same content again
    assert archived_path.read_text() == original_content


def test_rollback_restores_exact_prior_content(policy_file):
    original_text = policy_file.read_text()
    load_policy(policy_file)  # base version

    _bump(policy_file, _BASE_VERSION + 1, **{"bands.Low": {"max_score": 5}})
    load_policy(policy_file)  # bumped version
    assert load_policy(policy_file).bands.low_max == 5.0

    restored = rollback_to(_BASE_VERSION, policy_file)
    assert restored.version == _BASE_VERSION
    assert restored.bands.low_max == 25.0
    assert policy_file.read_text() == original_text


def test_rollback_to_unknown_version_raises(policy_file):
    load_policy(policy_file)
    with pytest.raises(PolicyError, match="no archived version"):
        rollback_to(99, policy_file)


def test_load_policy_version_does_not_touch_the_live_file(policy_file):
    load_policy(policy_file)
    live_before = policy_file.read_text()
    historical = load_policy_version(_BASE_VERSION, policy_file)
    assert historical.version == _BASE_VERSION
    assert policy_file.read_text() == live_before  # untouched


def test_list_archived_versions_empty_when_never_loaded(tmp_path):
    assert list_archived_versions(tmp_path / "never-loaded.yaml") == []


# --------------------------------------------------------------------------
# Fail-safe fallback: a broken edit degrades, never takes the service down
# --------------------------------------------------------------------------


def test_fallback_serves_last_good_policy_on_a_version_conflict(policy_file):
    good = load_policy_or_fallback(policy_file)
    assert good.version == _BASE_VERSION

    raw = yaml.safe_load(policy_file.read_text())
    raw["bands"]["Low"]["max_score"] = 1  # conflicting content, same version
    policy_file.write_text(yaml.safe_dump(raw))

    fallback = load_policy_or_fallback(policy_file)
    assert fallback.version == _BASE_VERSION
    assert fallback.bands.low_max == 25.0  # the ORIGINAL base content, not the broken edit


def test_fallback_serves_last_good_policy_on_malformed_yaml(policy_file):
    load_policy_or_fallback(policy_file)
    policy_file.write_text("not: [valid, yaml: {{{")
    fallback = load_policy_or_fallback(policy_file)
    assert fallback.version == _BASE_VERSION


def test_fallback_raises_on_a_first_load_with_nothing_to_fall_back_to(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("not: [valid, yaml: {{{")
    with pytest.raises((PolicyError, yaml.YAMLError)):
        load_policy_or_fallback(bad)


def test_fallback_recovers_once_the_file_is_fixed(policy_file):
    load_policy_or_fallback(policy_file)
    policy_file.write_text("broken {{{")
    assert load_policy_or_fallback(policy_file).version == _BASE_VERSION

    _bump(policy_file, _BASE_VERSION + 1, **{"bands.Low": {"max_score": 18}})
    recovered = load_policy_or_fallback(policy_file)
    assert recovered.version == _BASE_VERSION + 1
    assert recovered.bands.low_max == 18.0
