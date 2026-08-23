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
    InMemoryEventRepository,
    InMemoryJobRepository,
    InMemoryScoreRepository,
    JobRepository,
    ScoreRepository,
    SqlAlchemyEventRepository,
    SqlAlchemyJobRepository,
    SqlAlchemyScoreRepository,
    create_session_factory,
)
from crr.api.scoring import ModelBundle, ScoringService
from crr.api.settings import Settings
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


def load_service(settings: Settings) -> ScoringService:
    bundle = ModelBundle.load(settings.model_dir)
    return ScoringService(bundle)


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
