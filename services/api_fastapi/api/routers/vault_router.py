"""
services/api_fastapi/api/routers/vault_router.py

Family Vault balance and withdrawal endpoints.
Requirements: 6.1, 6.2, 6.3, 6.4, 6.5
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.api_fastapi.api.deps import (
    get_session,
    get_vault_service,
    require_age_confirmed,
    require_family_membership,
)
from services.api_fastapi.domain.models.family import FamilyMember
from services.api_fastapi.domain.services.vault_service import FamilyVaultService


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class WithdrawRequest(BaseModel):
    target_member_id: UUID
    amount: int


class VaultBalanceResponse(BaseModel):
    family_id: UUID
    balance: int


class WithdrawResponse(BaseModel):
    from_balance: int
    to_balance: int


# ---------------------------------------------------------------------------
# Router — age gate + family membership applied to all endpoints
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/families/me/vault", tags=["vault"])


@router.get("", response_model=VaultBalanceResponse)
async def get_vault_balance(
    _player_id: UUID = Depends(require_age_confirmed),
    member: FamilyMember = Depends(require_family_membership),
    session: AsyncSession = Depends(get_session),
    vault_svc: FamilyVaultService = Depends(get_vault_service),
) -> VaultBalanceResponse:
    balance = await vault_svc.get_vault_balance(session, member.family_id)
    return VaultBalanceResponse(family_id=member.family_id, balance=balance)


@router.post("/withdraw", response_model=WithdrawResponse)
async def withdraw(
    body: WithdrawRequest,
    request: Request,
    _player_id: UUID = Depends(require_age_confirmed),
    member: FamilyMember = Depends(require_family_membership),
    session: AsyncSession = Depends(get_session),
    vault_svc: FamilyVaultService = Depends(get_vault_service),
) -> WithdrawResponse:
    idem_key: str = request.state.idempotency_key
    result = await vault_svc.withdraw(
        session,
        actor_id=member.player_id,
        family_id=member.family_id,
        target_member_id=body.target_member_id,
        amount=body.amount,
        idempotency_key=idem_key,
    )
    return WithdrawResponse(
        from_balance=result.from_balance,
        to_balance=result.to_balance,
    )
