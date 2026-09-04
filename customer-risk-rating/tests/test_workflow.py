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

from crr.reporting import build_report_from_case, to_xml
from crr.screening.models import WatchlistRecord
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
# Watchlist entries (sanctions/PEP/adverse-media screening data)
# --------------------------------------------------------------------------


def test_seed_demo_watchlist_is_idempotent(store, db_url):
    store.seed_demo_watchlist()
    first = store.list_watchlist_entries()
    store.seed_demo_watchlist()
    assert reopen(db_url).list_watchlist_entries() == first


def test_seed_demo_watchlist_never_overwrites_real_data(store):
    store.replace_watchlist_source("ofac", [
        WatchlistRecord(source="ofac", source_id="REAL-1", name="Real Name", category="sanctions"),
    ])
    store.seed_demo_watchlist()  # ofac already has rows — must not touch it
    ofac_entries = [e for e in store.list_watchlist_entries() if e["list_source"] == "ofac"]
    assert [e["name"] for e in ofac_entries] == ["Real Name"]


def test_seed_demo_watchlist_still_seeds_untouched_sources_after_an_early_refresh(store):
    """A real ops workflow can run scripts/refresh_watchlists.py for ofac/un
    before anyone has ever opened the app (which is what normally triggers
    the seed). pep/adverse_media have no real source to refresh from at all
    — a whole-table "already has data" check would leave them permanently
    empty in that ordering; seeding must be evaluated per source instead."""
    store.replace_watchlist_source("ofac", [
        WatchlistRecord(source="ofac", source_id="REAL-1", name="Real Name", category="sanctions"),
    ])
    store.seed_demo_watchlist()
    entries = store.list_watchlist_entries()
    assert [e["name"] for e in entries if e["list_source"] == "ofac"] == ["Real Name"]
    assert any(e["list_source"] == "pep" for e in entries), "pep must still get its demo seed"
    assert any(e["list_source"] == "adverse_media" for e in entries), "adverse_media must still get its demo seed"


def test_replace_watchlist_source_evicts_only_that_source(store):
    store.seed_demo_watchlist()
    before_eu = [e for e in store.list_watchlist_entries() if e["list_source"] == "eu"]
    assert before_eu  # sanity: the demo seed has an EU entry

    n = store.replace_watchlist_source("ofac", [
        WatchlistRecord(source="ofac", source_id="R1", name="Fresh One", category="sanctions",
                        aliases=("Alias One",), dates_of_birth=("1990-01-01",), countries=("IL",),
                        program="SDGT", remarks="real data"),
        WatchlistRecord(source="ofac", source_id="R2", name="Fresh Two", category="sanctions"),
    ])
    assert n == 2

    entries = store.list_watchlist_entries()
    ofac_names = {e["name"] for e in entries if e["list_source"] == "ofac"}
    assert ofac_names == {"Fresh One", "Fresh Two"}, "the old demo OFAC entry must be gone"
    after_eu = [e for e in entries if e["list_source"] == "eu"]
    assert after_eu == before_eu, "refreshing ofac must not touch eu's rows at all"


def test_replace_watchlist_source_on_an_empty_table_just_inserts(store):
    n = store.replace_watchlist_source("un", [
        WatchlistRecord(source="un", source_id="U1", name="Someone", category="sanctions"),
    ])
    assert n == 1
    assert len(store.list_watchlist_entries()) == 1


def test_list_watchlist_entries_dict_shape_matches_what_the_matcher_and_ui_expect(store):
    store.replace_watchlist_source("ofac", [
        WatchlistRecord(source="ofac", source_id="R1", name="Jane Doe", category="sanctions",
                        aliases=("J. Doe",), dates_of_birth=("1980-05-01", "1980-06-01"),
                        countries=("IL", "CY"), program="SDGT", remarks="test"),
    ])
    entry = store.list_watchlist_entries()[0]
    assert entry["id"] == "ofac:R1"
    assert entry["name"] == "Jane Doe"
    assert entry["aliases"] == ["J. Doe"]
    assert entry["dates_of_birth"] == ["1980-05-01", "1980-06-01"]
    assert entry["countries"] == ["IL", "CY"]
    assert entry["dob"] == "1980-05-01"  # first of several, for display
    assert entry["country"] == "IL"
    assert entry["list_source"] == "ofac"
    assert entry["category"] == "sanctions"
    assert entry["reason"] == "test"


def test_watchlist_source_status_counts_and_freshness(store, db_url):
    store.seed_demo_watchlist()
    status_before = {row["source"]: row for row in store.watchlist_source_status()}
    assert status_before["ofac"]["count"] == 1
    assert status_before["ofac"]["last_refreshed"] is not None

    store.replace_watchlist_source("ofac", [
        WatchlistRecord(source="ofac", source_id="R1", name="A", category="sanctions"),
        WatchlistRecord(source="ofac", source_id="R2", name="B", category="sanctions"),
    ])
    status_after = {row["source"]: row for row in reopen(db_url).watchlist_source_status()}
    assert status_after["ofac"]["count"] == 2
    assert status_after["ofac"]["last_refreshed"] >= status_before["ofac"]["last_refreshed"]
    # other sources are untouched by the ofac refresh
    assert status_after["eu"]["count"] == status_before["eu"]["count"]


