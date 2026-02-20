"""
services/api_fastapi/domain/services/auth_service.py

AI MAFIA — Auth Service

Handles Apple Sign-In, Email OTP authentication, age gate, and JWT
issuance.  OTP codes and rate-limit counters live in Redis.  Player
records and wallets are persisted in PostgreSQL.

Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6,
              3.1, 3.2, 3.3
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

import jwt
import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api_fastapi.domain.models.economy import (
    Currency,
    OwnerType,
    Wallet,
)
from services.api_fastapi.domain.models.player import Player


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class InvalidToken(Exception):
    """Apple identity token is invalid or expired."""


class UpstreamUnavailable(Exception):
    """Apple server verification failed due to a network error."""


class InvalidOTP(Exception):
    """OTP code does not match the stored code."""


class OTPExpired(Exception):
    """OTP TTL (10 min) has elapsed."""


class RateLimited(Exception):
    """More than 5 OTP requests in a 15-minute window."""


class AgeRequired(Exception):
    """Player has not confirmed 18+ age gate."""


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AuthResult:
    """Returned by successful authentication flows."""

    player_id: UUID
    jwt_token: str
    is_new_player: bool


# ---------------------------------------------------------------------------
# Protocols / Interfaces
# ---------------------------------------------------------------------------

class EmailSender(Protocol):
    """Abstract email sender — actual implementation is out of scope for M1."""

    async def send_otp(self, email: str, code: str) -> None: ...


class AppleTokenVerifier(Protocol):
    """
    Verifies an Apple identity token and returns the ``sub`` claim.

    Implementations may call Apple's servers (httpx) or act as a stub
    for testing.
    """

    async def verify(self, identity_token: str) -> str:
        """Return the Apple ``sub`` claim, or raise InvalidToken / UpstreamUnavailable."""
        ...


# ---------------------------------------------------------------------------
# Redis key helpers
# ---------------------------------------------------------------------------

OTP_TTL = 600          # 10 minutes
RATE_WINDOW = 900      # 15 minutes
RATE_LIMIT = 5         # max OTP requests per window


def _otp_key(email: str) -> str:
    return f"otp:{email}"


def _rate_key(email: str) -> str:
    return f"otp_rate:{email}"


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

_JWT_ALGORITHM = "HS256"
_JWT_EXPIRY_HOURS = 24


def _create_jwt(player_id: UUID, secret: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(player_id),
        "iat": now,
        "exp": now + timedelta(hours=_JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, secret, algorithm=_JWT_ALGORITHM)


# ---------------------------------------------------------------------------
# AuthService
# ---------------------------------------------------------------------------

class AuthService:
    """
    Authentication service: Apple Sign-In, Email OTP, age gate, JWT.

    Dependencies:
      - ``AsyncSession``         — passed per-call for DB operations
      - ``redis.asyncio.Redis``  — OTP storage and rate limiting
      - ``AppleTokenVerifier``   — verifies Apple identity tokens
      - ``EmailSender``          — sends OTP emails
      - ``jwt_secret``           — HS256 signing key for JWTs
    """

    def __init__(
        self,
        redis: aioredis.Redis,
        apple_verifier: AppleTokenVerifier,
        email_sender: EmailSender,
        jwt_secret: str,
    ) -> None:
        self._redis = redis
        self._apple = apple_verifier
        self._email = email_sender
        self._jwt_secret = jwt_secret

    # -- Apple Sign-In (Requirements 1.1–1.4) ------------------------------

    async def apple_sign_in(
        self,
        session: AsyncSession,
        identity_token: str,
    ) -> AuthResult:
        """
        Verify Apple token, create-or-fetch player, create CASH wallet,
        return JWT.

        Raises:
          - ``InvalidToken``         if the token is invalid/expired
          - ``UpstreamUnavailable``  if Apple servers are unreachable
        """
        # 1. Verify token → extract apple_sub
        apple_sub = await self._apple.verify(identity_token)

        # 2. Create-or-fetch player
        is_new = False
        stmt = select(Player).where(Player.apple_sub == apple_sub)
        result = await session.execute(stmt)
        player = result.scalar_one_or_none()

        if player is None:
            player = Player(apple_sub=apple_sub)
            session.add(player)
            await session.flush()
            await self._ensure_cash_wallet(session, player.id)
            is_new = True

        # 3. Issue JWT
        token = _create_jwt(player.id, self._jwt_secret)
        return AuthResult(player_id=player.id, jwt_token=token, is_new_player=is_new)

    # -- Email OTP (Requirements 2.1–2.6) ----------------------------------

    async def request_otp(self, email: str) -> None:
        """
        Generate a 6-digit OTP, store in Redis with 600s TTL, enforce
        rate limit (5 per 15 min), and send via email.

        Raises:
          - ``RateLimited`` if >5 requests in 15-minute window
        """
        # 1. Rate-limit check
        rate_key = _rate_key(email)
        current_count = await self._redis.get(rate_key)
        if current_count is not None and int(current_count) >= RATE_LIMIT:
            raise RateLimited(
                "Too many OTP requests. Please wait before trying again."
            )

        # 2. Generate 6-digit code
        code = f"{secrets.randbelow(1_000_000):06d}"

        # 3. Store in Redis with TTL
        await self._redis.set(_otp_key(email), code, ex=OTP_TTL)

        # 4. Increment rate counter
        pipe = self._redis.pipeline(transaction=True)
        pipe.incr(rate_key)
        pipe.expire(rate_key, RATE_WINDOW)
        await pipe.execute()

        # 5. Send email
        await self._email.send_otp(email, code)

    async def verify_otp(
        self,
        session: AsyncSession,
        email: str,
        code: str,
    ) -> AuthResult:
        """
        Validate OTP, create-or-fetch player, create CASH wallet, return JWT.

        Raises:
          - ``OTPExpired``  if the OTP key no longer exists in Redis
          - ``InvalidOTP``  if the submitted code doesn't match
        """
        # 1. Retrieve stored OTP
        otp_key = _otp_key(email)
        stored = await self._redis.get(otp_key)

        if stored is None:
            raise OTPExpired("OTP has expired. Please request a new code.")

        stored_code = stored.decode("utf-8") if isinstance(stored, bytes) else stored

        if stored_code != code:
            raise InvalidOTP("The OTP code is incorrect.")

        # 2. Delete OTP after successful verification
        await self._redis.delete(otp_key)

        # 3. Create-or-fetch player
        is_new = False
        stmt = select(Player).where(Player.email == email)
        result = await session.execute(stmt)
        player = result.scalar_one_or_none()

        if player is None:
            player = Player(email=email)
            session.add(player)
            await session.flush()
            await self._ensure_cash_wallet(session, player.id)
            is_new = True

        # 4. Issue JWT
        token = _create_jwt(player.id, self._jwt_secret)
        return AuthResult(player_id=player.id, jwt_token=token, is_new_player=is_new)

    # -- Age Gate (Requirements 3.1–3.3) -----------------------------------

    async def confirm_age(
        self,
        session: AsyncSession,
        player_id: UUID,
        confirmed: bool,
    ) -> None:
        """
        Persist age confirmation on the player record.

        Raises:
          - ``AgeRequired`` if the player declines (confirmed=False)
        """
        if not confirmed:
            raise AgeRequired(
                "You must confirm you are 18 years or older to play."
            )

        stmt = select(Player).where(Player.id == player_id).with_for_update()
        result = await session.execute(stmt)
        player = result.scalar_one()

        player.age_confirmed = True
        player.updated_at = datetime.now(timezone.utc)

    # -- Internal helpers ---------------------------------------------------

    @staticmethod
    async def _ensure_cash_wallet(
        session: AsyncSession,
        player_id: UUID,
    ) -> Wallet:
        """Create a CASH wallet with zero balance if one doesn't exist."""
        stmt = select(Wallet).where(
            Wallet.owner_type == OwnerType.PLAYER,
            Wallet.owner_id == player_id,
            Wallet.currency == Currency.CASH,
        )
        result = await session.execute(stmt)
        wallet = result.scalar_one_or_none()
        if wallet is not None:
            return wallet

        wallet = Wallet(
            owner_type=OwnerType.PLAYER,
            owner_id=player_id,
            currency=Currency.CASH,
            balance=0,
            reserved_balance=0,
            is_active=True,
        )
        session.add(wallet)
        await session.flush()
        return wallet
