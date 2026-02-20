"""
services/api_fastapi/api/routers/family_router.py

Family CRUD, membership, role management, and dissolution endpoints.
Requirements: 1.1, 2.1, 2.5, 2.7, 3.1, 3.3, 3.7, 4.1, 13.1
"""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.api_fastapi.api.deps import (
    get_family_service,
    get_session,
    require_age_confirmed,
)
from services.api_fastapi.domain.models.family import FamilyRole
from services.api_fastapi.domain.services.family_service import FamilyService


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class CreateFamilyRequest(BaseModel):
    name: str
    tag: str


class KickRequest(BaseModel):
    target_id: UUID


class PromoteRequest(BaseModel):
    target_id: UUID
    new_role: str  # "CAPO", "UNDERBOSS"


class DemoteRequest(BaseModel):
    target_id: UUID
    new_role: str  # "SOLDIER", "CAPO"


class TransferDonRequest(BaseModel):
    target_id: UUID


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class FamilyResponse(BaseModel):
    family_id: UUID
    name: str
    tag: str


class FamilyDetailResponse(BaseModel):
    family_id: UUID
    name: str
    tag: str
    status: str
    created_at: str
    member_count: int
    vault_balance: Optional[int] = None


class MemberResponse(BaseModel):
    player_id: UUID
    display_name: str
    role: str
    joined_at: str


class RoleChangeResponse(BaseModel):
    player_id: UUID
    old_role: str
    new_role: str


class DisbandResponse(BaseModel):
    family_id: UUID
    vault_transferred: int


# ---------------------------------------------------------------------------
# Router — age gate applied to all endpoints
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/families", tags=["families"])


@router.post("", response_model=FamilyResponse, status_code=201)
async def create_family(
    body: CreateFamilyRequest,
    request: Request,
    player_id: UUID = Depends(require_age_confirmed),
    session: AsyncSession = Depends(get_session),
    family_svc: FamilyService = Depends(get_family_service),
) -> FamilyResponse:
    idem_key: str = request.state.idempotency_key
    result = await family_svc.create_family(
        session, player_id, body.name, body.tag, idem_key,
    )
    return FamilyResponse(
        family_id=result.family_id,
        name=result.name,
        tag=result.tag,
    )


@router.get("/me", response_model=Optional[FamilyDetailResponse])
async def get_my_family(
    player_id: UUID = Depends(require_age_confirmed),
    session: AsyncSession = Depends(get_session),
    family_svc: FamilyService = Depends(get_family_service),
) -> FamilyDetailResponse:
    detail = await family_svc.get_player_family(session, player_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="not_in_family")
    return FamilyDetailResponse(
        family_id=detail.family_id,
        name=detail.name,
        tag=detail.tag,
        status=detail.status.value,
        created_at=detail.created_at.isoformat(),
        member_count=detail.member_count,
        vault_balance=detail.vault_balance,
    )


@router.get("/{family_id}", response_model=FamilyDetailResponse)
async def get_family(
    family_id: UUID,
    player_id: UUID = Depends(require_age_confirmed),
    session: AsyncSession = Depends(get_session),
    family_svc: FamilyService = Depends(get_family_service),
) -> FamilyDetailResponse:
    detail = await family_svc.get_family(session, family_id)
    return FamilyDetailResponse(
        family_id=detail.family_id,
        name=detail.name,
        tag=detail.tag,
        status=detail.status.value,
        created_at=detail.created_at.isoformat(),
        member_count=detail.member_count,
        vault_balance=detail.vault_balance,
    )


@router.get("/{family_id}/members", response_model=List[MemberResponse])
async def list_members(
    family_id: UUID,
    player_id: UUID = Depends(require_age_confirmed),
    session: AsyncSession = Depends(get_session),
    family_svc: FamilyService = Depends(get_family_service),
) -> List[MemberResponse]:
    members = await family_svc.list_members(session, family_id)
    return [
        MemberResponse(
            player_id=m.player_id,
            display_name=m.display_name,
            role=m.role.value,
            joined_at=m.joined_at.isoformat(),
        )
        for m in members
    ]


