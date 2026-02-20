"""
services/api_fastapi/api/deps.py

FastAPI dependency injection: JWT auth, age gate, DB session, Redis,
and service factories.
"""

from __future__ import annotations

import os
from typing import AsyncGenerator
from uuid import UUID

import jwt
import redis.asyncio as aioredis
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from services.api_fastapi.domain.models.player import Player
from services.api_fastapi.domain.services.auth_service import (
    AgeRequired,
    AuthService,
    InvalidToken,
)
from services.api_fastapi.domain.services.chat_service import ChatService
from services.api_fastapi.domain.services.config_service import ConfigService
from services.api_fastapi.domain.services.crime_service import CrimeService
from services.api_fastapi.domain.services.family_service import FamilyService, NotInFamily
from services.api_fastapi.domain.services.heat_service import HeatService
from services.api_fastapi.domain.services.nerve_service import NerveService
from services.api_fastapi.domain.services.profile_service import PlayerProfileService
from services.api_fastapi.domain.services.property_service import PropertyService
from services.api_fastapi.domain.services.rank_service import RankService
from services.api_fastapi.domain.services.vault_service import FamilyVaultService

# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

_JWT_ALGORITHM = "HS256"


def _jwt_secret() -> str:
    return os.environ.get("JWT_SECRET", "dev-secret-change-me")


async def get_current_player_id(request: Request) -> UUID:
    """Extract and validate JWT from Authorization header, return player_id."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise InvalidToken("Missing or malformed Authorization header.")
    token = auth_header[7:]
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[_JWT_ALGORITHM])
        return UUID(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError) as exc:
        raise InvalidToken(f"Invalid token: {exc}") from exc


# ---------------------------------------------------------------------------
# Infrastructure singletons (set during app startup)
# ---------------------------------------------------------------------------

_redis: aioredis.Redis | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def set_redis(r: aioredis.Redis) -> None:
    global _redis
    _redis = r


def set_session_factory(sf: async_sessionmaker[AsyncSession]) -> None:
    global _session_factory
    _session_factory = sf


def get_redis() -> aioredis.Redis:
    assert _redis is not None, "Redis not initialised"
    return _redis


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    assert _session_factory is not None, "Session factory not initialised"
    async with _session_factory() as session:
        async with session.begin():
            yield session


# ---------------------------------------------------------------------------
# Service factories
# ---------------------------------------------------------------------------

def get_config_service(
    r: aioredis.Redis = Depends(get_redis),
) -> ConfigService:
    return ConfigService(r)


def get_nerve_service(
    r: aioredis.Redis = Depends(get_redis),
    config: ConfigService = Depends(get_config_service),
) -> NerveService:
    return NerveService(r, config)


def get_heat_service(
    r: aioredis.Redis = Depends(get_redis),
    config: ConfigService = Depends(get_config_service),
) -> HeatService:
    return HeatService(r, config)


def get_rank_service(
    nerve: NerveService = Depends(get_nerve_service),
) -> RankService:
    return RankService(nerve)


def get_profile_service(
    nerve: NerveService = Depends(get_nerve_service),
    heat: HeatService = Depends(get_heat_service),
) -> PlayerProfileService:
    return PlayerProfileService(nerve, heat)


def get_vault_service(
    config: ConfigService = Depends(get_config_service),
) -> FamilyVaultService:
    return FamilyVaultService(config)


def get_family_service(
    r: aioredis.Redis = Depends(get_redis),
    config: ConfigService = Depends(get_config_service),
) -> FamilyService:
    return FamilyService(r, config)


def get_property_service(
    config: ConfigService = Depends(get_config_service),
) -> PropertyService:
    return PropertyService(config)


def get_chat_service(
    r: aioredis.Redis = Depends(get_redis),
    config: ConfigService = Depends(get_config_service),
) -> ChatService:
    return ChatService(r, config)


def get_crime_service(
    r: aioredis.Redis = Depends(get_redis),
    config: ConfigService = Depends(get_config_service),
    nerve: NerveService = Depends(get_nerve_service),
    heat: HeatService = Depends(get_heat_service),
    rank: RankService = Depends(get_rank_service),
    vault: FamilyVaultService = Depends(get_vault_service),
) -> CrimeService:
    return CrimeService(r, config, nerve, heat, rank, vault_service=vault)


def get_auth_service(
    r: aioredis.Redis = Depends(get_redis),
) -> AuthService:
    from services.api_fastapi.domain.services.auth_service import (
        AppleTokenVerifier,
        EmailSender,
    )

    class _StubApple:
        async def verify(self, identity_token: str) -> str:
            raise NotImplementedError("Apple verifier not configured")

    class _StubEmail:
        async def send_otp(self, email: str, code: str) -> None:
            pass  # no-op in dev

    return AuthService(
        redis=r,
        apple_verifier=_StubApple(),
        email_sender=_StubEmail(),
        jwt_secret=_jwt_secret(),
    )


# ---------------------------------------------------------------------------
# Age gate dependency
# ---------------------------------------------------------------------------

async def require_age_confirmed(
    player_id: UUID = Depends(get_current_player_id),
    session: AsyncSession = Depends(get_session),
) -> UUID:
    """Dependency that blocks unconfirmed players from gameplay endpoints."""
    from sqlalchemy import select

    stmt = select(Player).where(Player.id == player_id)
    result = await session.execute(stmt)
    player = result.scalar_one_or_none()
    if player is None or not player.age_confirmed:
        raise AgeRequired("You must confirm you are 18+ to access gameplay.")
    return player_id


# ---------------------------------------------------------------------------
# Family membership gate dependency
# ---------------------------------------------------------------------------

async def require_family_membership(
    player_id: UUID = Depends(get_current_player_id),
    session: AsyncSession = Depends(get_session),
) -> "FamilyMember":
    """Dependency that loads the player's FamilyMember record or raises NotInFamily."""
    from sqlalchemy import select
    from services.api_fastapi.domain.models.family import FamilyMember

    stmt = select(FamilyMember).where(FamilyMember.player_id == player_id)
    result = await session.execute(stmt)
    member = result.scalar_one_or_none()
    if member is None:
        raise NotInFamily("You are not a member of any family.")
    return member
