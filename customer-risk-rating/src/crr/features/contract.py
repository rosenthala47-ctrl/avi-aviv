"""The feature contract: a versioned, enforceable description of the model's inputs.

Two jobs, both about failing loudly instead of quietly:

1. **Leakage.** Some columns can never be features — the outcome labels, the
   generator's latent state, and PII. They are rejected by name, at build time,
   before a model ever sees them. A model that silently trains on
   ``p_default_true`` scores 0.99 and is worthless, and nothing about its metrics
   would tell you.

2. **Training/serving skew.** The contract produced when the pipeline is fitted is
   saved alongside the model. At scoring time the incoming frame is validated
   against it: same columns, same order, same types, values in range. Without
   this, a renamed upstream column turns into an all-null feature and the model
   keeps returning confident nonsense.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import pandas as pd

CONTRACT_VERSION = "1.0.0"

FeatureKind = Literal["numeric", "categorical", "indicator"]


#: Columns that must never appear in a feature frame, whatever the caller intends.
#: Outcome labels and anything from the generator's hidden state.
FORBIDDEN_EXACT: frozenset[str] = frozenset(
    {
        # outcome labels and their by-products
        "default_12m",
        "financial_crime_12m",
        "days_past_due_at_outcome",
        "outcome_observation_date",
        # the human decision — legitimate input to the phase 8 feedback study,
        # never an input to the risk model itself: it is made with knowledge of
        # the case and would leak the answer backwards.
        "underwriter_decision",
        "underwriter_confidence",
        "underwriter_override_flag",
        "underwriter_perceived_score",
        # generator ground truth
        "z_credit_quality",
        "z_liquidity_stress",
        "z_volatility",
        "z_concealment",
        "latent_distress",
        "latent_concealment_text",
        "narrative_distress_level",
        "narrative_concealment_level",
        "p_default_true",
        "p_financial_crime_true",
        "true_risk_score",
        "true_risk_band",
        "duplicate_of_customer_id",
        # direct identifiers
        "full_name",
        "national_id",
        "email",
        "phone",
        "address_line",
    }
)

#: Substring guards, so a renamed or derived leak is still caught.
FORBIDDEN_SUBSTRINGS: tuple[str, ...] = ("_true", "true_risk", "latent_", "z_credit", "z_liquidity", "z_concealment")


class LeakageError(ValueError):
    """Raised when a forbidden column reaches a feature frame."""


class ContractViolation(ValueError):
    """Raised when a frame does not match the contract it is validated against."""


@dataclass(frozen=True)
class FeatureSpec:
    """One column of the model input."""

    name: str
    kind: FeatureKind
    block: str
    description: str
    minimum: float | None = None
    maximum: float | None = None
    nullable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FeatureContract:
    """The full set of features a fitted pipeline produces."""

    version: str = CONTRACT_VERSION
    pipeline_version: str = "0.0.0"
    specs: tuple[FeatureSpec, ...] = field(default_factory=tuple)
    categories: dict[str, list[str]] = field(default_factory=dict)
    """Vocabulary per categorical feature, learned at fit time."""

    # ---- introspection ---------------------------------------------------

    @property
    def names(self) -> list[str]:
        return [spec.name for spec in self.specs]

    @property
    def categorical_names(self) -> list[str]:
        return [spec.name for spec in self.specs if spec.kind == "categorical"]

    @property
    def numeric_names(self) -> list[str]:
        return [spec.name for spec in self.specs if spec.kind != "categorical"]

    def spec(self, name: str) -> FeatureSpec:
        for candidate in self.specs:
            if candidate.name == name:
                return candidate
        raise KeyError(name)

    # ---- enforcement -----------------------------------------------------

    def validate(self, frame: pd.DataFrame, *, check_ranges: bool = True) -> None:
        """Raise if ``frame`` does not match this contract."""
        assert_no_leakage(frame)

        actual, expected = list(frame.columns), self.names
        if actual != expected:
            missing = [c for c in expected if c not in actual]
            extra = [c for c in actual if c not in expected]
            detail = []
            if missing:
                detail.append(f"missing {missing[:10]}{'...' if len(missing) > 10 else ''}")
            if extra:
                detail.append(f"unexpected {extra[:10]}{'...' if len(extra) > 10 else ''}")
            if not detail:
                detail.append("column order differs")
            raise ContractViolation("; ".join(detail))

        problems: list[str] = []
        for spec in self.specs:
            column = frame[spec.name]
            if not spec.nullable and column.isna().any():
                problems.append(f"{spec.name}: nulls not allowed")
            if spec.kind == "categorical":
                continue
            if not check_ranges:
                continue
            values = pd.to_numeric(column, errors="coerce")
            if spec.minimum is not None and (values < spec.minimum).any():
                problems.append(f"{spec.name}: below minimum {spec.minimum}")
            if spec.maximum is not None and (values > spec.maximum).any():
                problems.append(f"{spec.name}: above maximum {spec.maximum}")
        if problems:
            raise ContractViolation("; ".join(problems))

    # ---- persistence -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "pipeline_version": self.pipeline_version,
            "specs": [spec.to_dict() for spec in self.specs],
            "categories": self.categories,
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FeatureContract:
        return cls(
            version=payload["version"],
            pipeline_version=payload.get("pipeline_version", "0.0.0"),
            specs=tuple(FeatureSpec(**spec) for spec in payload["specs"]),
            categories=payload.get("categories", {}),
        )

    @classmethod
    def load(cls, path: str | Path) -> FeatureContract:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def assert_no_leakage(frame: pd.DataFrame) -> None:
    """Raise :class:`LeakageError` if any forbidden column is present.

    Called on every feature frame the pipeline produces, not just in tests. The
    cost is a set lookup per column; the cost of missing one is a model that
    looks excellent, ships, and fails in production.
    """
    columns = set(frame.columns)
    exact = sorted(columns & FORBIDDEN_EXACT)
    fuzzy = sorted(
        column
        for column in columns
        if column not in exact and any(token in column for token in FORBIDDEN_SUBSTRINGS)
    )
    if exact or fuzzy:
        raise LeakageError(
            "forbidden columns in feature frame: "
            + ", ".join(exact + [f"{c} (matched a forbidden pattern)" for c in fuzzy])
        )
