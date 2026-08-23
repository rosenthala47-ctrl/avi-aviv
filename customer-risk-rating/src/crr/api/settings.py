"""Service configuration, from environment variables.

Twelve-factor: everything that changes between environments is an env var, never a
literal in code. The defaults are the infra-free local setup (in-memory
everything), so the service starts with no configuration; production sets
``CRR_DATABASE_URL`` and ``CRR_REDIS_URL`` to switch to durable, shared backends.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from crr.llm.anthropic_extractor import DEFAULT_MODEL

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CRR_", env_file=".env", extra="ignore")

    model_dir: Path = REPO_ROOT / "models"
    policy_path: Path = REPO_ROOT / "config" / "risk_policy.yaml"

    # Empty => in-memory backends. Set to switch to Postgres / Redis.
    database_url: str = ""
    redis_url: str = ""

    idempotency_ttl_seconds: int = 300
    max_batch_sync_size: int = 50
    api_version: str = "0.4.0"

    # Empty => the deterministic reference extractor (no network, no cost).
    # Set to use the real Claude-backed extractor for phase 7's text signals.
    anthropic_api_key: str = ""
    extraction_model: str = DEFAULT_MODEL
