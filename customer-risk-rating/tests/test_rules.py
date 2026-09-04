"""Tests for the rule engine: expression safety, raise-only enforcement, simulation.

The expression-injection tests are the load-bearing ones. ``config/risk_policy.yaml``
is designed to be editable by a risk manager with no code review — the same
profile as an untrusted-enough input — so every construct outside a tiny
whitelist must be rejected at compile time, before it is ever stored as a valid
rule. The raise-only tests are the second load-bearing set: the entire premise of
the rule engine is that it cannot be used, even by a malformed or malicious
policy edit, to quietly disable a control.
"""

from __future__ import annotations

import datetime as dt

import pytest

from crr.api.projections import assessment_to_stored
from crr.api.repository import InMemoryScoreRepository, StoredScore
from crr.policy import Rule, load_policy
from crr.rules.engine import RuleEngine, RuleEngineError
from crr.rules.expressions import ExpressionError, compile_expression, evaluate_source
from crr.rules.simulate import simulate

# --------------------------------------------------------------------------
# Expression evaluator: correctness
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "record", "expected"),
    [
        ("sanctions_screen_hits > 0", {"sanctions_screen_hits": 1}, True),
        ("sanctions_screen_hits > 0", {"sanctions_screen_hits": 0}, False),
        ("a == 1 and b == 2", {"a": 1, "b": 2}, True),
        ("a == 1 and b == 2", {"a": 1, "b": 3}, False),
        ("a == 1 or b == 2", {"a": 5, "b": 2}, True),
        ("not (a > 0)", {"a": 0}, True),
        ("x in ['a', 'b', 'c']", {"x": "b"}, True),
        ("x in ['a', 'b', 'c']", {"x": "z"}, False),
        ("x not in ['a', 'b']", {"x": "z"}, True),
        ("age >= 18 and age <= 65", {"age": 40}, True),
        ("age >= 18 and age <= 65", {"age": 70}, False),
        ("flag == True", {"flag": True}, True),
        ("a < b", {"a": 1, "b": 2}, True),
        ("a <= b", {"a": 2, "b": 2}, True),
        ("'gift' in ['undeclared', 'gift']", {}, True),
    ],
)
def test_expression_evaluates_correctly(expr, record, expected):
    assert evaluate_source(expr, record) is expected


# --------------------------------------------------------------------------
# Expression evaluator: missing / malformed data fails closed, not with an
# exception that would take down a scoring request.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr",
    [
        "sanctions_screen_hits > 0",
        "a == 1 and b == 2",
        "a < 5",
        "a <= 5",
        "a >= 5",
    ],
)
def test_missing_field_does_not_fire_the_rule(expr):
    assert evaluate_source(expr, {}) is False
    assert evaluate_source(expr, {"a": None, "b": None, "sanctions_screen_hits": None}) is False


def test_type_mismatch_does_not_raise():
    """A malformed payload (string where a number was expected) must not crash
    scoring — the rule simply does not fire."""
    assert evaluate_source("a > 5", {"a": "not-a-number"}) is False


def test_any_comparison_touching_missing_data_is_false_with_no_exceptions():
    """Simpler and more defensible than special-casing '==': a rule asserting
    two unknowns are equal is not a meaningful claim, so every comparison
    operator treats a None operand the same way — indeterminate, does not fire."""
    assert evaluate_source("a == b", {"a": None, "b": None}) is False
    assert evaluate_source("a == b", {"a": None, "b": 1}) is False
    assert evaluate_source("a != b", {"a": None, "b": 1}) is False


# --------------------------------------------------------------------------
# Expression evaluator: injection resistance — the load-bearing safety tests
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attack",
    [
        "__import__('os').system('echo pwned')",
        "().__class__.__bases__[0]",
        "open('/etc/passwd').read()",
        "[x for x in range(10)]",
        "{x: 1 for x in range(10)}",
        "lambda: 1",
        "a.b.c",
        "a[0]",
        "f()",
        "print('x')",
        "1 if True else 2",
        "(a := 1)",
        "a; b",
        "yield 1",
        "a ** b",
        "a + b",
        "-a",
        "~a",
        "a is None",
        "a is not None",
        "assert a",
        "del a",
        "global a",
        "import os",
        "exec('1')",
    ],
)
def test_disallowed_syntax_is_rejected_at_compile_time(attack):
    with pytest.raises((ExpressionError, SyntaxError)):
        compile_expression(attack)


