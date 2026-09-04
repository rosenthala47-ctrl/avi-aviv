"""WorkflowStore: the one object the app talks to for all persisted state.

It hides SQLAlchemy entirely — every method takes and returns plain Python
dicts/lists in the exact shape the Streamlit console already renders, so the UI
code that used to read ``st.session_state.queue`` keeps working unchanged once
those session copies are hydrated from here. No live ORM object ever escapes
this module (``expire_on_commit=False`` plus dict conversion), so there are no
DetachedInstanceError surprises across Streamlit's per-interaction reruns.

The audit log is append-only and tamper-evident: :meth:`append_audit` chains
each row to the previous by hash, and :meth:`verify_audit_chain` walks the
chain to prove nothing was altered or removed. There is deliberately no method
to update or delete an audit row — the immutability is structural.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from crr.screening.models import WatchlistRecord
from crr.workflow import auth
from crr.workflow.models import (
    AuditEntry,
    Case,
    CustomRule,
    FiledReport,
    TimelineEvent,
    User,
    UserSession,
    WatchlistDisposition,
    WatchlistEntry,
)

#: Session lifetime for a browser login token.
SESSION_TTL = dt.timedelta(hours=12)

#: First link of the audit hash chain — a fixed, well-known value so the very
#: first real entry has a defined predecessor to hash against.
GENESIS_HASH = "0" * 64

#: Seeded demo accounts, one per role. Passwords are intentionally simple and
#: shown on the login screen: this is a demo. A real deployment disables these
#: and provisions accounts through the admin user form (or an external IdP).
DEMO_USERS: tuple[dict[str, str], ...] = (
    {"username": "analyst", "display_name": "Dana (Junior Analyst)",
     "role": "junior_analyst", "password": "analyst123"},
    {"username": "manager", "display_name": "Roi (Risk Manager)",
     "role": "risk_manager", "password": "manager123"},
    {"username": "officer", "display_name": "Noa (Compliance Officer)",
     "role": "compliance_admin", "password": "officer123"},
)

#: Fictional demo watchlist entries — replaces the hardcoded WATCHLIST_ENTRIES
#: that used to live in app.py. Seeded once, only into an empty table (see
#: seed_demo_watchlist), so the screening feature has something to show
#: before anyone has run scripts/refresh_watchlists.py. The "ofac"/"eu"/"un"
#: rows are placeholders: replace_watchlist_source deletes every row for a
#: source before inserting the freshly parsed list, so the first real refresh
#: for that source evicts its demo stand-in automatically. "pep" and
#: "adverse_media" have no free, authoritative, machine-readable source to
#: refresh from (a real deployment integrates a paid vendor — World-Check,
#: Dow Jones — for those; see crr/screening/ingest.py's module docstring) and
#: so stay demo-only indefinitely.
_DEMO_WATCHLIST_SEED: tuple[WatchlistRecord, ...] = (
    WatchlistRecord(source="ofac", source_id="OFAC-2201", name="Mikhail Aslanov", category="sanctions",
                     aliases=("Michael Aslanov",), dates_of_birth=("1975-11-02",), countries=("CY",),
                     remarks="Specially Designated National — asset-freeze order (demo data)."),
    WatchlistRecord(source="eu", source_id="EU-3105", name="Khaled Marwan", category="sanctions",
                     aliases=("Khalid Marwan",), dates_of_birth=("1969-06-19",), countries=("AE",),
                     remarks="EU consolidated sanctions list — arms trafficking (demo data)."),
    WatchlistRecord(source="un", source_id="UN-1042", name="Elena Petrova", category="sanctions",
                     aliases=("Yelena Petrova",), dates_of_birth=("1982-01-30",), countries=("RU",),
                     remarks="UN Security Council sanctions list (demo data)."),
    WatchlistRecord(source="pep", source_id="PEP-0087", name="Rustam Aliyev", category="pep",
                     dates_of_birth=("1958-09-12",), countries=("TR",),
                     remarks="Former deputy minister of finance — politically exposed person (demo data)."),
    WatchlistRecord(source="pep", source_id="PEP-0142", name="Fatima Al-Sayed", category="pep",
                     dates_of_birth=("1971-04-05",), countries=("AE",),
                     remarks="Spouse of a serving head of state — politically exposed person (demo data)."),
    WatchlistRecord(source="adverse_media", source_id="AM-0509", name="Dmitri Volkov", category="adverse_media",
                     aliases=("Dmitry Volkov",), dates_of_birth=("1966-12-24",), countries=("CY",),
                     remarks="Named in investigative reporting on offshore shell structures (demo data)."),
)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _norm_at(value: Any) -> str:
    """A dialect-stable string form of a timestamp for the audit hash.

    Normalises to UTC and drops the tzinfo before formatting, so a value stored
    tz-aware on PostgreSQL and read back naive-UTC on SQLite hashes identically
    — the chain must verify the same on whatever database it lands in."""
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(dt.UTC).replace(tzinfo=None)
        return value.isoformat(timespec="microseconds")
    return str(value)


def _audit_material(actor: str, role: str, action: str, customer_id: str | None,
                    detail: dict[str, Any], at: dt.datetime) -> str:
    """Canonical, order-independent serialisation of an audit row's content."""
    return json.dumps(
        {
            "at": _norm_at(at),
            "actor": actor,
            "role": role,
            "action": action,
            "customer_id": customer_id,
            "detail": detail,
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )


def _chain_hash(prev_hash: str, material: str) -> str:
    return hashlib.sha256(f"{prev_hash}\n{material}".encode()).hexdigest()


class WorkflowStore:
    """Persistence facade over the workflow tables."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    # ---- users & auth ----------------------------------------------------

    def seed_default_users(self) -> None:
        """Create the demo accounts if they do not exist yet. Idempotent."""
        with self._sf() as s:
            existing = set(s.scalars(select(User.username)).all())
            for spec in DEMO_USERS:
                if spec["username"] in existing:
                    continue
                s.add(User(
                    username=spec["username"],
                    display_name=spec["display_name"],
                    role=spec["role"],
                    password_hash=auth.hash_password(spec["password"]),
                    is_active=True,
                    created_at=_utcnow(),
                ))
            s.commit()

    def create_user(self, username: str, display_name: str, role: str, password: str) -> dict[str, Any]:
        username = username.strip()
        if not username:
            raise ValueError("username must not be empty")
        with self._sf() as s:
            if s.scalar(select(User).where(User.username == username)) is not None:
                raise ValueError(f"user {username!r} already exists")
            user = User(
                username=username,
                display_name=display_name.strip() or username,
                role=role,
                password_hash=auth.hash_password(password),
                is_active=True,
                created_at=_utcnow(),
            )
            s.add(user)
            s.commit()
            return _user_dict(user)

    def list_users(self) -> list[dict[str, Any]]:
        with self._sf() as s:
            users = s.scalars(select(User).order_by(User.created_at)).all()
            return [_user_dict(u) for u in users]

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        """Return the user dict on a correct password, else None. Upgrades a
        stale password hash on successful login (see auth.needs_rehash)."""
        with self._sf() as s:
            user = s.scalar(select(User).where(User.username == username.strip()))
            if user is None or not user.is_active:
                return None
            if not auth.verify_password(password, user.password_hash):
                return None
            if auth.needs_rehash(user.password_hash):
                user.password_hash = auth.hash_password(password)
                s.commit()
            return _user_dict(user)

    def create_login_session(self, user_id: int) -> str:
        """Issue a bearer token, store only its hash, return the raw token."""
        token = auth.new_session_token()
        with self._sf() as s:
            s.add(UserSession(
                token_hash=auth.hash_token(token),
                user_id=user_id,
                created_at=_utcnow(),
                expires_at=_utcnow() + SESSION_TTL,
            ))
            s.commit()
        return token

    def resolve_session(self, token: str | None) -> dict[str, Any] | None:
        """The active user for a raw token, or None if unknown/expired."""
        if not token:
            return None
        with self._sf() as s:
            row = s.scalar(select(UserSession).where(UserSession.token_hash == auth.hash_token(token)))
            if row is None:
                return None
            expires = row.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=dt.UTC)
            if expires < _utcnow():
                return None
            user = s.get(User, row.user_id)
            if user is None or not user.is_active:
                return None
            return _user_dict(user)

    def end_session(self, token: str | None) -> None:
        if not token:
            return
        with self._sf() as s:
            row = s.scalar(select(UserSession).where(UserSession.token_hash == auth.hash_token(token)))
            if row is not None:
                s.delete(row)
                s.commit()

    # ---- cases -----------------------------------------------------------

    def case_count(self) -> int:
        with self._sf() as s:
            return int(s.scalar(select(func.count()).select_from(Case)) or 0)

    def upsert_case(
        self, customer_id: str, profile: dict[str, Any], narratives: dict[str, Any],
        result: dict[str, Any], *, archetype: str = "custom", actor: str = "system",
        note: str = "Initial scoring",
    ) -> None:
        """Insert or re-score a case, appending a 'scored' timeline row. Status
        is preserved on an existing case (a re-score never un-decides it)."""
        now = _utcnow()
        with self._sf() as s:
            case = s.get(Case, customer_id)
            if case is None:
                case = Case(customer_id=customer_id, status="pending_review", created_at=now, created_by=actor)
                s.add(case)
            case.archetype = archetype
            case.profile = profile
            case.narratives = narratives
            case.result = result
            case.scored_at = now
            case.updated_at = now
            s.add(TimelineEvent(
                customer_id=customer_id, kind="scored", actor=actor, at=now,
                payload={"risk_score": result.get("risk_score"), "risk_band": result.get("risk_band"), "note": note},
            ))
            s.commit()

    def add_timeline(self, customer_id: str, kind: str, actor: str, at: dt.datetime | None = None,
                     **payload: Any) -> None:
        with self._sf() as s:
            s.add(TimelineEvent(
                customer_id=customer_id, kind=kind, actor=actor, at=at or _utcnow(), payload=payload,
            ))
            s.commit()

    def set_status(self, customer_id: str, status: str) -> None:
        with self._sf() as s:
            case = s.get(Case, customer_id)
            if case is not None:
                case.status = status
                case.updated_at = _utcnow()
                s.commit()

    def update_result(self, customer_id: str, result: dict[str, Any]) -> None:
        with self._sf() as s:
            case = s.get(Case, customer_id)
            if case is not None:
                case.result = result
                case.scored_at = _utcnow()
                case.updated_at = _utcnow()
                s.commit()

    def load_queue(self) -> dict[str, dict[str, Any]]:
        """Every case as the flat entry dict the console renders — timeline and
        watchlist dispositions reassembled from their own tables."""
        with self._sf() as s:
            cases = s.scalars(select(Case)).all()
            timeline_rows = s.scalars(select(TimelineEvent)).all()
            disp_rows = s.scalars(select(WatchlistDisposition)).all()

        timelines: dict[str, list[dict[str, Any]]] = {}
        for row in sorted(timeline_rows, key=lambda r: (r.at, r.id)):
            timelines.setdefault(row.customer_id, []).append(
                {"kind": row.kind, "at": row.at, "actor": row.actor, **(row.payload or {})}
            )
        dispositions: dict[str, dict[str, dict[str, Any]]] = {}
        for row in disp_rows:
            dispositions.setdefault(row.customer_id, {})[row.hit_id] = {
                "disposition": row.disposition, "note": row.note, "actor": row.actor,
                "role": row.role, "at": row.at,
            }

        queue: dict[str, dict[str, Any]] = {}
        for case in cases:
            queue[case.customer_id] = {
                "customer_id": case.customer_id,
                "archetype": case.archetype,
                "profile": case.profile or {},
                "narratives": case.narratives or {},
                "result": case.result or {},
                "status": case.status,
                "timeline": timelines.get(case.customer_id, []),
                "watchlist_dispositions": dispositions.get(case.customer_id, {}),
            }
        return queue

    # ---- rules -----------------------------------------------------------

    def list_rules(self) -> list[dict[str, Any]]:
        with self._sf() as s:
            rows = s.scalars(select(CustomRule).order_by(CustomRule.position, CustomRule.created_at)).all()
            out = []
            for row in rows:
                rule = dict(row.definition or {})
                rule["enabled"] = row.enabled  # column is source of truth for the toggle
                out.append(rule)
            return out

    def add_rule(self, rule: dict[str, Any], *, actor: str = "system") -> None:
        with self._sf() as s:
            position = int(s.scalar(select(func.count()).select_from(CustomRule)) or 0)
            s.add(CustomRule(
                id=str(rule["id"]),
                name=rule.get("name", ""),
                enabled=bool(rule.get("enabled", True)),
                position=position,
                definition=rule,
                created_by=actor,
                created_at=_utcnow(),
            ))
            s.commit()

    def set_rule_enabled(self, rule_id: str, enabled: bool) -> None:
        with self._sf() as s:
            row = s.get(CustomRule, str(rule_id))
            if row is not None:
                definition = dict(row.definition or {})
                definition["enabled"] = enabled
                row.definition = definition
                row.enabled = enabled
                s.commit()

    def delete_rule(self, rule_id: str) -> None:
        with self._sf() as s:
            row = s.get(CustomRule, str(rule_id))
            if row is not None:
                s.delete(row)
                s.commit()

    # ---- watchlist entries (sanctions/PEP/adverse-media screening data) --

    def seed_demo_watchlist(self) -> None:
        """Insert each demo entry whose SOURCE currently has zero rows — not
        an all-or-nothing check on the whole table, so an ops team that runs
        scripts/refresh_watchlists.py for ofac/un before anyone has ever
        opened the app still gets pep/adverse_media's demo rows seeded (they
        have no real source to refresh from at all, see the module-level
        comment on _DEMO_WATCHLIST_SEED — a whole-table check would leave
        them permanently empty in that ordering). Never re-seeds a source
        that already has rows, real or demo, so a genuine refresh's data is
        never overwritten by this. Idempotent; called from get_store() on
        every process start, same as seed_default_users."""
        with self._sf() as s:
            existing_sources = set(s.scalars(select(WatchlistEntry.source).distinct()).all())
            to_seed = [r for r in _DEMO_WATCHLIST_SEED if r.source not in existing_sources]
            if not to_seed:
                return
            now = _utcnow()
            for record in to_seed:
                s.add(_entry_from_record(record, now))
            s.commit()

    def replace_watchlist_source(self, source: str, records: list[WatchlistRecord]) -> int:
        """Atomically swap in a freshly parsed list for one source: delete
        every existing row for it, insert the new ones, in one transaction.
        Refreshing one source never touches another's rows, and a failure
        partway through rolls back rather than leaving a source
        half-updated. Returns the number of rows inserted."""
        now = _utcnow()
        with self._sf() as s:
            s.execute(delete(WatchlistEntry).where(WatchlistEntry.source == source))
            for record in records:
                s.add(_entry_from_record(record, now))
            s.commit()
        return len(records)

    def list_watchlist_entries(self) -> list[dict[str, Any]]:
        with self._sf() as s:
            rows = s.scalars(select(WatchlistEntry)).all()
            return [_watchlist_entry_dict(r) for r in rows]

    def watchlist_source_status(self) -> list[dict[str, Any]]:
        """Per-source row count and most recent ingest timestamp — what the
        admin-only "watchlist data sources" panel shows, so a compliance
        officer can answer "how do we know this list is current" without
        opening the database directly."""
        with self._sf() as s:
            rows = s.execute(
                select(WatchlistEntry.source, func.count(), func.max(WatchlistEntry.ingested_at))
                .group_by(WatchlistEntry.source)
            ).all()
            return [
                {"source": source, "count": count, "last_refreshed": last}
                for source, count, last in sorted(rows, key=lambda r: r[0])
            ]

    # ---- watchlist dispositions -----------------------------------------

    def add_disposition(
        self, customer_id: str, hit_id: str, disposition: str, note: str, actor: str, role: str,
        *, hit_name: str = "", list_source: str = "",
    ) -> None:
        with self._sf() as s:
            s.add(WatchlistDisposition(
                customer_id=customer_id, hit_id=hit_id, disposition=disposition, note=note,
                actor=actor, role=role, hit_name=hit_name, list_source=list_source, at=_utcnow(),
            ))
            s.commit()

    # ---- filed reports (suspicious transaction/activity reports) --------

    def create_report(self, report_id: str, customer_id: str, *, report_code: str, reason: str,
                      indicators: list[str], xml_content: str, filed_by: str, filed_by_role: str) -> None:
        with self._sf() as s:
            s.add(FiledReport(
                id=report_id, customer_id=customer_id, report_code=report_code, reason=reason,
                indicators=list(indicators), xml_content=xml_content, filed_by=filed_by,
                filed_by_role=filed_by_role, filed_at=_utcnow(),
            ))
            s.commit()

    def list_reports(self, customer_id: str | None = None) -> list[dict[str, Any]]:
        with self._sf() as s:
            stmt = select(FiledReport).order_by(FiledReport.filed_at.desc())
            if customer_id is not None:
                stmt = stmt.where(FiledReport.customer_id == customer_id)
            return [_report_dict(r) for r in s.scalars(stmt).all()]

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        with self._sf() as s:
            row = s.get(FiledReport, report_id)
            return _report_dict(row) if row is not None else None

    # ---- audit log (append-only, hash-chained) ---------------------------

    def append_audit(self, action: str, actor: str, role: str, customer_id: str | None = None,
                     detail: dict[str, Any] | None = None) -> None:
        detail = detail or {}
        at = _utcnow()
        with self._sf() as s:
            last = s.scalar(select(AuditEntry).order_by(AuditEntry.id.desc()).limit(1))
            prev_hash = last.entry_hash if last is not None else GENESIS_HASH
            material = _audit_material(actor, role, action, customer_id, detail, at)
            s.add(AuditEntry(
                at=at, actor=actor, role=role, action=action, customer_id=customer_id,
                detail=detail, prev_hash=prev_hash, entry_hash=_chain_hash(prev_hash, material),
            ))
            s.commit()

    def list_audit(self) -> list[dict[str, Any]]:
        with self._sf() as s:
            rows = s.scalars(select(AuditEntry).order_by(AuditEntry.id)).all()
            return [
                {"at": r.at, "actor": r.actor, "role": r.role, "action": r.action,
                 "customer_id": r.customer_id, "detail": r.detail or {}}
                for r in rows
            ]

    def verify_audit_chain(self) -> tuple[bool, int | None]:
        """Walk the chain; return (ok, first_broken_id). ok=True means every
        row's stored hash matches a recomputation and links to its predecessor —
        proof nothing was altered, reordered, or removed."""
        with self._sf() as s:
            rows = s.scalars(select(AuditEntry).order_by(AuditEntry.id)).all()
            prev_hash = GENESIS_HASH
            for row in rows:
                material = _audit_material(row.actor, row.role, row.action, row.customer_id, row.detail or {}, row.at)
                expected = _chain_hash(prev_hash, material)
                if row.prev_hash != prev_hash or row.entry_hash != expected:
                    return False, row.id
                prev_hash = row.entry_hash
            return True, None


def _entry_from_record(record: WatchlistRecord, at: dt.datetime) -> WatchlistEntry:
    return WatchlistEntry(
        source=record.source, source_id=record.source_id, name=record.name, category=record.category,
        aliases=list(record.aliases), dates_of_birth=list(record.dates_of_birth), countries=list(record.countries),
        program=record.program, remarks=record.remarks, ingested_at=at,
    )


def _watchlist_entry_dict(row: WatchlistEntry) -> dict[str, Any]:
    """The dict shape crr.screening.matcher.screen() and the app's watchlist
    UI expect — "dob"/"country" are the first of the (possibly multi-valued)
    dates_of_birth/countries lists, kept for display; matching itself reads
    the full lists so any of several real dates/countries can corroborate a
    hit, not just the first one."""
    dobs = row.dates_of_birth or []
    countries = row.countries or []
    return {
        "id": f"{row.source}:{row.source_id}",
        "name": row.name,
        "aliases": row.aliases or [],
        "dates_of_birth": dobs,
        "countries": countries,
        "dob": dobs[0] if dobs else None,
        "country": countries[0] if countries else None,
        "list_source": row.source,
        "category": row.category,
        "reason": row.remarks or row.program or f"Listed on the {row.source.upper()} sanctions list.",
    }


def _report_dict(row: FiledReport) -> dict[str, Any]:
    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "report_code": row.report_code,
        "reason": row.reason,
        "indicators": row.indicators or [],
        "xml_content": row.xml_content,
        "filed_by": row.filed_by,
        "filed_by_role": row.filed_by_role,
        "filed_at": row.filed_at,
    }


def _user_dict(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
    }
