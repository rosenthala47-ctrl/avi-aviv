"""Policy simulation: "what would change if this proposed policy went live?"

The last piece of requirement 4c's "no code, no deploy" promise — a risk manager
should be able to see the consequence of an edit before it is live, not after.

This deliberately does **not** re-run the ML model or SHAP. Everything downstream
of the model's two probabilities (the composite blend, the band cut-offs, the
rule floors, the review threshold) is a pure function of policy content, so
replaying it against every stored score's *already-computed* probabilities and
*stored customer snapshot* is exact — not an approximation — and cheap enough to
run against months of history in well under a second. Only a change to the model
itself would need re-inference, and that is a retrain (phase 8), not a policy
edit.

This is also the reason phase 5 stores ``customer_snapshot`` on every score (see
``crr.api.repository.StoredScore``): without the original input, a rule change
could not be replayed, because rules evaluate against raw customer fields the
aggregate stored outputs alone do not carry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from crr.api.repository import StoredScore
from crr.policy import RiskPolicy
from crr.rules.engine import RuleEngine, RuleOutcome


@dataclass(frozen=True)
class Rederived:
    """One policy's verdict on one stored score, recomputed from its
    already-known model probabilities."""

    risk_score: float
    outcome: RuleOutcome


@dataclass
class CustomerDelta:
    """How one customer's outcome differs between the current and proposed policy."""

    customer_id: str
    current: Rederived
    proposed: Rederived

    @property
    def band_changed(self) -> bool:
        return self.current.outcome.final_band != self.proposed.outcome.final_band

    @property
    def review_changed(self) -> bool:
        return self.current.outcome.requires_review != self.proposed.outcome.requires_review

    @property
    def rules_gained(self) -> tuple[str, ...]:
        before = {r.id for r in self.current.outcome.fired_rules}
        after = {r.id for r in self.proposed.outcome.fired_rules}
        return tuple(sorted(after - before))

    @property
    def rules_lost(self) -> tuple[str, ...]:
        before = {r.id for r in self.current.outcome.fired_rules}
        after = {r.id for r in self.proposed.outcome.fired_rules}
        return tuple(sorted(before - after))


@dataclass
class SimulationReport:
    """The full diff between two policies over one sample of historical scores."""

    current_version: int
    proposed_version: int
    n_evaluated: int
    band_transitions: dict[tuple[str, str], int] = field(default_factory=dict)
    """(from_band, to_band) -> count, including (X, X) for unchanged."""
    review_gained: int = 0
    review_lost: int = 0
    rule_fire_counts_current: dict[str, int] = field(default_factory=dict)
    rule_fire_counts_proposed: dict[str, int] = field(default_factory=dict)
    changed_customers: list[CustomerDelta] = field(default_factory=list)
    """Every customer whose band or review status changed — capped by the
    caller's sample size, not truncated further here, since a risk manager
    reviewing a policy change needs to see all of them, not a preview."""

    @property
    def n_band_changed(self) -> int:
        return sum(count for (a, b), count in self.band_transitions.items() if a != b)

    @property
    def n_unchanged(self) -> int:
        """Customers where NEITHER band nor review status would change at all —
        the complement of ``changed_customers``, which already tracks exactly
        that condition (see ``simulate``), so this is a direct count rather
        than a derivation from the band-transition table."""
        return self.n_evaluated - len(self.changed_customers)


def rederive(policy: RiskPolicy, engine: RuleEngine, score: StoredScore) -> Rederived:
    """Recompute one stored score's outcome under a given policy."""
    risk_score = policy.composite_score(score.credit_probability, score.financial_crime_probability)
    model_band = policy.band_for_score(risk_score)
    outcome = engine.apply(model_band, score.customer_snapshot, review_bands=policy.review_bands)
    return Rederived(risk_score=risk_score, outcome=outcome)


