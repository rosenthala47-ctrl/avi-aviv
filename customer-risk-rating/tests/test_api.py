"""Tests for the serving API.

Covers the three endpoints, the input contract (missing vs malformed), idempotency,
the audit record's reproducibility guarantee, and the SQLAlchemy persistence path
against SQLite. The model bundle is loaded once for the module — it is the slow
part — and shared across an in-memory app so the tests stay fast.
"""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from crr.api.app import create_app
from crr.api.repository import (
    InMemoryJobRepository,
    InMemoryScoreRepository,
    SqlAlchemyScoreRepository,
    StoredScore,
    create_session_factory,
)
from crr.api.scoring import ModelBundle, ScoringService
from crr.api.settings import Settings
from crr.data.synthetic import GeneratorConfig, generate

REPO_ROOT = pytest.importorskip("pathlib").Path(__file__).resolve().parents[1]
PII = ["full_name", "national_id", "email", "phone", "address_line", "split"]


def _needs_models() -> bool:
    return not (REPO_ROOT / "models" / "default_12m" / "model.txt").exists()


pytestmark = pytest.mark.skipif(
    _needs_models(),
    reason="trained models not present; run scripts/train_baseline.py for both targets",
)


@pytest.fixture(scope="module")
def bundle():
    return ModelBundle.load()


@pytest.fixture(scope="module")
def customers():
    # A fresh synthetic sample so the tests do not depend on data/raw being present.
    dataset = generate(GeneratorConfig(n_customers=60, seed=7))
    rows = []
    for _, row in dataset.customers.iterrows():
        record = row.to_dict()
        for key in PII:
            record.pop(key, None)
        rows.append({k: (None if pd.isna(v) else v) for k, v in record.items()})
    return rows


@pytest.fixture
def client(bundle):
    scores = InMemoryScoreRepository()
    jobs = InMemoryJobRepository()
    app = create_app(
        settings=Settings(),
        service=ScoringService(bundle),
        scores=scores,
        jobs=jobs,
    )
    with TestClient(app) as test_client:
        test_client.scores = scores  # type: ignore[attr-defined]
        yield test_client


def _to_json(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if v is not None} | {
        "customer_id": payload["customer_id"],
        "snapshot_date": str(payload["snapshot_date"])[:10],
    }


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------


