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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from crr.explain import Explainer
from crr.explain.reason_codes import BY_CODE
from crr.features import FeaturePipeline
from crr.llm.batch import extract_all
from crr.llm.extraction import Extractor
from crr.models import ModelArtifact
from crr.policy import RiskPolicy, load_policy_or_fallback
from crr.rules import FiredRule, RuleEngine

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
    model_band: str
    risk_band: str
    band_floor_applied: bool
    credit_probability: float
    financial_crime_probability: float
    top_factors: list[MergedFactor]
    protective_factors: list[MergedFactor]
    fired_rules: list[FiredRule]
    requires_review: bool
    model_version: str
    policy_version: int
    scored_at: dt.datetime
    audience: str
    input_hash: str
    customer_snapshot: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    additivity_error: float = 0.0
    degraded: bool = False
    """True when narrative text was supplied but no real extraction happened
    (no extractor configured, or the LLM was unavailable) — the score is
    tabular-only rather than failing the request (roadmap phase 7)."""

    def to_audit_record(self) -> dict[str, Any]:
        """The minimum needed to reproduce and defend this decision.

        ``risk_score`` stays the model's continuous composite even when a rule
        floors the band: inflating the NUMBER to match a floored BAND would be
        dishonest (a customer whose model score is 40 and gets floored to
        Extreme by a sanctions hit should show 40 with an Extreme band and the
        rule that did it, not a fabricated 97). ``model_band`` is what the
        score alone produced; ``risk_band`` is what was actually acted on.
        """
        return {
            "customer_id": self.customer_id,
            "scored_at": self.scored_at.isoformat(),
            "risk_score": round(self.risk_score, 4),
            "model_band": self.model_band,
            "risk_band": self.risk_band,
            "band_floor_applied": self.band_floor_applied,
            "credit_probability": round(self.credit_probability, 6),
            "financial_crime_probability": round(self.financial_crime_probability, 6),
            "requires_review": self.requires_review,
            "fired_rule_ids": [r.id for r in self.fired_rules],
            "model_version": self.model_version,
            "policy_version": self.policy_version,
            "input_hash": self.input_hash,
            "audience": self.audience,
            "additivity_error": self.additivity_error,
            "latency_ms": round(self.latency_ms, 3),
            "degraded": self.degraded,
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

    def __init__(self, bundle: ModelBundle, policy: RiskPolicy | None = None, extractor: Extractor | None = None) -> None:
        self.bundle = bundle
        self._policy = policy
        self._extractor = extractor
        self._engine_cache: dict[str, RuleEngine] = {}

    @property
    def policy(self) -> RiskPolicy:
        # Re-read on each access so a policy edit is picked up without a restart.
        # load_policy_or_fallback caches on content hash, so this is cheap when
        # nothing changed, and degrades to the last known-good policy rather than
        # failing every request if the file is mid-edit or broken.
        return self._policy or load_policy_or_fallback()

    def _rule_engine(self, policy: RiskPolicy) -> RuleEngine:
        """The compiled rule engine for this policy's exact content.

        Compiling walks and validates every rule's AST (see
        ``crr.rules.expressions``), which is unnecessary work to repeat on every
        request when the policy has not changed — cached by content hash, the
        same key ``load_policy`` itself uses to decide whether anything changed.
        """
        cached = self._engine_cache.get(policy.content_hash)
        if cached is not None:
            return cached
        engine = RuleEngine(policy.rules)
        # An unbounded cache would leak memory across policy edits over a long
        # process lifetime; in practice policy edits are rare operator actions,
        # so keeping only the current and previous few versions is enough headroom
        # for in-flight requests spanning a reload, without growing forever.
        if len(self._engine_cache) >= 8:
            self._engine_cache.pop(next(iter(self._engine_cache)))
        self._engine_cache[policy.content_hash] = engine
        return engine

    def score(
        self,
        customer: dict[str, Any],
        events: list[dict[str, Any]] | None = None,
        narratives: dict[str, str] | None = None,
        *,
        audience: str = "internal",
        explain: bool = True,
        now: dt.datetime | None = None,
    ) -> Assessment:
        """Score one customer. ``customer``, ``events`` and ``narratives`` are
        plain dicts (the router passes ``model_dump()`` of the validated
        payloads).

        ``narratives`` is optional free text (support-call summary,
        underwriter note, KYC extract) fed through the configured extractor
        (phase 7). No narratives, or no extractor configured, or the
        extractor unavailable, all score tabular-only — only the last one
        sets ``Assessment.degraded``, since it is the only one that means "we
        tried and could not."

        ``explain=False`` skips the SHAP pass entirely and returns the score, band
        and probabilities with no factors. That is the low-latency path for a
        real-time yes/no decision; the explanation can be produced on the
        ``explain=True`` path or fetched later. SHAP costs roughly 30ms for the
        two models, which is the difference between comfortably under and around
        the 150ms budget."""
        policy = self.policy
        scored_at = now or dt.datetime.now(dt.UTC)

        text_features, degraded = self._narrative_features(customer, narratives)
        features = self._build_features(customer, events or [], text_features)

        probabilities: dict[str, float] = {}
        all_factors: list[MergedFactor] = []
        additivity = 0.0
        weights = {"credit": policy.composite.credit_weight,
                   "financial_crime": policy.composite.financial_crime_weight}

        for dimension, explainer in self.bundle.explainers.items():
            artifact = self.bundle.artifacts[dimension]
            if explain:
                probabilities[dimension] = float(artifact.predict_proba(features)[0])
                # Always compute the full internal view here, regardless of the
                # audience the caller ultimately wants displayed. Filtering by
                # audience is applied once, at the response/projection layer
                # (crr.api.projections), never baked into what gets computed or
                # persisted — otherwise a score first requested for a customer
                # display would permanently lose the internal-only factors, and
                # a LATER `GET /explain?audience=internal` on that same score
                # could never recover them. The stored record must hold
                # everything a reviewer might need to see.
                explanation = explainer.explain_row(
                    str(customer["customer_id"]), features, audience="internal", include_features=False
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
        model_band = policy.band_for_score(risk_score)
        top, protective = self._merge_factors(all_factors, policy.explainability.top_factors,
                                              policy.explainability.min_absolute_shap)

        # The rule engine evaluates against the RAW customer record — the same
        # field names a risk manager writes in risk_policy.yaml and the same
        # ones in the API's input contract — never the pipeline's internal
        # encoded feature matrix. A rule can only raise the band or force
        # review (see crr.rules.engine); it is structurally incapable of
        # lowering either, however the policy file is edited.
        outcome = self._rule_engine(policy).apply(model_band, customer, review_bands=policy.review_bands)

        return Assessment(
            customer_id=str(customer["customer_id"]),
            risk_score=risk_score,
            model_band=model_band,
            risk_band=outcome.final_band,
            band_floor_applied=outcome.band_floor_applied,
            credit_probability=probabilities["credit"],
            financial_crime_probability=probabilities["financial_crime"],
            top_factors=top,
            protective_factors=protective,
            fired_rules=list(outcome.fired_rules),
            requires_review=outcome.requires_review,
            customer_snapshot=dict(customer),
            model_version=self.bundle.version,
            policy_version=policy.version,
            scored_at=scored_at,
            audience=audience,
            input_hash=_hash_payload({"customer": customer, "events": events or [], "narratives": narratives or {}}),
            additivity_error=additivity,
            degraded=degraded,
        )

    def _narrative_features(
        self, customer: dict[str, Any], narratives: dict[str, str] | None
    ) -> tuple[pd.DataFrame | None, bool]:
        """Runs ``narratives`` through the configured extractor and returns the
        raw extraction frame ``FeaturePipeline`` expects (the same shape
        ``crr.llm.batch.extract_all`` produces for training — one code path,
        not two that could drift), plus whether the request degraded to
        tabular-only. No narratives supplied is not a degradation — it is
        simply nothing to extract, same as a customer with no events."""
        if not narratives or not any(narratives.values()):
            return None, False
        if self._extractor is None:
            return None, True

        narratives_frame = pd.DataFrame(
            [
                {
                    "customer_id": str(customer["customer_id"]),
                    "support_call_summary": narratives.get("support_call_summary") or "",
                    "underwriter_note": narratives.get("underwriter_note") or "",
                    "kyc_document_extract": narratives.get("kyc_document_extract") or "",
                }
            ]
        )
        extraction_frame = extract_all(narratives_frame, self._extractor)
        degraded = bool(extraction_frame["degraded"].iloc[0])
        return (None if degraded else extraction_frame), degraded

    def _build_features(
        self, customer: dict[str, Any], events: list[dict[str, Any]], text_features: pd.DataFrame | None = None
    ) -> pd.DataFrame:
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
        return self.bundle.pipeline.transform(customers, events_frame, text_features)

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
    """Whether a SHAP-derived reason code may appear in a customer-facing response."""
    reason_code = BY_CODE.get(code)
    return reason_code is None or reason_code.customer_visible


def filter_for_audience(
    factors: list[dict[str, Any]], fired_rules: list[dict[str, Any]], audience: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reduce a full (internal) factor/rule set down to what an audience may see.

    The single place this filtering happens, applied identically whether the
    caller is reading the immediate ``POST /score`` response or a later
    ``GET /explain`` fetch of the same stored record — so the two can never
    disagree about what is safe to show a customer. ``factors`` and
    ``fired_rules`` are the plain-dict (JSON-serialisable) forms already used
    for storage, so this works directly against a persisted record with no
    reconstruction of dataclasses.
    """
    if audience != "customer":
        return factors, fired_rules
    visible_factors = [f for f in factors if visible_factor(f["code"])]
    visible_rules = [r for r in fired_rules if r.get("customer_visible", True)]
    return visible_factors, visible_rules
