"""Liveness / readiness."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from crr.api.dependencies import get_service, get_settings
from crr.api.schemas import HealthResponse
from crr.api.scoring import ScoringService
from crr.api.settings import Settings

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(service: ScoringService = Depends(get_service), settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        models_loaded=sorted(service.bundle.explainers),
        policy_version=service.policy.version,
        version=settings.api_version,
    )