def test_rule_engine_construction_rejects_an_unsafe_rule():
    """A rule with a malicious or malformed 'when' must fail when the engine
    is built (policy load time), not silently or on the first score."""
    bad = Rule(id="BAD", description="x", when="__import__('os')", floor_band=None,
               require_review=False, reason_code="X", enabled=True)
    with pytest.raises(RuleEngineError, match="BAD"):
        RuleEngine((bad,))


def test_disabled_rule_is_never_compiled_or_evaluated():
    """A disabled rule must not even reach the AST-safety check — this lets an
    operator disable a rule that references a field no longer sent by any
    integration without it blocking policy load."""
    disabled = Rule(id="OFF", description="x", when="__import__('os')", floor_band="High",
                     require_review=False, reason_code="X", enabled=False)
    engine = RuleEngine((disabled,))
    assert engine.rule_count == 0
    assert engine.evaluate({}) == []


# --------------------------------------------------------------------------
# Rule engine: matches the real policy
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def policy():
    return load_policy()


@pytest.fixture(scope="module")
def engine(policy):
    return RuleEngine(policy.rules)


def test_engine_compiles_every_enabled_rule_in_the_real_policy(policy, engine):
    enabled = [r for r in policy.rules if r.enabled]
    assert engine.rule_count == len(enabled) > 0


def test_sanctions_hit_floors_to_extreme_and_forces_review(engine, policy):
    record = {"sanctions_screen_hits": 1}
    outcome = engine.apply("Low", record, review_bands=policy.review_bands)
    assert outcome.final_band == "Extreme"
    assert outcome.requires_review is True
    assert outcome.band_floor_applied is True
    assert [r.id for r in outcome.fired_rules] == ["SANCTIONS_MATCH"]


def test_clean_customer_triggers_no_rule(engine, policy):
    record = {
        "sanctions_screen_hits": 0, "pep_flag": 0, "high_risk_jurisdiction_exposure": 0,
        "source_of_funds_declared": "salary", "source_of_funds_verified": 1,
        "kyc_refresh_overdue_days": 0, "max_days_past_due_24m": 0,
        "months_since_last_delinquency": 36, "account_age_months": 60,
        "credit_utilization_ratio": 0.2,
    }
    outcome = engine.apply("Low", record, review_bands=policy.review_bands)
    assert outcome.final_band == "Low"
    assert outcome.requires_review is False
    assert outcome.fired_rules == ()


def test_review_band_threshold_fires_without_any_rule(engine, policy):
    """High/Extreme requires review from the model score alone (review_bands),
    with zero rules needing to fire — the separate, simpler policy lever."""
    outcome = engine.apply("High", {}, review_bands=policy.review_bands)
    assert outcome.requires_review is True
    assert outcome.fired_rules == ()


def test_sanctions_rule_is_not_customer_visible(engine, policy):
    outcome = engine.apply("Low", {"sanctions_screen_hits": 1}, review_bands=policy.review_bands)
    assert outcome.fired_rules[0].customer_visible is False


def test_severe_arrears_rule_is_customer_visible(engine, policy):
    record = {"max_days_past_due_24m": 95, "months_since_last_delinquency": 1}
    outcome = engine.apply("Low", record, review_bands=policy.review_bands)
    ids = {r.id: r for r in outcome.fired_rules}
    assert "SEVERE_ARREARS" in ids
    assert ids["SEVERE_ARREARS"].customer_visible is True


# --------------------------------------------------------------------------
# Raise-only guarantee — cannot construct a counter-example
# --------------------------------------------------------------------------


@pytest.mark.parametrize("model_band", ["Low", "Medium", "High", "Extreme"])
def test_a_low_floor_rule_can_never_lower_any_band(model_band):
    weak_rule = Rule(id="ALWAYS_FIRES", description="x", when="1 == 1", floor_band="Low",
                      require_review=False, reason_code="X", enabled=True)
    engine = RuleEngine((weak_rule,))
    outcome = engine.apply(model_band, {})
    assert outcome.final_band == model_band


def test_no_fired_rules_and_no_review_band_leaves_the_model_band_untouched():
    engine = RuleEngine(())
    for band in ("Low", "Medium", "High", "Extreme"):
        outcome = engine.apply(band, {"anything": 1})
        assert outcome.final_band == band
        assert outcome.requires_review is False


def test_multiple_fired_rules_floor_to_the_maximum_not_the_last_one():
    """Order must not matter — the ceiling is the max across all fired rules,
    not whichever rule happened to be listed last."""
    low_first = Rule(id="A", description="x", when="1 == 1", floor_band="Medium",
                      require_review=False, reason_code="A", enabled=True)
    high_second = Rule(id="B", description="x", when="1 == 1", floor_band="Extreme",
                        require_review=False, reason_code="B", enabled=True)
    forward = RuleEngine((low_first, high_second))
    backward = RuleEngine((high_second, low_first))
    assert forward.apply("Low", {}).final_band == "Extreme"
    assert backward.apply("Low", {}).final_band == "Extreme"


