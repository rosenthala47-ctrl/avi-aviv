"""Tests for the persisted workflow layer: auth, persistence, and the
append-only, tamper-evident audit chain.

Every test runs against a real file-backed SQLite database in a tmp dir (not
in-memory), because the whole point of this layer is that state survives a
process restart — several tests reopen the database through a fresh store to
prove exactly that."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from crr.workflow import WorkflowStore, create_session_factory
from crr.workflow.auth import (
    extract_bearer_token,
    hash_password,
    hash_token,
    needs_rehash,
    verify_password,
)
from crr.workflow.store import GENESIS_HASH


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{tmp_path / 'workflow.db'}"


@pytest.fixture
def store(db_url):
    s = WorkflowStore(create_session_factory(db_url))
    s.seed_default_users()
    return s


def reopen(db_url) -> WorkflowStore:
    """A fresh store over the same database — simulates a process restart."""
    return WorkflowStore(create_session_factory(db_url))


# --------------------------------------------------------------------------
# Password hashing
# --------------------------------------------------------------------------


def test_password_round_trips_and_rejects_wrong():
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong", h)


def test_password_hash_is_salted_and_never_plaintext():
    a, b = hash_password("same"), hash_password("same")
    assert a != b, "a random per-user salt must make identical passwords hash differently"
    assert "same" not in a and a.startswith("pbkdf2_sha256$")


def test_empty_password_is_rejected():
    with pytest.raises(ValueError):
        hash_password("")


def test_verify_never_raises_on_a_corrupt_stored_hash():
    for junk in ["", "not-a-hash", "pbkdf2_sha256$notanint$aa$bb", "a$b$c"]:
        assert verify_password("x", junk) is False


def test_needs_rehash_flags_weaker_or_malformed():
    assert needs_rehash("garbage")
    assert needs_rehash(hash_password("x", iterations=1000))  # below current policy
    assert not needs_rehash(hash_password("x"))


def test_only_the_token_hash_is_stored(store, db_url):
    user = store.authenticate("officer", "officer123")
    token = store.create_login_session(user["id"])
    with create_session_factory(db_url)() as s:
        stored = s.execute(text("SELECT token_hash FROM wf_sessions")).scalar_one()
    assert stored == hash_token(token)
    assert token not in stored, "the raw bearer token must never be persisted"


# --------------------------------------------------------------------------
# Bearer-token extraction — header > cookie > query param, framework-agnostic
# --------------------------------------------------------------------------


def test_extract_prefers_header_over_cookie_and_query():
    assert extract_bearer_token("h", "c", "q") == "h"


def test_extract_falls_back_to_cookie_when_header_absent():
    assert extract_bearer_token(None, "c", "q") == "c"


def test_extract_falls_back_to_query_when_header_and_cookie_absent():
    assert extract_bearer_token(None, None, "q") == "q"


def test_extract_returns_none_when_all_absent():
    assert extract_bearer_token(None, None, None) is None


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_extract_treats_blank_values_as_absent(blank):
    assert extract_bearer_token(blank, "c", "q") == "c"
    assert extract_bearer_token(blank, blank, "q") == "q"
    assert extract_bearer_token(blank, blank, blank) is None


def test_extract_strips_surrounding_whitespace():
    assert extract_bearer_token("  h  ", None, None) == "h"


# --------------------------------------------------------------------------
# Authentication & sessions — role comes from the record, not a UI toggle
# --------------------------------------------------------------------------


def test_seed_users_is_idempotent(store):
    store.seed_default_users()
    store.seed_default_users()
    assert sorted(u["username"] for u in store.list_users()) == ["analyst", "manager", "officer"]


@pytest.mark.parametrize(
    "username,role",
    [("analyst", "junior_analyst"), ("manager", "risk_manager"), ("officer", "compliance_admin")],
)
def test_each_seeded_account_authenticates_to_its_role(store, username, role):
    user = store.authenticate(username, f"{username}123")
    assert user is not None and user["role"] == role


def test_authenticate_rejects_bad_password_and_unknown_user(store):
    assert store.authenticate("officer", "nope") is None
    assert store.authenticate("ghost", "whatever") is None


def test_session_resolves_then_expires(store, db_url, monkeypatch):
    user = store.authenticate("manager", "manager123")
    token = store.create_login_session(user["id"])
    assert store.resolve_session(token)["username"] == "manager"
    assert store.resolve_session("bogus") is None

    # Force the stored session to be already expired, then confirm it is rejected.
    with create_session_factory(db_url)() as s:
        s.execute(text("UPDATE wf_sessions SET expires_at = :past"),
                  {"past": dt.datetime(2000, 1, 1)})
        s.commit()
    assert store.resolve_session(token) is None


def test_logout_invalidates_the_session(store):
    user = store.authenticate("analyst", "analyst123")
    token = store.create_login_session(user["id"])
    assert store.resolve_session(token) is not None
    store.end_session(token)
    assert store.resolve_session(token) is None


def test_create_user_and_login_survives_reopen(store, db_url):
    store.create_user("carol", "Carol", "risk_manager", "carolpw1")
    with pytest.raises(ValueError):
        store.create_user("carol", "Dup", "junior_analyst", "x")
    reopened = reopen(db_url)
    assert reopened.authenticate("carol", "carolpw1")["role"] == "risk_manager"


# --------------------------------------------------------------------------
# Cases, rules, dispositions — persistence across a restart
# --------------------------------------------------------------------------


def test_case_round_trips_with_timeline_and_survives_reopen(store, db_url):
    store.upsert_case(
        "CUS-1", {"segment": "retail", "full_name": "Alice B"}, {"underwriter_note": "n"},
        {"risk_score": 42.0, "risk_band": "Medium"}, archetype="demo", actor="officer",
    )
    store.set_status("CUS-1", "escalated_aml")
    store.add_timeline("CUS-1", "decision", "officer", action="escalated_aml", note="opaque funds")

    q = reopen(db_url).load_queue()
    assert "CUS-1" in q
    entry = q["CUS-1"]
    assert entry["status"] == "escalated_aml"
    assert entry["profile"]["full_name"] == "Alice B"  # UI-only identity persisted
    kinds = [t["kind"] for t in entry["timeline"]]
    assert kinds == ["scored", "decision"], "timeline reassembled in chronological order"


def test_rescore_preserves_a_decided_status(store):
    store.upsert_case("CUS-2", {}, {}, {"risk_score": 10, "risk_band": "Low"}, actor="officer")
    store.set_status("CUS-2", "approved")
    store.upsert_case("CUS-2", {}, {}, {"risk_score": 90, "risk_band": "Extreme"}, actor="system")
    assert store.load_queue()["CUS-2"]["status"] == "approved", "a re-score must not un-decide a case"


def test_rules_crud_and_toggle_source_of_truth(store, db_url):
    store.add_rule({"id": "r1", "name": "R1", "enabled": True, "conditions": []}, actor="officer")
    store.set_rule_enabled("r1", False)
    rules = reopen(db_url).list_rules()
    assert len(rules) == 1 and rules[0]["enabled"] is False
    store.delete_rule("r1")
    assert store.list_rules() == []


def test_disposition_is_terminal_one_per_hit(store):
    store.upsert_case("CUS-3", {}, {}, {"risk_score": 50, "risk_band": "High"}, actor="officer")
    store.add_disposition("CUS-3", "OFAC-1", "true_positive", "confirmed", "officer", "compliance_admin",
                          hit_name="X", list_source="ofac")
    disp = store.load_queue()["CUS-3"]["watchlist_dispositions"]
    assert disp["OFAC-1"]["disposition"] == "true_positive"
    with pytest.raises(IntegrityError):  # unique(customer_id, hit_id) — no second ruling on the same hit
        store.add_disposition("CUS-3", "OFAC-1", "false_positive", "x", "officer", "compliance_admin")


# --------------------------------------------------------------------------
# Immutable, tamper-evident audit log
# --------------------------------------------------------------------------


def test_audit_appends_and_lists_in_order(store):
    for i in range(4):
        store.append_audit("case_decision", "officer", "compliance_admin", "CUS-1", {"note": f"n{i}"})
    log = store.list_audit()
    assert [e["detail"]["note"] for e in log] == ["n0", "n1", "n2", "n3"]


def test_audit_chain_verifies_and_survives_reopen(store, db_url):
    for i in range(6):
        store.append_audit("note_added", "analyst", "junior_analyst", "CUS-1", {"note": f"n{i}"})
    ok, broken = store.verify_audit_chain()
    assert ok and broken is None
    ok2, broken2 = reopen(db_url).verify_audit_chain()
    assert ok2 and broken2 is None, "the chain must still verify from a cold reopen"


def test_first_entry_links_to_genesis(store, db_url):
    store.append_audit("login", "officer", "compliance_admin", None, {})
    with create_session_factory(db_url)() as s:
        prev = s.execute(text("SELECT prev_hash FROM wf_audit_log ORDER BY id LIMIT 1")).scalar_one()
    assert prev == GENESIS_HASH


def test_tampering_with_a_row_breaks_the_chain(store, db_url):
    for i in range(5):
        store.append_audit("case_decision", "officer", "compliance_admin", "CUS-1", {"note": f"n{i}"})
    with create_session_factory(db_url)() as s:
        s.execute(text("UPDATE wf_audit_log SET detail = :d WHERE id = 3"),
                  {"d": '{"note": "TAMPERED"}'})
        s.commit()
    ok, broken = store.verify_audit_chain()
    assert not ok and broken == 3


def test_deleting_a_row_breaks_the_chain(store, db_url):
    for i in range(5):
        store.append_audit("case_decision", "officer", "compliance_admin", "CUS-1", {"note": f"n{i}"})
    with create_session_factory(db_url)() as s:
        s.execute(text("DELETE FROM wf_audit_log WHERE id = 3"))
        s.commit()
    ok, broken = store.verify_audit_chain()
    assert not ok and broken == 4, "the row after the gap no longer links to its recorded predecessor"


def test_store_exposes_no_audit_mutation_methods():
    # Immutability is structural: the facade offers append/list/verify and
    # nothing that could edit or remove a past entry.
    forbidden = [m for m in dir(WorkflowStore)
                 if any(w in m.lower() for w in ("update_audit", "delete_audit", "edit_audit"))]
    assert forbidden == []
