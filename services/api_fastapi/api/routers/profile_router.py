"""
services/api_fastapi/api/routers/profile_router.py

Player profile endpoints.
Requirements: 4.2, 4.4
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.api_fastapi.api.deps import (
    get_profile_service,
    get_session,
    require_age_confirmed,
)
from services.api_fastapi.domain.services.profile_service import PlayerProfileService


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class UpdateNameRequest(BaseModel):
    display_name: str


class ProfileResponse(BaseModel):
    player_id: str
    display_name: Optional[str]
    rank: str
    xp: int
    heat: int
    cash_balance: int
    nerve_current: int
    nerve_max: int
    next_regen_at: Optional[float]


# ---------------------------------------------------------------------------
# Router — age gate applied to all endpoints
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("/me", response_model=ProfileResponse)
async def get_profile(
    player_id: UUID = Depends(require_age_confirmed),
    session: AsyncSession = Depends(get_session),
    profile_svc: PlayerProfileService = Depends(get_profile_service),
) -> ProfileResponse:
    result = await profile_svc.get_profile(session, player_id)
    return ProfileResponse(
        player_id=str(result.player_id),
        display_name=result.display_name,
        rank=result.rank,
        xp=result.xp,
        heat=result.heat,
        cash_balance=result.cash_balance,
        nerve_current=result.nerve_current,
        nerve_max=result.nerve_max,
        next_regen_at=result.next_regen_at,
    )


@router.put("/me/name", response_model=ProfileResponse)
async def update_display_name(
    body: UpdateNameRequest,
    request: Request,
    player_id: UUID = Depends(require_age_confirmed),
    session: AsyncSession = Depends(get_session),
    profile_svc: PlayerProfileService = Depends(get_profile_service),
) -> ProfileResponse:
    idem_key: str = request.state.idempotency_key
    await profile_svc.update_display_name(session, player_id, body.display_name, idem_key)
    # Return the full profile after update
    result = await profile_svc.get_profile(session, player_id)
    return ProfileResponse(
        player_id=str(result.player_id),
        display_name=result.display_name,
        rank=result.rank,
        xp=result.xp,
        heat=result.heat,
        cash_balance=result.cash_balance,
        nerve_current=result.nerve_current,
        nerve_max=result.nerve_max,
        next_regen_at=result.next_regen_at,
    )
