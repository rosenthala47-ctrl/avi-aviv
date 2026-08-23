"""Dependency wiring for the API.

Backends are chosen from settings: empty URLs mean in-memory (the service runs
with no infra), a database or Redis URL switches to the durable, shared
implementation. Everything hangs off ``app.state`` so tests can build an app with
a pre-loaded model bundle and in-memory repositories in milliseconds.
"""

from __future__ import annotations

from fastapi import Request

from crr.api.cache import InMemoryCache, KeyValueStore, RedisCache
from crr.api.repository import (
    EventRepository,
    ExtractionRepository,
    InMemoryEventRepository,
    InMemoryExtractionRepository,
    InMemoryJobRepository,
    InMemoryScoreRepository,
    JobRepository,
    ScoreRepository,
    SqlAlchemyEventRepository,
    SqlAlchemyExtractionRepository,
    SqlAlchemyJobRepository,
    SqlAlchemyScoreRepository,
    create_session_factory,
)
from crr.api.scoring import ModelBundle, ScoringService
from crr.api.settings import Settings
from crr.llm.anthropic_extractor import AnthropicExtractor
from crr.llm.cache import CachingExtractor
from crr.llm.extraction import Extractor
from crr.llm.reference_extractor import ReferenceExtractor
from crr.pipelines.notifications import LoggingNotificationSink, NotificationSink
from crr.pipelines.rescoring import RescoringEngine


def build_backends(
    settings: Settings,
) -> tuple[ScoreRepository, JobRepository, KeyValueStore, EventRepository]:
    """Choose repositories and cache from settings."""
    if settings.database_url:
        session_factory = create_session_factory(settings.database_url)
        scores: ScoreRepository = SqlAlchemyScoreRepository(session_factory)
        jobs: JobRepository = SqlAlchemyJobRepository(session_factory)
        events: EventRepository = SqlAlchemyEventRepository(session_factory)
    else:
        scores = InMemoryScoreRepository()
        jobs = InMemoryJobRepository()
        events = InMemoryEventRepository()
    cache: KeyValueStore = RedisCache(settings.redis_url) if settings.redis_url else InMemoryCache()
    return scores, jobs, cache, events


def build_notifications(settings: Settings) -> NotificationSink:  # settings: unused today, kept for a future webhook/queue URL
    """The zero-infrastructure production default: a structured log line a
    downstream system already tailing logs (see ``crr.api.audit``) can pick up
    with no new integration. See ``LoggingNotificationSink`` for the swap path."""
    return LoggingNotificationSink()


def build_extraction_cache(settings: Settings) -> ExtractionRepository:
    """A separate connection pool from ``build_backends``'s when a database is
    configured — a second, small pool against the same URL, traded here for
    keeping this concern independent (the same choice already made for
    ``build_notifications``). Worth revisiting only if pool exhaustion is
    ever actually observed."""
    if settings.database_url:
        return SqlAlchemyExtractionRepository(create_session_factory(settings.database_url))
    return InMemoryExtractionRepository()


def build_extractor(settings: Settings) -> Extractor:
    """The real Claude-backed extractor when an API key is configured, the
    deterministic reference extractor otherwise — the same "runs with no
    infrastructure by default" pattern as every other backend here. Always
    cached: notes change rarely and LLM calls dominate cost (phase 7)."""
    inner: Extractor
    if settings.anthropic_api_key:
        inner = AnthropicExtractor(api_key=settings.anthropic_api_key, model=settings.extraction_model)
    else:
        inner = ReferenceExtractor()
    return CachingExtractor(inner, build_extraction_cache(settings))


def load_service(settings: Settings) -> ScoringService:
    bundle = ModelBundle.load(settings.model_dir)
    return ScoringService(bundle, extractor=build_extractor(settings))


# ---- request-scoped accessors -------------------------------------------


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_service(request: Request) -> ScoringService:
    return request.app.state.service


def get_scores(request: Request) -> ScoreRepository:
    return request.app.state.scores


def get_jobs(request: Request) -> JobRepository:
    return request.app.state.jobs


def get_cache(request: Request) -> KeyValueStore:
    return request.app.state.cache


def get_events(request: Request) -> EventRepository:
    return request.app.state.events


def get_notifications(request: Request) -> NotificationSink:
    return request.app.state.notifications


def get_rescoring_engine(request: Request) -> RescoringEngine:
    return request.app.state.rescoring_engine