@router.post("/{family_id}/join", response_model=MemberResponse)
async def join_family(
    family_id: UUID,
    request: Request,
    player_id: UUID = Depends(require_age_confirmed),
    session: AsyncSession = Depends(get_session),
    family_svc: FamilyService = Depends(get_family_service),
) -> MemberResponse:
    idem_key: str = request.state.idempotency_key
    result = await family_svc.join_family(session, player_id, family_id, idem_key)
    return MemberResponse(
        player_id=result.player_id,
        display_name=result.display_name,
        role=result.role.value,
        joined_at=result.joined_at.isoformat(),
    )


@router.post("/me/leave", status_code=204)
async def leave_family(
    request: Request,
    player_id: UUID = Depends(require_age_confirmed),
    session: AsyncSession = Depends(get_session),
    family_svc: FamilyService = Depends(get_family_service),
) -> None:
    idem_key: str = request.state.idempotency_key
    await family_svc.leave_family(session, player_id, idem_key)


@router.post("/me/kick", status_code=204)
async def kick_member(
    body: KickRequest,
    request: Request,
    player_id: UUID = Depends(require_age_confirmed),
    session: AsyncSession = Depends(get_session),
    family_svc: FamilyService = Depends(get_family_service),
) -> None:
    idem_key: str = request.state.idempotency_key
    await family_svc.kick_member(session, player_id, body.target_id, idem_key)


@router.post("/me/promote", response_model=RoleChangeResponse)
async def promote_member(
    body: PromoteRequest,
    request: Request,
    player_id: UUID = Depends(require_age_confirmed),
    session: AsyncSession = Depends(get_session),
    family_svc: FamilyService = Depends(get_family_service),
) -> RoleChangeResponse:
    idem_key: str = request.state.idempotency_key
    result = await family_svc.promote_member(
        session, player_id, body.target_id, FamilyRole(body.new_role), idem_key,
    )
    return RoleChangeResponse(
        player_id=result.player_id,
        old_role=result.old_role.value,
        new_role=result.new_role.value,
    )


@router.post("/me/demote", response_model=RoleChangeResponse)
async def demote_member(
    body: DemoteRequest,
    request: Request,
    player_id: UUID = Depends(require_age_confirmed),
    session: AsyncSession = Depends(get_session),
    family_svc: FamilyService = Depends(get_family_service),
) -> RoleChangeResponse:
    idem_key: str = request.state.idempotency_key
    result = await family_svc.demote_member(
        session, player_id, body.target_id, FamilyRole(body.new_role), idem_key,
    )
    return RoleChangeResponse(
        player_id=result.player_id,
        old_role=result.old_role.value,
        new_role=result.new_role.value,
    )


@router.post("/me/transfer-don", response_model=RoleChangeResponse)
async def transfer_don(
    body: TransferDonRequest,
    request: Request,
    player_id: UUID = Depends(require_age_confirmed),
    session: AsyncSession = Depends(get_session),
    family_svc: FamilyService = Depends(get_family_service),
) -> RoleChangeResponse:
    idem_key: str = request.state.idempotency_key
    result = await family_svc.transfer_don(
        session, player_id, body.target_id, idem_key,
    )
    return RoleChangeResponse(
        player_id=result.player_id,
        old_role=result.old_role.value,
        new_role=result.new_role.value,
    )


@router.post("/me/disband", response_model=DisbandResponse)
async def disband_family(
    request: Request,
    player_id: UUID = Depends(require_age_confirmed),
    session: AsyncSession = Depends(get_session),
    family_svc: FamilyService = Depends(get_family_service),
) -> DisbandResponse:
    idem_key: str = request.state.idempotency_key
    result = await family_svc.disband_family(session, player_id, idem_key)
    return DisbandResponse(
        family_id=result.family_id,
        vault_transferred=result.vault_transferred,
    )
