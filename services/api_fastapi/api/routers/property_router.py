"""
services/api_fastapi/api/routers/property_router.py

Property listing, family property listing, purchase, and upgrade endpoints.
Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 9.1, 9.4, 9.5, 9.6, 9.7
"""

from __future__ import annotations

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.api_fastapi.api.deps import (
    get_config_service,
    get_property_service,
    get_session,
    require_age_confirmed,
    require_family_membership,
)
from services.api_fastapi.domain.models.family import FamilyMember
from services.api_fastapi.domain.services.config_service import ConfigService
from services.api_fastapi.domain.services.property_service import PropertyService


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class PropertyDefinitionResponse(BaseModel):
    property_id: str
    name: str
    purchase_price: int
    daily_income: int
    max_level: int


class FamilyPropertyResponse(BaseModel):
    property_id: str
    level: int
    purchased_at: str
    updated_at: str


class PropertyOwnershipResponse(BaseModel):
    family_id: UUID
    property_id: str
    name: str
    level: int
    daily_income: int


# ---------------------------------------------------------------------------
# Top-level router — GET /properties (JWT + age gate, no family membership)
# ---------------------------------------------------------------------------

properties_router = APIRouter(prefix="/properties", tags=["properties"])


@properties_router.get("", response_model=List[PropertyDefinitionResponse])
async def list_properties(
    _player_id: UUID = Depends(require_age_confirmed),
    config: ConfigService = Depends(get_config_service),
    prop_svc: PropertyService = Depends(get_property_service),
) -> List[PropertyDefinitionResponse]:
    defs = await prop_svc.list_properties(config)
    return [
        PropertyDefinitionResponse(
            property_id=d.property_id,
            name=d.name,
            purchase_price=d.purchase_price,
            daily_income=d.daily_income,
            max_level=d.max_level,
        )
        for d in defs
    ]


# ---------------------------------------------------------------------------
# Family-scoped router — /families/me/properties (JWT + age gate + family)
# ---------------------------------------------------------------------------

family_properties_router = APIRouter(
    prefix="/families/me/properties", tags=["properties"],
)


@family_properties_router.get("", response_model=List[FamilyPropertyResponse])
async def list_family_properties(
    _player_id: UUID = Depends(require_age_confirmed),
    member: FamilyMember = Depends(require_family_membership),
    session: AsyncSession = Depends(get_session),
    prop_svc: PropertyService = Depends(get_property_service),
) -> List[FamilyPropertyResponse]:
    props = await prop_svc.list_family_properties(session, member.family_id)
    return [
        FamilyPropertyResponse(
            property_id=fp.property_id,
            level=fp.level,
            purchased_at=fp.purchased_at.isoformat(),
            updated_at=fp.updated_at.isoformat(),
        )
        for fp in props
    ]


@family_properties_router.post(
    "/{property_id}/purchase",
    response_model=PropertyOwnershipResponse,
)
async def purchase_property(
    property_id: str,
    request: Request,
    _player_id: UUID = Depends(require_age_confirmed),
    member: FamilyMember = Depends(require_family_membership),
    session: AsyncSession = Depends(get_session),
    prop_svc: PropertyService = Depends(get_property_service),
) -> PropertyOwnershipResponse:
    idem_key: str = request.state.idempotency_key
    result = await prop_svc.purchase_property(
        session,
        actor_id=member.player_id,
        family_id=member.family_id,
        property_id=property_id,
        idempotency_key=idem_key,
    )
    return PropertyOwnershipResponse(
        family_id=result.family_id,
        property_id=result.property_id,
        name=result.name,
        level=result.level,
        daily_income=result.daily_income,
    )


@family_properties_router.post(
    "/{property_id}/upgrade",
    response_model=PropertyOwnershipResponse,
)
async def upgrade_property(
    property_id: str,
    request: Request,
    _player_id: UUID = Depends(require_age_confirmed),
    member: FamilyMember = Depends(require_family_membership),
    session: AsyncSession = Depends(get_session),
    prop_svc: PropertyService = Depends(get_property_service),
) -> PropertyOwnershipResponse:
    idem_key: str = request.state.idempotency_key
    result = await prop_svc.upgrade_property(
        session,
        actor_id=member.player_id,
        family_id=member.family_id,
        property_id=property_id,
        idempotency_key=idem_key,
    )
    return PropertyOwnershipResponse(
        family_id=result.family_id,
        property_id=result.property_id,
        name=result.name,
        level=result.level,
        daily_income=result.daily_income,
    )
