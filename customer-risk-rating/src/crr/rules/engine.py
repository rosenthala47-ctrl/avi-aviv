"""The rule engine: deterministic, policy-driven overrides applied after the model.

Requirement 4c is a risk manager who can retune the system by editing
``config/risk_policy.yaml`` — no code change, no deploy. This module is the part
that makes an edited ``rules:`` list actually change behaviour, on top of
:mod:`crr.policy` (which loads and structurally validates the file) and
:mod:`crr.rules.expressions` (which evaluates one rule's ``when`` clause safely).

The one property this module exists to guarantee: **a rule can only raise risk or
force review, never lower it.** A policy edit that could quietly disable a
control — even by accident, a typo, a bad merge — would be worse than no rule
engine at all, because it would look like the safeguard is still there. That
guarantee is enforced structurally, not by convention:

* the policy loader already rejects a ``floor_band`` outside the four valid bands
  (nothing lower than the enum, and there is no "lower_to" action — the schema
  has no verb that could reduce risk);
* this module combines a fired rule's floor with the model's band via
  ``max(...)`` on the band's ordinal rank, so applying zero, one, or many rules
  is monotone by construction — :func:`RuleEngine.apply` cannot return a band
  below what the model alone produced, and a test in ``tests/test_rules.py``
  tries to construct a counter-example and confirms it cannot.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

from crr.policy import RISK_BANDS, Rule
from crr.rules.expressions import ExpressionError, compile_expression, evaluate

#: Ordinal rank of each band, low to high. The single source of truth for "is
#: band X at least as severe as band Y" — every raise-only comparison in this
#: module goes through this, not string equality or YAML ordering.
_BAND_RANK: dict[str, int] = {band: i for i, band in enumerate(RISK_BANDS)}


@dataclass(frozen=True)
class FiredRule:
    """One rule that matched a customer, and what it did."""

    id: str
    description: str
    reason_code: str
    floor_band: str | None
    require_review: bool
    customer_visible: bool = True


@dataclass(frozen=True)
class RuleOutcome:
    """The result of applying every enabled rule to one customer."""

    model_band: str
    final_band: str
    band_floor_applied: bool
    requires_review: bool
    fired_rules: tuple[FiredRule, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_band": self.model_band,
            "final_band": self.final_band,
            "band_floor_applied": self.band_floor_applied,
            "requires_review": self.requires_review,
            "fired_rules": [
                {
                    "id": r.id,
                    "description": r.description,
                    "reason_code": r.reason_code,
                    "floor_band": r.floor_band,
                    "require_review": r.require_review,
                    "customer_visible": r.customer_visible,
                }
                for r in self.fired_rules
            ],
        }


class RuleEngineError(ValueError):
    """A rule failed to compile. Raised at engine construction, not per-request,
    so a bad rule is caught when the policy loads, not silently ignored during
    scoring."""


def _max_band(a: str, b: str) -> str:
    """The more severe of two bands, by rank — the raise-only primitive
    everything else in this module is built from."""
    return a if _BAND_RANK[a] >= _BAND_RANK[b] else b


class RuleEngine:
    """Compiles a policy's rules once, evaluates them against many customers.

    Compiling at construction (not per-call) means the AST-walk safety check in
    :mod:`crr.rules.expressions` runs once per policy load, not once per score —
    the hot path only walks a pre-validated tree.
    """

    def __init__(self, rules: tuple[Rule, ...]) -> None:
        self._compiled: list[tuple[Rule, ast.Expression]] = []
        for rule in rules:
            if not rule.enabled:
                continue
            try:
                self._compiled.append((rule, compile_expression(rule.when)))
            except ExpressionError as exc:
                raise RuleEngineError(f"rule {rule.id!r} has an invalid 'when' clause: {exc}") from exc

    @property
    def rule_count(self) -> int:
        return len(self._compiled)

    def evaluate(self, record: dict[str, Any]) -> list[FiredRule]:
        """Which enabled rules match this customer record."""
        fired = []
        for rule, compiled in self._compiled:
            if evaluate(compiled, record):
                fired.append(
                    FiredRule(
                        id=rule.id,
                        description=rule.description,
                        reason_code=rule.reason_code,
                        floor_band=rule.floor_band,
                        require_review=rule.require_review,
                        customer_visible=rule.customer_visible,
                    )
                )
        return fired

    def apply(
        self, model_band: str, record: dict[str, Any], *, review_bands: frozenset[str] = frozenset()
    ) -> RuleOutcome:
        """Combine the model's band with every fired rule's floor.

        ``review_bands`` is the policy's band-level review threshold (e.g.
        ``{"High", "Extreme"}``) — a customer lands there on the model score
        alone, with no rule needing to fire, exactly as a rule's own
        ``require_review`` would. It is a policy-level threshold rather than a
        rule because it compares the *computed* band, which is not a field on
        the customer record a ``when`` expression could reference.
        """
        if model_band not in _BAND_RANK:
            raise ValueError(f"unknown band {model_band!r}")

        fired = self.evaluate(record)
        final_band = model_band
        for rule in fired:
            if rule.floor_band is not None:
                final_band = _max_band(final_band, rule.floor_band)

        requires_review = final_band in review_bands or any(r.require_review for r in fired)

        return RuleOutcome(
            model_band=model_band,
            final_band=final_band,
            band_floor_applied=final_band != model_band,
            requires_review=requires_review,
            fired_rules=tuple(fired),
        )
