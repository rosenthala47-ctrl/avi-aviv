"""The scoring service: payload in, composite rating + explanation out.

One shared feature pipeline builds the matrix once; the two model artefacts
(credit default, financial crime) score it; the policy blends the two
probabilities into the published 0-100 score and band. The two per-model SHAP
explanations are merged into a single ranked factor list.

Everything the service needs to reproduce a decision later — the exact model
version, the policy version, a hash of the input — travels with the result, so the
audit record the router writes is sufficient to recompute the score bit-for-bit.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from crr.explain import Explainer
from crr.explain.reason_codes import BY_CODE
from crr.features import FeaturePipeline
from crr.models import ModelArtifact
from crr.policy import RiskPolicy, load_policy

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL_DIR = REPO_ROOT / "models"

DIMENSIONS: tuple[str, ...] = ("credit", "financial_crime")
_TARGET_FOR_DIMENSION = {"credit": "default_12m", "financial_crime": "financial_crime_12m"}


@dataclass
class MergedFactor:
    """One reason code's contribution to the composite, after merging dimensions."""

    code: str
    category: str
    statement: str
    dimension: str
    contribution: float
    direction: str


@dataclass
class Assessment:
    """The full internal result for one customer. The router projects this onto
    the API response schemas and the audit record."""

    customer_id: str
    risk_score: float
    risk_band: str
    credit_probability: float
    financial_crime_probability: float
    top_factors: list[MergedFactor]
    protective_factors: list[MergedFactor]
    requires_review: bool
    model_version: str
    policy_version: int
    scored_at: dt.datetime
    audience: str
    input_hash: str
    latency_ms: float = 0.0
    additivity_error: float = 0.0

    def to_audit_record(self) -> dict[str, Any]:
        """The minimum needed to reproduce and defend this decision."""
        return {
            "customer_id": self.customer_id,
            "scored_at": self.scored_at.isoformat(),
            "risk_score": round(self.risk_score, 4),
            "risk_band": self.risk_band,
            "credit_probability": round(self.credit_probability, 6),
            "financial_crime_probability": round(self.financial_crime_probability, 6),
            "requires_review": self.requires_review,
            "model_version": self.model_version,
            "policy_version": self.policy_version,
            "input_hash": self.input_hash,
            "audience": self.audience,
            "additivity_error": self.additivity_error,
            "latency_ms": round(self.latency_ms, 3),
        }


class ModelBundle:
    """Shared feature pipeline plus one explainer per risk dimension."""

    def __init__(
        self,
        pipeline: FeaturePipeline,
        explainers: dict[str, Explainer],
        artifacts: dict[str, ModelArtifact],
        version: str,
    ) -> None:
        self.pipeline = pipeline
        self.explainers = explainers
        self.artifacts = artifacts
        self.version = version

    @classmethod
    def load(cls, model_dir: str | Path = DEFAULT_MODEL_DIR, *, top_factors: int = 100,
             min_absolute_shap: float = 0.005) -> ModelBundle:
        """Load both dimensions' artefacts from ``model_dir/<target>/``.

        The explainers are built to return *all* factors above ``min_absolute_shap``
        (a high ``top_factors``) so the service can merge across dimensions and
        rank globally rather than being handed each model's pre-truncated top five.
        """
        model_dir = Path(model_dir)
        pipeline: FeaturePipeline | None = None
        explainers: dict[str, Explainer] = {}
        artifacts: dict[str, ModelArtifact] = {}
        digests: list[str] = []

        for dimension, target in _TARGET_FOR_DIMENSION.items():
            target_dir = model_dir / target
            if not (target_dir / "model.txt").exists():
                raise FileNotFoundError(
                    f"no model for '{dimension}' at {target_dir} — run scripts/train_baseline.py --target {target}"
                )
            artifact = ModelArtifact.load(target_dir)
            artifacts[dimension] = artifact
            explainers[dimension] = Explainer.from_artifact(
                artifact, top_factors=top_factors, min_absolute_shap=min_absolute_shap
            )
            if pipeline is None:
                pipeline = FeaturePipeline.load(target_dir)
            digests.append(hashlib.sha256((target_dir / "model.txt").read_bytes()).hexdigest()[:8])

        assert pipeline is not None
        version = "+".join(digests)
        return cls(pipeline, explainers, artifacts, version)


def _hash_payload(payload: dict[str, Any]) -> str:
    """Stable hash of the scoring input, for the audit trail and idempotency."""
    serialised = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(serialised.encode()).hexdigest()[:16]