def test_watchlist_source_status_omits_sources_with_no_rows(store):
    store.replace_watchlist_source("un", [
        WatchlistRecord(source="un", source_id="U1", name="Someone", category="sanctions"),
    ])
    sources_present = {row["source"] for row in store.watchlist_source_status()}
    assert sources_present == {"un"}


# --------------------------------------------------------------------------
# Filed reports (suspicious transaction/activity reports — crr.reporting)
# --------------------------------------------------------------------------


def test_create_and_get_report_round_trips(store, db_url):
    store.upsert_case("CUS-5", {}, {}, {"risk_score": 90, "risk_band": "Extreme"}, actor="officer")
    store.create_report(
        "CRR-CUS-5-1", "CUS-5", report_code="STR", reason="unusual cash activity",
        indicators=["STRUCTURING", "ADVERSE_MEDIA"], xml_content="<report/>",
        filed_by="Noa", filed_by_role="compliance_admin",
    )
    got = reopen(db_url).get_report("CRR-CUS-5-1")
    assert got["customer_id"] == "CUS-5"
    assert got["report_code"] == "STR"
    assert got["reason"] == "unusual cash activity"
    assert got["indicators"] == ["STRUCTURING", "ADVERSE_MEDIA"]
    assert got["xml_content"] == "<report/>"
    assert got["filed_by"] == "Noa"
    assert got["filed_by_role"] == "compliance_admin"
    assert got["filed_at"] is not None


def test_get_report_returns_none_for_an_unknown_id(store):
    assert store.get_report("does-not-exist") is None


def test_list_reports_filters_by_customer(store):
    store.create_report("R1", "CUS-A", report_code="STR", reason="r", indicators=[], xml_content="<x/>",
                        filed_by="A", filed_by_role="compliance_admin")
    store.create_report("R2", "CUS-B", report_code="SAR", reason="r", indicators=[], xml_content="<x/>",
                        filed_by="A", filed_by_role="compliance_admin")
    assert [r["id"] for r in store.list_reports("CUS-A")] == ["R1"]
    assert [r["id"] for r in store.list_reports("CUS-B")] == ["R2"]
    assert {r["id"] for r in store.list_reports()} == {"R1", "R2"}


def test_list_reports_newest_first(store):
    store.create_report("R1", "CUS-A", report_code="STR", reason="r", indicators=[], xml_content="<x/>",
                        filed_by="A", filed_by_role="compliance_admin")
    store.create_report("R2", "CUS-A", report_code="STR", reason="r", indicators=[], xml_content="<x/>",
                        filed_by="A", filed_by_role="compliance_admin")
    ids = [r["id"] for r in store.list_reports("CUS-A")]
    assert ids == ["R2", "R1"]


def test_list_reports_for_a_customer_with_none_filed_is_empty(store):
    assert store.list_reports("CUS-NOTHING-FILED") == []


def test_filing_a_report_end_to_end_through_the_real_builder_and_serializer(store):
    """The same path app.py's render_report_filing_panel takes: build a
    SarReport from a case dict, serialize it, persist it — proving the
    store, builder and serializer actually fit together, not just each in
    isolation."""
    entry = {
        "customer_id": "CUS-9",
        "profile": {"full_name": "Mikhail Aslanov", "date_of_birth": "1975-11-02", "country_of_residence": "CY"},
        "timeline": [{"kind": "event", "at": dt.datetime(2026, 1, 1), "event_type": "wire_transfer_out",
                      "amount": 5000, "reason": "trigger", "rescored": True, "band_changed": True}],
    }
    hits = [{"name": "Mikhail Aslanov", "list_source": "ofac", "match_score": 95.0, "reason": "SDN"}]
    report = build_report_from_case(entry, reason="Screening hit under review.", indicators=["SANCTIONS_OR_PEP_MATCH"],
                                    officer_name="Noa", officer_role="compliance_admin", watchlist_hits=hits)
    xml_content = to_xml(report)
    store.create_report(report.report_ref, entry["customer_id"], report_code=report.report_code,
                        reason=report.reason, indicators=list(report.indicators), xml_content=xml_content,
                        filed_by="Noa", filed_by_role="compliance_admin")

    stored = store.get_report(report.report_ref)
    assert stored["xml_content"] == xml_content
    assert "Mikhail Aslanov" in stored["xml_content"]
    assert "<report_code>STR</report_code>" in stored["xml_content"]


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
