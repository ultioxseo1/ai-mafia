"""
services/api_fastapi/api/routers/auth_router.py

Auth endpoints: Apple Sign-In, Email OTP, age gate.
Requirements: 1.1, 2.1, 2.2, 3.1
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.api_fastapi.api.deps import (
    get_auth_service,
    get_current_player_id,
    get_session,
)
from services.api_fastapi.domain.services.auth_service import AuthService


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class AppleSignInRequest(BaseModel):
    identity_token: str


class OTPRequestBody(BaseModel):
    email: str


class OTPVerifyRequest(BaseModel):
    email: str
    code: str


class AgeConfirmRequest(BaseModel):
    confirmed: bool


class AuthResponse(BaseModel):
    player_id: str
    jwt_token: str
    is_new_player: bool


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/apple", response_model=AuthResponse)
async def apple_sign_in(
    body: AppleSignInRequest,
    session: AsyncSession = Depends(get_session),
    auth: AuthService = Depends(get_auth_service),
) -> AuthResponse:
    result = await auth.apple_sign_in(session, body.identity_token)
    return AuthResponse(
        player_id=str(result.player_id),
        jwt_token=result.jwt_token,
        is_new_player=result.is_new_player,
    )


@router.post("/otp/request", status_code=204)
async def request_otp(
    body: OTPRequestBody,
    auth: AuthService = Depends(get_auth_service),
) -> None:
    await auth.request_otp(body.email)


@router.post("/otp/verify", response_model=AuthResponse)
async def verify_otp(
    body: OTPVerifyRequest,
    session: AsyncSession = Depends(get_session),
    auth: AuthService = Depends(get_auth_service),
) -> AuthResponse:
    result = await auth.verify_otp(session, body.email, body.code)
    return AuthResponse(
        player_id=str(result.player_id),
        jwt_token=result.jwt_token,
        is_new_player=result.is_new_player,
    )


@router.post("/age-confirm", status_code=204)
async def confirm_age(
    body: AgeConfirmRequest,
    player_id: UUID = Depends(get_current_player_id),
    session: AsyncSession = Depends(get_session),
    auth: AuthService = Depends(get_auth_service),
) -> None:
    await auth.confirm_age(session, player_id, body.confirmed)
