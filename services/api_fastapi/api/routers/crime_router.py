"""
services/api_fastapi/api/routers/crime_router.py

Crime execution and listing endpoints.
Requirements: 6.1, 6.2
"""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.api_fastapi.api.deps import (
    get_crime_service,
    get_session,
    require_age_confirmed,
)
from services.api_fastapi.domain.services.crime_service import CrimeService


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CrimeResultResponse(BaseModel):
    crime_id: str
    cash_earned: int
    xp_earned: int
    heat_added: int
    new_rank: Optional[str]
    promoted: bool
    nerve_remaining: int


class CrimeDefinitionResponse(BaseModel):
    crime_id: str
    name: str
    nerve_cost: int
    cash_min: int
    cash_max: int
    xp_reward: int
    heat_increase: int


# ---------------------------------------------------------------------------
# Router — age gate applied to all endpoints
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/crimes", tags=["crimes"])


@router.post("/{crime_id}/execute", response_model=CrimeResultResponse)
async def execute_crime(
    crime_id: str,
    request: Request,
    player_id: UUID = Depends(require_age_confirmed),
    session: AsyncSession = Depends(get_session),
    crime_svc: CrimeService = Depends(get_crime_service),
) -> CrimeResultResponse:
    idem_key: str = request.state.idempotency_key
    result = await crime_svc.execute_crime(session, player_id, crime_id, idem_key)
    return CrimeResultResponse(
        crime_id=result.crime_id,
        cash_earned=result.cash_earned,
        xp_earned=result.xp_earned,
        heat_added=result.heat_added,
        new_rank=result.new_rank,
        promoted=result.promoted,
        nerve_remaining=result.nerve_remaining,
    )


@router.get("", response_model=List[CrimeDefinitionResponse])
async def list_crimes(
    player_id: UUID = Depends(require_age_confirmed),
    crime_svc: CrimeService = Depends(get_crime_service),
) -> List[CrimeDefinitionResponse]:
    crimes = await crime_svc.list_crimes()
    return [
        CrimeDefinitionResponse(
            crime_id=c.crime_id,
            name=c.name,
            nerve_cost=c.nerve_cost,
            cash_min=c.cash_min,
            cash_max=c.cash_max,
            xp_reward=c.xp_reward,
            heat_increase=c.heat_increase,
        )
        for c in crimes
    ]