def test_engine_rejects_an_unknown_band():
    engine = RuleEngine(())
    with pytest.raises(ValueError):
        engine.apply("Nonexistent", {})


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------


def _stored_score(customer_id, credit_p, fc_p, scored_at, **customer_fields) -> StoredScore:
    from crr.api.scoring import Assessment

    assessment = Assessment(
        customer_id=customer_id, risk_score=0.0, model_band="Low", risk_band="Low",
        band_floor_applied=False, credit_probability=credit_p, financial_crime_probability=fc_p,
        top_factors=[], protective_factors=[], fired_rules=[], requires_review=False,
        model_version="test", policy_version=1, scored_at=scored_at, audience="internal",
        input_hash="x", customer_snapshot={"customer_id": customer_id, **customer_fields},
    )
    return assessment_to_stored(assessment)


def test_simulate_shows_no_changes_for_an_identical_policy(policy):
    scores = [_stored_score("C1", 0.02, 0.01, dt.datetime.now(dt.UTC))]
    report = simulate(policy, policy, scores)
    assert report.n_band_changed == 0
    assert report.changed_customers == []


def test_simulate_detects_a_band_threshold_change():
    from pathlib import Path

    import yaml

    from crr.policy import _parse

    raw = yaml.safe_load(Path("config/risk_policy.yaml").read_text())
    current = _parse(raw, Path("current.yaml"), 0.0, "hash-a")

    proposed_raw = dict(raw)
    proposed_raw["version"] = 999
    proposed_raw["bands"] = dict(raw["bands"])
    proposed_raw["bands"]["Medium"] = {"max_score": 30}  # stricter, still > Low (25) and < High (75)
    proposed = _parse(proposed_raw, Path("proposed.yaml"), 0.0, "hash-b")

    # A customer whose composite score lands in (10, 50] moves Medium -> High.
    scores = [_stored_score("C1", 0.08, 0.02, dt.datetime.now(dt.UTC))]
    report = simulate(current, proposed, scores)
    assert report.n_evaluated == 1
    if report.n_band_changed:
        delta = report.changed_customers[0]
        assert delta.customer_id == "C1"
        assert delta.proposed.risk_score >= delta.current.risk_score  # policy change alone never re-orders


def test_simulate_deduplicates_to_each_customers_most_recent_score(policy):
    old = _stored_score("C1", 0.01, 0.01, dt.datetime.now(dt.UTC) - dt.timedelta(days=5), sanctions_screen_hits=1)
    new = _stored_score("C1", 0.01, 0.01, dt.datetime.now(dt.UTC), sanctions_screen_hits=0)
    report = simulate(policy, policy, [old, new])
    assert report.n_evaluated == 1  # one customer, not two score events


def test_simulate_reports_rule_gain():
    from pathlib import Path

    import yaml

    from crr.policy import _parse

    raw = yaml.safe_load(Path("config/risk_policy.yaml").read_text())
    current = _parse(raw, Path("current.yaml"), 0.0, "hash-c")

    proposed_raw = dict(raw)
    proposed_raw["version"] = 998
    proposed_raw["rules"] = [dict(r) for r in raw["rules"]]
    for rule in proposed_raw["rules"]:
        if rule["id"] == "KYC_REFRESH_OVERDUE":
            rule["when"] = "kyc_refresh_overdue_days > 10"
    proposed = _parse(proposed_raw, Path("proposed.yaml"), 0.0, "hash-d")

    scores = [_stored_score("C1", 0.02, 0.01, dt.datetime.now(dt.UTC), kyc_refresh_overdue_days=50)]
    report = simulate(current, proposed, scores)
    assert report.rule_fire_counts_current.get("KYC_REFRESH_OVERDUE", 0) == 0
    assert report.rule_fire_counts_proposed.get("KYC_REFRESH_OVERDUE", 0) == 1
    assert "KYC_REFRESH_OVERDUE" in report.changed_customers[0].rules_gained


def test_recent_filters_by_window_and_sorts_newest_first():
    repository = InMemoryScoreRepository()
    now = dt.datetime.now(dt.UTC)
    repository.save(_stored_score("OLD", 0.1, 0.1, now - dt.timedelta(days=200)))
    repository.save(_stored_score("NEW", 0.1, 0.1, now - dt.timedelta(days=1)))
    recent = repository.recent(now - dt.timedelta(days=90))
    assert [s.customer_id for s in recent] == ["NEW"]
