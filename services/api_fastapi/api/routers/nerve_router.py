"""
services/api_fastapi/api/routers/nerve_router.py

Nerve (energy) read endpoint.
Requirements: 5.1
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from services.api_fastapi.api.deps import (
    get_nerve_service,
    require_age_confirmed,
)
from services.api_fastapi.domain.services.nerve_service import NerveService


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class NerveResponse(BaseModel):
    current: int
    max_nerve: int
    next_regen_at: Optional[float]


# ---------------------------------------------------------------------------
# Router — age gate applied
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/nerve", tags=["nerve"])


@router.get("", response_model=NerveResponse)
async def get_nerve(
    player_id: UUID = Depends(require_age_confirmed),
    nerve_svc: NerveService = Depends(get_nerve_service),
) -> NerveResponse:
    state = await nerve_svc.get_nerve(player_id)
    return NerveResponse(
        current=state.current,
        max_nerve=state.max_nerve,
        next_regen_at=state.next_regen_at,
    )