class ScoringService:
    """Stateless scorer. Persistence and caching are the router's concern."""

    def __init__(self, bundle: ModelBundle, policy: RiskPolicy | None = None) -> None:
        self.bundle = bundle
        self._policy = policy

    @property
    def policy(self) -> RiskPolicy:
        # Re-read on each access so a policy edit is picked up without a restart;
        # load_policy caches on mtime, so this is cheap when nothing changed.
        return self._policy or load_policy()

    def score(
        self,
        customer: dict[str, Any],
        events: list[dict[str, Any]] | None = None,
        *,
        audience: str = "internal",
        explain: bool = True,
        now: dt.datetime | None = None,
    ) -> Assessment:
        """Score one customer. ``customer`` and ``events`` are plain dicts (the
        router passes ``model_dump()`` of the validated payloads).

        ``explain=False`` skips the SHAP pass entirely and returns the score, band
        and probabilities with no factors. That is the low-latency path for a
        real-time yes/no decision; the explanation can be produced on the
        ``explain=True`` path or fetched later. SHAP costs roughly 30ms for the
        two models, which is the difference between comfortably under and around
        the 150ms budget."""
        policy = self.policy
        scored_at = now or dt.datetime.now(dt.UTC)

        features = self._build_features(customer, events or [])

        probabilities: dict[str, float] = {}
        all_factors: list[MergedFactor] = []
        additivity = 0.0
        weights = {"credit": policy.composite.credit_weight,
                   "financial_crime": policy.composite.financial_crime_weight}

        for dimension, explainer in self.bundle.explainers.items():
            artifact = self.bundle.artifacts[dimension]
            if explain:
                probabilities[dimension] = float(artifact.predict_proba(features)[0])
                explanation = explainer.explain_row(
                    str(customer["customer_id"]), features, audience=audience, include_features=False
                )
                additivity = max(additivity, explanation.additivity_error)
                for factor in explanation.top_factors + explanation.protective_factors:
                    all_factors.append(
                        MergedFactor(
                            code=factor.code,
                            category=factor.category,
                            statement=factor.statement,
                            dimension=dimension,
                            contribution=factor.contribution * weights[dimension],
                            direction=factor.direction,
                        )
                    )
            else:
                probabilities[dimension] = float(artifact.predict_proba(features)[0])

        risk_score = policy.composite_score(probabilities["credit"], probabilities["financial_crime"])
        risk_band = policy.band_for_score(risk_score)
        top, protective = self._merge_factors(all_factors, policy.explainability.top_factors,
                                              policy.explainability.min_absolute_shap)

        # Interim review rule: High/Extreme goes to a human. The phase 5 rule
        # engine replaces this with the policy's rule set (which can also floor
        # the band and attach reason codes).
        requires_review = risk_band in ("High", "Extreme")

        return Assessment(
            customer_id=str(customer["customer_id"]),
            risk_score=risk_score,
            risk_band=risk_band,
            credit_probability=probabilities["credit"],
            financial_crime_probability=probabilities["financial_crime"],
            top_factors=top,
            protective_factors=protective,
            requires_review=requires_review,
            model_version=self.bundle.version,
            policy_version=policy.version,
            scored_at=scored_at,
            audience=audience,
            input_hash=_hash_payload({"customer": customer, "events": events or []}),
            additivity_error=additivity,
        )

    def _build_features(self, customer: dict[str, Any], events: list[dict[str, Any]]) -> pd.DataFrame:
        """One-row feature frame. Omitted fields become NaN, never zero."""
        row = dict(customer)
        row.setdefault("split", "serve")
        customers = pd.DataFrame([row])
        customers["snapshot_date"] = pd.to_datetime(customers["snapshot_date"])
        events_frame = (
            pd.DataFrame([dict(e, customer_id=customer["customer_id"]) for e in events])
            if events
            else pd.DataFrame()
        )
        if not events_frame.empty:
            events_frame["event_ts"] = pd.to_datetime(events_frame["event_ts"])
            if "event_id" not in events_frame.columns:
                events_frame.insert(0, "event_id", [f"EVT-REQ-{i}" for i in range(len(events_frame))])
        return self.bundle.pipeline.transform(customers, events_frame)

    def _merge_factors(
        self, factors: list[MergedFactor], top_n: int, min_abs: float
    ) -> tuple[list[MergedFactor], list[MergedFactor]]:
        """Merge factors sharing a reason code, then split into risk/protective.

        The same concern flagged by both dimensions (e.g. cash intensity) is a
        single line to the reader, so contributions for one code are summed. The
        dimension shown is whichever contributed more in magnitude.
        """
        by_code: dict[str, MergedFactor] = {}
        for factor in factors:
            existing = by_code.get(factor.code)
            if existing is None:
                by_code[factor.code] = MergedFactor(**vars(factor))
                continue
            existing.contribution += factor.contribution
            if abs(factor.contribution) > abs(existing.contribution - factor.contribution):
                existing.dimension = factor.dimension

        merged = [f for f in by_code.values() if abs(f.contribution) >= min_abs]
        for factor in merged:
            factor.direction = "increases" if factor.contribution > 0 else "decreases"

        increasing = sorted((f for f in merged if f.contribution > 0), key=lambda f: f.contribution, reverse=True)
        decreasing = sorted((f for f in merged if f.contribution < 0), key=lambda f: f.contribution)
        return increasing[:top_n], decreasing[:top_n]


def visible_factor(code: str) -> bool:
    """Whether a reason code may appear in a customer-facing response."""
    reason_code = BY_CODE.get(code)
    return reason_code is None or reason_code.customer_visible