def test_health_reports_loaded_models(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["models_loaded"] == ["credit", "financial_crime"]
    assert body["policy_version"] >= 1


# --------------------------------------------------------------------------
# Score
# --------------------------------------------------------------------------


def test_score_returns_a_banded_rating(client, customers):
    response = client.post("/api/v1/score", json={"customer": _to_json(customers[0])})
    assert response.status_code == 200
    result = response.json()["result"]
    assert 0 <= result["risk_score"] <= 100
    assert result["risk_band"] in ("Low", "Medium", "High", "Extreme")
    assert 0 <= result["credit"]["probability"] <= 1
    assert 0 <= result["financial_crime"]["probability"] <= 1
    assert result["model_version"]
    assert result["latency_ms"] >= 0


def test_score_includes_reason_factors_when_explaining(client, customers):
    response = client.post("/api/v1/score", json={"customer": _to_json(customers[0]), "explain": True})
    factors = response.json()["result"]["top_factors"]
    assert factors
    for factor in factors:
        assert factor["code"]
        assert factor["dimension"] in ("credit", "financial_crime")
        assert factor["direction"] in ("increases", "decreases")


def test_score_without_explanation_omits_factors(client, customers):
    response = client.post("/api/v1/score", json={"customer": _to_json(customers[0]), "explain": False})
    assert response.status_code == 200
    assert response.json()["result"]["top_factors"] == []


def test_missing_optional_fields_are_accepted_not_rejected(client):
    """Partial data is honest missingness, not a 422 — the model handles NaN."""
    minimal = {"customer_id": "CUS-MIN", "snapshot_date": "2026-01-01"}
    response = client.post("/api/v1/score", json={"customer": minimal})
    assert response.status_code == 200
    # A near-empty customer scores near the base rate, not a confident extreme.
    assert response.json()["result"]["risk_score"] < 60


def test_malformed_value_is_rejected(client):
    bad = {"customer_id": "X", "snapshot_date": "2026-01-01", "credit_utilization_ratio": -3}
    response = client.post("/api/v1/score", json={"customer": bad})
    assert response.status_code == 422


def test_unknown_field_is_rejected(client):
    bad = {"customer_id": "X", "snapshot_date": "2026-01-01", "totally_made_up": 1}
    response = client.post("/api/v1/score", json={"customer": bad})
    assert response.status_code == 422


def test_customer_audience_suppresses_compliance_factors(client, customers):
    """Find a customer whose internal explanation surfaces a suppressed code,
    then confirm the customer audience never shows it."""
    from crr.explain.reason_codes import BY_CODE

    for customer in customers:
        internal = client.post(
            "/api/v1/score", json={"customer": _to_json(customer), "audience": "internal"}
        ).json()["result"]
        internal_codes = {f["code"] for f in internal["top_factors"]}
        if any(not BY_CODE[c].customer_visible for c in internal_codes):
            visible = client.post(
                "/api/v1/score", json={"customer": _to_json(customer), "audience": "customer"}
            ).json()["result"]
            assert all(BY_CODE[f["code"]].customer_visible for f in visible["top_factors"])
            return
    pytest.skip("no suppressed code surfaced in the sample")


# --------------------------------------------------------------------------
# Idempotency and audit
# --------------------------------------------------------------------------


def test_identical_requests_store_one_score(client, customers):
    payload = {"customer": _to_json(customers[1])}
    client.post("/api/v1/score", json=payload)
    client.post("/api/v1/score", json=payload)
    history = client.scores.history(customers[1]["customer_id"])
    assert len(history) == 1


def test_different_inputs_store_separate_scores(client, customers):
    a = {"customer": _to_json(customers[2])}
    changed = dict(_to_json(customers[2]))
    changed["credit_utilization_ratio"] = 0.99
    client.post("/api/v1/score", json=a)
    client.post("/api/v1/score", json={"customer": changed})
    assert len(client.scores.history(customers[2]["customer_id"])) == 2


def test_stored_score_carries_reproducibility_fields(client, customers):
    client.post("/api/v1/score", json={"customer": _to_json(customers[3])})
    stored = client.scores.latest(customers[3]["customer_id"])
    assert stored.model_version
    assert stored.policy_version >= 1
    assert len(stored.input_hash) == 16


# --------------------------------------------------------------------------
# Explain
# --------------------------------------------------------------------------


def test_explain_reads_the_stored_score(client, customers):
    cid = customers[4]["customer_id"]
    client.post("/api/v1/score", json={"customer": _to_json(customers[4])})
    response = client.get(f"/api/v1/explain/{cid}")
    assert response.status_code == 200
    body = response.json()
    assert body["customer_id"] == cid
    assert body["top_factors"] or body["protective_factors"]


def test_explain_404_for_unknown_customer(client):
    assert client.get("/api/v1/explain/NOPE").status_code == 404


def test_explain_customer_audience_hides_suppressed_factors(client, customers):
    from crr.explain.reason_codes import BY_CODE

    for customer in customers:
        cid = customer["customer_id"]
        client.post("/api/v1/score", json={"customer": _to_json(customer), "audience": "internal"})
        internal = client.get(f"/api/v1/explain/{cid}?audience=internal").json()
        if any(not BY_CODE[f["code"]].customer_visible for f in internal["top_factors"]):
            customer_view = client.get(f"/api/v1/explain/{cid}?audience=customer").json()
            assert all(BY_CODE[f["code"]].customer_visible for f in customer_view["top_factors"])
            return
    pytest.skip("no suppressed code surfaced")


# --------------------------------------------------------------------------
# Batch
# --------------------------------------------------------------------------


def test_batch_scores_every_customer(client, customers):
    payload = {"customers": [_to_json(c) for c in customers[:15]]}
    submit = client.post("/api/v1/batch-score", json=payload)
    assert submit.status_code == 202
    job_id = submit.json()["job"]["job_id"]

    # TestClient runs background tasks synchronously before returning, so the job
    # is already complete here.
    status = client.get(f"/api/v1/batch-score/{job_id}").json()["job"]
    assert status["status"] == "completed"
    assert status["processed"] == 15
    assert len(client.scores.history(customers[0]["customer_id"])) >= 1


def test_batch_status_404_for_unknown_job(client):
    assert client.get("/api/v1/batch-score/does-not-exist").status_code == 404


def test_empty_batch_is_rejected(client):
    assert client.post("/api/v1/batch-score", json={"customers": []}).status_code == 422


# --------------------------------------------------------------------------
# SQLAlchemy persistence (SQLite exercises the production ORM path)
# --------------------------------------------------------------------------


def test_sqlalchemy_repository_round_trips_a_score():
    import datetime as dt

    repo = SqlAlchemyScoreRepository(create_session_factory("sqlite://"))
    score = StoredScore(
        customer_id="CUS-DB", scored_at=dt.datetime.now(dt.UTC), risk_score=61.0, risk_band="High",
        credit_probability=0.2, financial_crime_probability=0.05, requires_review=True,
        model_version="v1", policy_version=1, input_hash="deadbeefdeadbeef", audience="internal",
        explanation={"top_factors": [{"code": "CR01"}]},
    )
    repo.save(score)
    latest = repo.latest("CUS-DB")
    assert latest is not None
    assert latest.risk_band == "High"
    assert latest.explanation["top_factors"][0]["code"] == "CR01"
    assert repo.find_by_input_hash("CUS-DB", "deadbeefdeadbeef") is not None


def test_full_api_over_sqlalchemy_backend(bundle, customers):
    """End-to-end with the SQLite-backed repository, not the in-memory one."""
    session_factory = create_session_factory("sqlite://")
    scores = SqlAlchemyScoreRepository(session_factory)
    from crr.api.repository import SqlAlchemyJobRepository

    app = create_app(
        settings=Settings(),
        service=ScoringService(bundle),
        scores=scores,
        jobs=SqlAlchemyJobRepository(session_factory),
    )
    with TestClient(app) as test_client:
        cid = customers[5]["customer_id"]
        assert test_client.post("/api/v1/score", json={"customer": _to_json(customers[5])}).status_code == 200
        explained = test_client.get(f"/api/v1/explain/{cid}")
        assert explained.status_code == 200
        assert explained.json()["customer_id"] == cid
