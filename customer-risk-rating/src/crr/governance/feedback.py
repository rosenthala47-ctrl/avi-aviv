"""Human-model disagreement, and a direct measurement of the bias the roadmap
warns about: "the generator deliberately models humans as biased estimators
... naively training on human decisions reproduces the bias. Train on
outcomes; use decisions only to measure human-model disagreement."

``crr.data.synthetic._draw_underwriter_decisions`` gives the humans in this
dataset two biases with **no legitimate causal role in either outcome
model** (neither ``BETA_CREDIT`` nor ``BETA_CRIME`` contains a
private-banking or corporate term at all): 9 points of leniency toward
private-banking relationships, 4 toward corporate, and 11 points of harshness
for jurisdiction exposure. That makes ``default_12m`` the clean
demonstration — training a classifier on the human decision instead of the
true outcome has no legitimate reason to pick up either effect, so any
segment sensitivity the decision-trained model shows beyond the
outcome-trained model's is the reproduced bias, not a confound.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from crr.models.baseline import train_booster

#: Ordinal mapping from a human decision to the policy band it corresponds
#: to, for a like-for-like disagreement comparison against the model's own
#: assigned band.
DECISION_TO_BAND: dict[str, str] = {
    "approve": "Low",
    "approve_with_conditions": "Medium",
    "refer": "High",
    "decline": "Extreme",
}


def human_model_disagreement(decision: pd.Series, band: pd.Series) -> dict[str, Any]:
    """How often does the model's assigned band match what the human decision
    implies? Not a correctness check on either side — the humans in this
    dataset are deliberately biased and the model does not see their
    decision — this is a monitoring signal: a rising disagreement rate over
    time is exactly what a real feedback loop would want to catch."""
    implied_band = decision.map(DECISION_TO_BAND)
    agree = (implied_band == band).to_numpy()
    crosstab = pd.crosstab(decision, band, normalize="index").round(4)
    return {
        "n": int(len(decision)),
        "agreement_rate": round(float(agree.mean()), 4),
        "disagreement_rate": round(float(1 - agree.mean()), 4),
        "decision_vs_band": crosstab.to_dict(orient="index"),
    }


def decision_label(decision: pd.Series) -> np.ndarray:
    """The label a naive "learn what underwriters do" model would be trained
    on: 1 if the human referred or declined, 0 if they approved (with or
    without conditions)."""
    return decision.isin(["refer", "decline"]).to_numpy(dtype=int)


def bias_reproduction_report(
    X: pd.DataFrame,
    masks: dict[str, np.ndarray],
    categorical: list[str],
    y_decision: np.ndarray,
    outcome_trained_scores_test: np.ndarray,
    segment_test: pd.Series,
    seed: int = 42,
) -> pd.DataFrame:
    """Train a second booster on the human *decision* instead of the true
    outcome, on the exact same split, and compare its private-banking /
    corporate sensitivity against the true-outcome-trained model's. Both
    models see identical features; the only difference is the label. Any
    gap in how much a segment's average score differs from everyone else's
    is the label choice reproducing (or not) the human bias — a direct,
    measured version of the roadmap's warning, not a description of it.
    """
    decision_booster = train_booster(
        X[masks["train"]], y_decision[masks["train"]],
        X[masks["validation"]], y_decision[masks["validation"]],
        categorical, seed=seed,
    )
    decision_scores_test = np.asarray(decision_booster.predict(X[masks["test"]]), dtype=float)

    frame = pd.DataFrame({
        "segment": segment_test.to_numpy(),
        "outcome_trained_score": outcome_trained_scores_test,
        "decision_trained_score": decision_scores_test,
    })

    rows = []
    for segment_name in sorted(frame["segment"].unique()):
        in_segment = frame[frame["segment"] == segment_name]
        rest = frame[frame["segment"] != segment_name]
        if len(in_segment) < 30 or len(rest) < 30:
            continue
        rows.append({
            "segment": segment_name,
            "n": int(len(in_segment)),
            "outcome_trained_gap": round(float(in_segment["outcome_trained_score"].mean() - rest["outcome_trained_score"].mean()), 4),
            "decision_trained_gap": round(float(in_segment["decision_trained_score"].mean() - rest["decision_trained_score"].mean()), 4),
        })
    report = pd.DataFrame(rows)
    if len(report):
        report["reproduced_bias"] = (report["decision_trained_gap"] - report["outcome_trained_gap"]).round(4)
    return report
