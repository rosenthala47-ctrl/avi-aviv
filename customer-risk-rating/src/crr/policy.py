"""Load and validate the risk policy (``config/risk_policy.yaml``).

The policy is the risk manager's no-code control surface (requirement 4c). This
module reads it into a typed, validated object so a malformed edit fails at load
with a clear message instead of silently changing scores. Phase 4 uses the band,
composite and explainability sections; the rule engine (phase 5) reads the
``rules`` section from the same object.

Loaded once and cached by file path + modification time, so a running service
picks up an edit on the next request without a redeploy, but does not re-parse the
file on every score.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = REPO_ROOT / "config" / "risk_policy.yaml"

RISK_BANDS: tuple[str, ...] = ("Low", "Medium", "High", "Extreme")


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


@dataclass(frozen=True)
class RiskPolicy:
    version: int
    bands: BandThresholds
    composite: CompositeConfig
    explainability: ExplainabilityConfig
    rules: tuple[Rule, ...]
    rescoring: dict[str, Any] = field(default_factory=dict)
    feedback: dict[str, Any] = field(default_factory=dict)
    source_path: str = ""
    source_mtime: float = 0.0

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


def _parse(payload: dict[str, Any], path: Path, mtime: float) -> RiskPolicy:
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
            )
        )

    return RiskPolicy(
        version=int(payload.get("version", 0)),
        bands=bands,
        composite=composite,
        explainability=explainability,
        rules=tuple(rules),
        rescoring=payload.get("rescoring", {}),
        feedback=payload.get("feedback", {}),
        source_path=str(path),
        source_mtime=mtime,
    )


_CACHE: dict[str, RiskPolicy] = {}
_CACHE_LOCK = threading.Lock()


def load_policy(path: str | Path | None = None, *, use_cache: bool = True) -> RiskPolicy:
    """Load the policy, re-parsing only when the file changes.

    The cache key is the resolved path; the value is invalidated when the file's
    modification time advances. That is what lets a risk-policy edit take effect
    on the next request without a redeploy while keeping the hot path allocation-
    free.
    """
    resolved = Path(path or DEFAULT_POLICY_PATH).resolve()
    if not resolved.exists():
        raise PolicyError(f"risk policy file not found: {resolved}")
    mtime = resolved.stat().st_mtime
    key = str(resolved)

    if use_cache:
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached is not None and cached.source_mtime == mtime:
                return cached

    payload = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PolicyError(f"risk policy must be a mapping, got {type(payload).__name__}")
    policy = _parse(payload, resolved, mtime)

    if use_cache:
        with _CACHE_LOCK:
            _CACHE[key] = policy
    return policy
