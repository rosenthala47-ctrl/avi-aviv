"""Load and validate the risk policy (``config/risk_policy.yaml``).

The policy is the risk manager's no-code control surface (requirement 4c). This
module reads it into a typed, validated object so a malformed edit fails at load
with a clear message instead of silently changing scores. Phase 4 uses the band,
composite and explainability sections; the rule engine (:mod:`crr.rules`, phase 5)
reads the ``rules`` and ``review`` sections from the same object.

Loaded once and cached by a hash of the file's content, so a running service picks
up an edit on the next request without a redeploy, but does not re-parse an
unchanged file on every score. Three further guarantees, all enforced here rather
than left to convention:

* **Immutable versions.** Reusing a ``version`` number for genuinely different
  content is a load error, not a silent redefinition.
* **An archive of every version ever loaded**, so any of them can be restored —
  see :func:`rollback_to`.
* **Fail-safe reloading.** A broken edit degrades to "serve the last known-good
  policy and log loudly" rather than 500ing every request — see
  :func:`load_policy_or_fallback`, which is what the scoring service calls.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = REPO_ROOT / "config" / "risk_policy.yaml"
DEFAULT_ARCHIVE_DIR = REPO_ROOT / "config" / "policy_history"

RISK_BANDS: tuple[str, ...] = ("Low", "Medium", "High", "Extreme")

#: Emitted through the same logger name crr.api.audit attaches its JSON handler
#: to. policy.py deliberately has no import of crr.api — the API depends on the
#: policy layer, never the reverse — so this reaches the audit log by logger
#: name alone, which Python's logging registry resolves globally.
_audit_log = logging.getLogger("crr.audit")


class PolicyError(ValueError):
    """Raised when the policy file is missing required structure or is inconsistent."""


@dataclass(frozen=True)
class BandThresholds:
    """Upper score bound (inclusive of the band below) for each band."""

    low_max: float
    medium_max: float
    high_max: float

    def band_for(self, score: float) -> str:
        if score <= self.low_max:
            return "Low"
        if score <= self.medium_max:
            return "Medium"
        if score <= self.high_max:
            return "High"
        return "Extreme"


@dataclass(frozen=True)
class CompositeConfig:
    """How the two model dimensions combine into the published 0-100 score."""

    credit_weight: float
    financial_crime_weight: float
    score_exponent: float


@dataclass(frozen=True)
class ExplainabilityConfig:
    top_factors: int
    min_absolute_shap: float
    suppress_from_customer_view: tuple[str, ...]


@dataclass(frozen=True)
class Rule:
    """A deterministic post-model override. Applied in phase 5; parsed here."""

    id: str
    description: str
    when: str
    floor_band: str | None
    require_review: bool
    reason_code: str
    enabled: bool
    customer_visible: bool = True
    """Whether this rule's id/description may reach a customer-facing
    explanation. Defaults to visible, matching
    ``crr.explain.reason_codes.ReasonCode`` — the same default direction the
    SHAP-derived vocabulary uses — but a compliance-sensitive rule (a
    sanctions or PEP match) must set this to ``false`` explicitly: showing it
    can tip off the subject of an investigation and is often legally
    prohibited. The rule still fires and still floors the band/forces review
    for every audience; only whether it is NAMED in a customer-facing response
    is affected."""


@dataclass(frozen=True)
class RiskPolicy:
    version: int
    bands: BandThresholds
    composite: CompositeConfig
    explainability: ExplainabilityConfig
    rules: tuple[Rule, ...]
    review_bands: frozenset[str] = frozenset()
    rescoring: dict[str, Any] = field(default_factory=dict)
    feedback: dict[str, Any] = field(default_factory=dict)
    source_path: str = ""
    source_mtime: float = 0.0
    content_hash: str = ""

    # ---- derived helpers -------------------------------------------------

    def band_for_score(self, score: float) -> str:
        return self.bands.band_for(score)

    def composite_score(self, p_credit: float, p_financial_crime: float) -> float:
        """Blend two model probabilities into the 0-100 score.

        Same functional form the generator uses for its ground-truth score, but
        driven by the MODEL probabilities. Monotone in both inputs, so a higher
        risk on either dimension can only raise the composite — the property that
        makes the banding defensible.
        """
        w_c = self.composite.credit_weight
        w_fc = self.composite.financial_crime_weight
        p_blend = 1.0 - (1.0 - p_credit) ** (2.0 * w_c) * (1.0 - p_financial_crime) ** (2.0 * w_fc)
        p_blend = min(max(p_blend, 1e-9), 1.0)
        return float(min(100.0 * p_blend**self.composite.score_exponent, 100.0))


def _require(mapping: dict[str, Any], key: str, context: str) -> Any:
    if key not in mapping:
        raise PolicyError(f"risk policy is missing '{key}' in {context}")
    return mapping[key]


def _parse(payload: dict[str, Any], path: Path, mtime: float, content_hash: str) -> RiskPolicy:
    bands_raw = _require(payload, "bands", "policy")
    try:
        bands = BandThresholds(
            low_max=float(bands_raw["Low"]["max_score"]),
            medium_max=float(bands_raw["Medium"]["max_score"]),
            high_max=float(bands_raw["High"]["max_score"]),
        )
    except (KeyError, TypeError) as exc:
        raise PolicyError(f"malformed 'bands' section: {exc}") from exc

    if not (bands.low_max < bands.medium_max < bands.high_max):
        raise PolicyError(
            f"band thresholds must strictly increase; got "
            f"Low<={bands.low_max}, Medium<={bands.medium_max}, High<={bands.high_max}"
        )

    composite_raw = _require(payload, "composite", "policy")
    w_c = float(composite_raw.get("credit_weight", 0.6))
    w_fc = float(composite_raw.get("financial_crime_weight", 0.4))
    if abs(w_c + w_fc - 1.0) > 1e-6:
        raise PolicyError(f"composite weights must sum to 1.0; got {w_c} + {w_fc}")
    composite = CompositeConfig(w_c, w_fc, float(composite_raw.get("score_exponent", 0.45)))

    explain_raw = payload.get("explainability", {})
    explainability = ExplainabilityConfig(
        top_factors=int(explain_raw.get("top_factors", 5)),
        min_absolute_shap=float(explain_raw.get("min_absolute_shap", 0.01)),
        suppress_from_customer_view=tuple(explain_raw.get("suppress_from_customer_view", [])),
    )

    rules: list[Rule] = []
    for rule_raw in payload.get("rules", []):
        action = rule_raw.get("action", {})
        floor_band = action.get("floor_band")
        if floor_band is not None and floor_band not in RISK_BANDS:
            raise PolicyError(f"rule {rule_raw.get('id')!r} has unknown floor_band {floor_band!r}")
        rules.append(
            Rule(
                id=_require(rule_raw, "id", "rule"),
                description=rule_raw.get("description", ""),
                when=_require(rule_raw, "when", f"rule {rule_raw.get('id')!r}"),
                floor_band=floor_band,
                require_review=bool(action.get("require_review", False)),
                reason_code=action.get("reason_code", ""),
                enabled=bool(rule_raw.get("enabled", True)),
                customer_visible=bool(action.get("customer_visible", True)),
            )
        )

    review_raw = payload.get("review", {})
    review_bands = frozenset(review_raw.get("require_for_bands", []))
    unknown = review_bands - set(RISK_BANDS)
    if unknown:
        raise PolicyError(f"'review.require_for_bands' has unknown band(s): {sorted(unknown)}")

    return RiskPolicy(
        version=int(payload.get("version", 0)),
        bands=bands,
        composite=composite,
        explainability=explainability,
        rules=tuple(rules),
        review_bands=review_bands,
        rescoring=payload.get("rescoring", {}),
        feedback=payload.get("feedback", {}),
        source_path=str(path),
        source_mtime=mtime,
        content_hash=content_hash,
    )


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Keyed by resolved path. ``_CACHE`` is "the policy we would load right now, if
# asked" and drives the fast path; ``_LAST_GOOD`` is "the last policy that
# actually parsed successfully" and is what a broken edit falls back to (see
# ``load_policy_or_fallback``). ``_VERSION_CONTENT`` is (path, version) -> the
# content hash first recorded under that version number, the record that makes
# "policy versions are immutable" an enforced fact rather than a convention.
_CACHE: dict[str, tuple[str, RiskPolicy]] = {}  # path -> (content_hash, policy)
_LAST_GOOD: dict[str, RiskPolicy] = {}
_VERSION_CONTENT: dict[tuple[str, int], str] = {}
_CACHE_LOCK = threading.Lock()


def load_policy(path: str | Path | None = None, *, use_cache: bool = True) -> RiskPolicy:
    """Load the policy, re-parsing only when the file's content changes.

    Cached on a hash of the file's content rather than its modification time:
    an mtime-only cache can be fooled by a filesystem with coarse timestamp
    resolution (some FAT/network filesystems only tick once a second), and a
    tiny YAML file is cheap enough to read and hash on every call that there is
    no real cost to being exact instead of merely fast. That is what lets a
    risk-policy edit take effect on the next request with no redeploy, without
    the correctness gap a timestamp-only cache would carry.

    Raises :class:`PolicyError` if the same ``version`` number has already been
    used for different content — see the module docstring's raise-only
    guarantee: a version number is meant to be a permanent, audit-grade
    identifier for one exact policy, and letting it be silently redefined would
    undermine the "policy versions are immutable" claim requirement 4c depends
    on. Fix a genuine change by bumping ``version`` in the YAML.
    """
    resolved = Path(path or DEFAULT_POLICY_PATH).resolve()
    if not resolved.exists():
        raise PolicyError(f"risk policy file not found: {resolved}")
    key = str(resolved)
    mtime = resolved.stat().st_mtime
    raw_text = resolved.read_text(encoding="utf-8")
    content_hash = _hash_text(raw_text)

    if use_cache:
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached is not None and cached[0] == content_hash:
                return cached[1]

    payload = yaml.safe_load(raw_text)
    if not isinstance(payload, dict):
        raise PolicyError(f"risk policy must be a mapping, got {type(payload).__name__}")
    policy = _parse(payload, resolved, mtime, content_hash)

    with _CACHE_LOCK:
        version_key = (key, policy.version)
        previous_hash = _VERSION_CONTENT.get(version_key)
        if previous_hash is None:
            # No in-memory record — either this is the first load of this
            # version in this process, or the process just restarted and lost
            # everything it knew. Either way, check the DURABLE filesystem
            # archive too: an in-memory-only guard would have amnesia across a
            # restart and could silently accept a version number being reused
            # for different content the moment the service redeployed, which
            # would make "immutable" a claim that only held between restarts.
            archived_path = _archive_dir_for(resolved) / f"v{policy.version}.yaml"
            if archived_path.exists():
                previous_hash = _hash_text(archived_path.read_text(encoding="utf-8"))
        if previous_hash is not None and previous_hash != content_hash:
            raise PolicyError(
                f"policy version {policy.version} at {resolved} was already recorded with different "
                f"content (hash {previous_hash[:12]} vs {content_hash[:12]}); bump 'version' in the YAML "
                "for a genuine change instead of reusing a version number"
            )
        is_new_version = previous_hash is None
        previously_active = _CACHE.get(key)
        _VERSION_CONTENT[version_key] = content_hash
        if use_cache:
            _CACHE[key] = (content_hash, policy)
        _LAST_GOOD[key] = policy

    if is_new_version:
        _archive_version(resolved, policy.version, raw_text)
    if previously_active is not None and previously_active[1].version != policy.version:
        _audit_log.info(
            "policy.changed",
            extra={
                "audit": {
                    "source_path": key,
                    "previous_version": previously_active[1].version,
                    "new_version": policy.version,
                    "content_hash": content_hash,
                }
            },
        )
    return policy


def load_policy_or_fallback(path: str | Path | None = None) -> RiskPolicy:
    """Load the policy, but never let a broken edit take the service down.

    ``load_policy`` raises on a malformed or version-conflicting file, which is
    correct for a first load — there is nothing safe to fall back to. But once
    a policy has loaded successfully at least once, a *later* bad edit (a typo,
    an incomplete save, a bad merge) should not turn every subsequent scoring
    request into a 500: the risk manager who broke the YAML should get a loud
    audit-logged failure and keep serving the last known-good policy until it
    is fixed, on the next successful reload, with no restart required either
    way. This is what :class:`~crr.api.scoring.ScoringService` calls on every
    request.
    """
    resolved = Path(path or DEFAULT_POLICY_PATH).resolve()
    try:
        return load_policy(resolved)
    except (PolicyError, yaml.YAMLError) as exc:
        with _CACHE_LOCK:
            fallback = _LAST_GOOD.get(str(resolved))
        if fallback is None:
            raise
        _audit_log.error(
            "policy.load_failed",
            extra={
                "audit": {
                    "source_path": str(resolved),
                    "error": str(exc),
                    "serving_version": fallback.version,
                }
            },
        )
        return fallback


# --------------------------------------------------------------------------
# Version archive — the "rollback to any prior version" half of the exit
# criterion. Filesystem-based, consistent with the rest of the project: no
# database is needed to keep an immutable copy of a small YAML file per
# version, and the archive is itself something a risk manager can `git log`.
# --------------------------------------------------------------------------


def _resolve_archive_root(archive_root: Path | None) -> Path:
    """``None`` means "the current module default" — resolved here, at call
    time, rather than baked into a function signature as
    ``archive_root: Path = DEFAULT_ARCHIVE_DIR``. Python binds a default
    argument value once, at function *definition* time, so a signature default
    would permanently capture whatever ``DEFAULT_ARCHIVE_DIR`` was when this
    module was first imported — silently ignoring any later override (tests
    monkeypatching it, or a caller reassigning it) and, worse, ignoring it at
    exactly the call sites inside this module (``load_policy`` calling
    ``_archive_version`` with no explicit root) that most need to honour it.
    """
    return archive_root if archive_root is not None else DEFAULT_ARCHIVE_DIR


def _archive_dir_for(path: Path, archive_root: Path | None = None) -> Path:
    return _resolve_archive_root(archive_root) / path.stem


def _archive_version(path: Path, version: int, raw_text: str, archive_root: Path | None = None) -> None:
    directory = _archive_dir_for(path, archive_root)
    directory.mkdir(parents=True, exist_ok=True)
    archive_path = directory / f"v{version}.yaml"
    if not archive_path.exists():
        archive_path.write_text(raw_text, encoding="utf-8")


def list_archived_versions(path: str | Path | None = None, archive_root: Path | None = None) -> list[int]:
    """Every version number ever loaded for this policy file, ascending."""
    resolved = Path(path or DEFAULT_POLICY_PATH).resolve()
    directory = _archive_dir_for(resolved, archive_root)
    if not directory.exists():
        return []
    versions = []
    for candidate in directory.glob("v*.yaml"):
        try:
            versions.append(int(candidate.stem[1:]))
        except ValueError:  # pragma: no cover — defensive against stray files
            continue
    return sorted(versions)


def load_policy_version(
    version: int, path: str | Path | None = None, archive_root: Path | None = None
) -> RiskPolicy:
    """Load a specific historical version from the archive, without touching
    the live active file. Used by the policy-simulation tooling to compare
    "current" against "proposed" and by an operator to inspect a past version
    before rolling back to it."""
    resolved = Path(path or DEFAULT_POLICY_PATH).resolve()
    archive_path = _archive_dir_for(resolved, archive_root) / f"v{version}.yaml"
    if not archive_path.exists():
        available = list_archived_versions(resolved, archive_root)
        raise PolicyError(f"no archived version {version} for {resolved} (have: {available})")
    raw_text = archive_path.read_text(encoding="utf-8")
    payload = yaml.safe_load(raw_text)
    if not isinstance(payload, dict):
        raise PolicyError(f"archived policy version {version} is not a mapping")
    return _parse(payload, archive_path, archive_path.stat().st_mtime, _hash_text(raw_text))


def rollback_to(
    version: int, path: str | Path | None = None, archive_root: Path | None = None
) -> RiskPolicy:
    """Restore an archived version as the active policy.

    Copies the archived file's exact bytes back over the live path, so the
    restored policy is byte-identical to what was actually running under that
    version number — not a re-serialisation that might drift from it — and the
    version-immutability check above passes trivially (the content hash matches
    what was already recorded for that version). Returns the restored,
    now-active policy. This is the "no code, no deploy" rollback path: an
    operator (today, a script; eventually an admin UI) calls this, and the next
    scoring request picks it up exactly as any other policy edit would.
    """
    resolved = Path(path or DEFAULT_POLICY_PATH).resolve()
    restored_source = load_policy_version(version, resolved, archive_root)
    raw_text = Path(restored_source.source_path).read_text(encoding="utf-8")
    resolved.write_text(raw_text, encoding="utf-8")
    _audit_log.info(
        "policy.rolled_back", extra={"audit": {"source_path": str(resolved), "restored_version": version}}
    )
    return load_policy(resolved)