def simulate(
    current_policy: RiskPolicy, proposed_policy: RiskPolicy, scores: list[StoredScore]
) -> SimulationReport:
    """Replay ``scores`` (typically the last N days, via
    ``ScoreRepository.recent``) under both policies and report every difference.

    Deduplicates to each customer's most recent score in the sample, so a
    customer scored many times in the window is not overcounted — the question
    a risk manager is asking is "how many CUSTOMERS would this affect", not "how
    many historical score EVENTS".
    """
    current_engine = RuleEngine(current_policy.rules)
    proposed_engine = RuleEngine(proposed_policy.rules)

    latest_per_customer: dict[str, StoredScore] = {}
    for score in scores:
        existing = latest_per_customer.get(score.customer_id)
        if existing is None or score.scored_at > existing.scored_at:
            latest_per_customer[score.customer_id] = score

    report = SimulationReport(
        current_version=current_policy.version, proposed_version=proposed_policy.version,
        n_evaluated=len(latest_per_customer),
    )

    for customer_id, score in latest_per_customer.items():
        current = rederive(current_policy, current_engine, score)
        proposed = rederive(proposed_policy, proposed_engine, score)

        transition = (current.outcome.final_band, proposed.outcome.final_band)
        report.band_transitions[transition] = report.band_transitions.get(transition, 0) + 1

        for rule in current.outcome.fired_rules:
            report.rule_fire_counts_current[rule.id] = report.rule_fire_counts_current.get(rule.id, 0) + 1
        for rule in proposed.outcome.fired_rules:
            report.rule_fire_counts_proposed[rule.id] = report.rule_fire_counts_proposed.get(rule.id, 0) + 1

        if not proposed.outcome.requires_review and current.outcome.requires_review:
            report.review_lost += 1
        elif proposed.outcome.requires_review and not current.outcome.requires_review:
            report.review_gained += 1

        delta = CustomerDelta(customer_id=customer_id, current=current, proposed=proposed)
        if delta.band_changed or delta.review_changed:
            report.changed_customers.append(delta)

    return report


def format_report(report: SimulationReport) -> str:
    """Human-readable summary for the CLI."""
    lines: list[str] = []
    add = lines.append
    add("=" * 74)
    add(f"POLICY SIMULATION: v{report.current_version} (current) -> v{report.proposed_version} (proposed)")
    add("=" * 74)
    add(f"  customers evaluated: {report.n_evaluated:,}")
    add(f"  band changed:        {report.n_band_changed:,}")
    add(f"  review gained:       {report.review_gained:,}  (customers newly requiring review)")
    add(f"  review lost:         {report.review_lost:,}  (customers no longer requiring review)")
    add("")

    if report.band_transitions:
        add("BAND TRANSITIONS (current -> proposed)")
        for (before, after), count in sorted(report.band_transitions.items(), key=lambda kv: -kv[1]):
            marker = "  (unchanged)" if before == after else "  <-- CHANGED"
            add(f"  {before:<10} -> {after:<10} {count:>8,}{marker}")
        add("")

    all_rule_ids = sorted(set(report.rule_fire_counts_current) | set(report.rule_fire_counts_proposed))
    if all_rule_ids:
        add("RULE FIRE COUNTS (current vs proposed)")
        add(f"  {'rule':<28}{'current':>10}{'proposed':>10}{'delta':>8}")
        for rule_id in all_rule_ids:
            before = report.rule_fire_counts_current.get(rule_id, 0)
            after = report.rule_fire_counts_proposed.get(rule_id, 0)
            add(f"  {rule_id:<28}{before:>10,}{after:>10,}{after - before:>+8,}")
        add("")

    if report.changed_customers:
        add(f"CHANGED CUSTOMERS ({len(report.changed_customers):,})")
        add(f"  {'customer_id':<18}{'current':>10}{'proposed':>10}  rules gained / lost")
        for delta in report.changed_customers:
            rule_note = ""
            if delta.rules_gained:
                rule_note += f"  +{','.join(delta.rules_gained)}"
            if delta.rules_lost:
                rule_note += f"  -{','.join(delta.rules_lost)}"
            if not rule_note:
                rule_note = "  (band cutoff / review threshold only, no rule change)"
            add(f"  {delta.customer_id:<18}{delta.current.outcome.final_band:>10}"
                f"{delta.proposed.outcome.final_band:>10}{rule_note}")
    else:
        add("No customer's band or review status would change.")
    add("=" * 74)
    return "\n".join(lines)
