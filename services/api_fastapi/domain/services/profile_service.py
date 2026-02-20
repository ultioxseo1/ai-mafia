"""
services/api_fastapi/domain/services/profile_service.py

AI MAFIA — Player Profile Service

Manages player profile CRUD, display name validation, and profile read
aggregation.  The ``get_profile`` method combines data from PostgreSQL
(Player record, Wallet balance) with Redis (Nerve state, Heat) into a
single response.

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api_fastapi.domain.models.economy import (
    Currency,
    IdempotencyKey,
    OwnerType,
    Wallet,
)
from services.api_fastapi.domain.models.player import Player
from services.api_fastapi.domain.services.heat_service import HeatService
from services.api_fastapi.domain.services.nerve_service import NerveService


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DISPLAY_NAME_REGEX = re.compile(r"^[a-zA-Z0-9_]{3,20}$")

_DEFAULT_RANK = "Empty-Suit"
_DEFAULT_XP = 0
_DEFAULT_HEAT = 0
_DEFAULT_NERVE = 50


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class NameTaken(Exception):
    """Display name already in use by another player."""


class InvalidName(Exception):
    """Display name fails validation (length or character rules)."""


# ---------------------------------------------------------------------------
# Response DTOs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlayerProfileResponse:
    """Aggregated profile returned by ``get_profile``."""

    player_id: UUID
    display_name: Optional[str]
    rank: str
    xp: int
    heat: int
    cash_balance: int
    nerve_current: int
    nerve_max: int
    next_regen_at: Optional[float]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fingerprint(payload: Dict[str, Any]) -> str:
    """Stable fingerprint for idempotency conflict detection."""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def validate_display_name(name: str) -> None:
    """
    Validate a display name against the rules:
      - 3–20 characters
      - Only alphanumeric + underscore
      - Regex: ``^[a-zA-Z0-9_]{3,20}$``

    Raises ``InvalidName`` on failure.
    """
    if not _DISPLAY_NAME_REGEX.match(name):
        raise InvalidName(
            "Display name must be 3–20 characters and contain only "
            "letters, digits, and underscores."
        )


async def _idempo_get(
    session: AsyncSession,
    owner_type: OwnerType,
    owner_id: UUID,
    action: str,
    idem_key: str,
) -> Optional[IdempotencyKey]:
    stmt = select(IdempotencyKey).where(
        IdempotencyKey.owner_type == owner_type,
        IdempotencyKey.owner_id == owner_id,
        IdempotencyKey.action == action,
        IdempotencyKey.idempotency_key == idem_key,
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def _idempo_store(
    session: AsyncSession,
    owner_type: OwnerType,
    owner_id: UUID,
    action: str,
    idem_key: str,
    request_fingerprint: str,
    response_body: Dict[str, Any],
) -> None:
    row = IdempotencyKey(
        owner_type=owner_type,
        owner_id=owner_id,
        action=action,
        idempotency_key=idem_key,
        request_fingerprint=request_fingerprint,
        response_body=response_body,
    )
    session.add(row)
    await session.flush()


# ---------------------------------------------------------------------------
# PlayerProfileService
# ---------------------------------------------------------------------------

class PlayerProfileService:
    """
    Manages player profile lifecycle.

    Dependencies:
      - ``AsyncSession`` — passed per-call for DB operations
      - ``NerveService``  — Redis-backed nerve state
      - ``HeatService``   — Redis-backed heat state
    """

    def __init__(self, nerve_service: NerveService, heat_service: HeatService) -> None:
        self._nerve = nerve_service
        self._heat = heat_service

    # -- Public API ---------------------------------------------------------

    async def create_profile(
        self,
        session: AsyncSession,
        player_id: UUID,
    ) -> Player:
        """
        Initialise a new player profile with defaults.

        Defaults (Requirement 4.1):
          - rank = "Empty-Suit"
          - xp   = 0
          - heat  = 0  (Redis, via HeatService lazy init)
          - nerve = 50 (Redis, via NerveService lazy init)

        The Player row is created in the caller's transaction.
        Nerve and Heat are lazily initialised in Redis on first access.
        """
        player = Player(
            id=player_id,
            rank=_DEFAULT_RANK,
            xp=_DEFAULT_XP,
        )
        session.add(player)
        await session.flush()

        # Ensure Redis state is initialised for nerve and heat.
        # NerveService._ensure_initialised sets value=cap=50, which matches
        # the Empty-Suit default.  HeatService._ensure_initialised sets
        # value=0.
        await self._nerve.get_nerve(player_id)
        await self._heat.get_heat(player_id)

        return player

    async def update_display_name(
        self,
        session: AsyncSession,
        player_id: UUID,
        name: str,
        idem_key: str,
    ) -> Player:
        """
        Validate and set the player's display name.

        Validation (Requirement 4.2):
          - 3–20 characters
          - ``^[a-zA-Z0-9_]+$``
          - Unique across all players

        Idempotency (Requirement 4.5):
          - Uses the IdempotencyKey table scoped to
            ``(PLAYER, player_id, "profile.update_name", idem_key)``

        Raises:
          - ``InvalidName``  if the name fails regex/length validation
          - ``NameTaken``    if the name is already used by another player
        """
        # 1. Validate format
        validate_display_name(name)

        # 2. Idempotency check
        action = "profile.update_name"
        payload = {"player_id": str(player_id), "name": name}
        fp = _fingerprint(payload)

        existing = await _idempo_get(
            session, OwnerType.PLAYER, player_id, action, idem_key
        )
        if existing:
            if existing.request_fingerprint != fp:
                from services.api_fastapi.domain.services.ledger_service import (
                    IdempotencyConflict,
                )
                raise IdempotencyConflict(
                    "Idempotency-Key reused with different payload."
                )
            # Replay: return current player state
            stmt = select(Player).where(Player.id == player_id)
            result = await session.execute(stmt)
            return result.scalar_one()

        # 3. Uniqueness check (Requirement 4.3)
        stmt = select(Player).where(
            Player.display_name == name,
            Player.id != player_id,
        )
        result = await session.execute(stmt)
        if result.scalar_one_or_none() is not None:
            raise NameTaken(f"Display name '{name}' is already taken.")

        # 4. Update the player record
        stmt = (
            select(Player)
            .where(Player.id == player_id)
            .with_for_update()
        )
        result = await session.execute(stmt)
        player = result.scalar_one()
        player.display_name = name
        player.updated_at = datetime.utcnow()

        # 5. Store idempotency record
        await _idempo_store(
            session,
            OwnerType.PLAYER,
            player_id,
            action,
            idem_key,
            fp,
            {"player_id": str(player_id), "display_name": name},
        )

        return player

    async def get_profile(
        self,
        session: AsyncSession,
        player_id: UUID,
    ) -> PlayerProfileResponse:
        """
        Aggregate the full player profile from multiple sources.

        Sources (Requirement 4.4):
          - Player record (PG): display_name, rank, xp
          - Wallet balance (PG): CASH balance
          - Nerve state (Redis): current nerve, max, next_regen_at
          - Heat (Redis): current heat value

        Returns a ``PlayerProfileResponse`` with all fields.
        """
        # 1. Player record
        stmt = select(Player).where(Player.id == player_id)
        result = await session.execute(stmt)
        player = result.scalar_one()

        # 2. Wallet balance (CASH)
        wallet_stmt = select(Wallet).where(
            Wallet.owner_type == OwnerType.PLAYER,
            Wallet.owner_id == player_id,
            Wallet.currency == Currency.CASH,
            Wallet.is_active == True,  # noqa: E712
        )
        wallet_result = await session.execute(wallet_stmt)
        wallet = wallet_result.scalar_one_or_none()
        cash_balance = wallet.balance if wallet else 0

        # 3. Nerve state (Redis)
        nerve_state = await self._nerve.get_nerve(player_id)

        # 4. Heat (Redis)
        heat = await self._heat.get_heat(player_id)

        return PlayerProfileResponse(
            player_id=player.id,
            display_name=player.display_name,
            rank=player.rank,
            xp=player.xp,
            heat=heat,
            cash_balance=cash_balance,
            nerve_current=nerve_state.current,
            nerve_max=nerve_state.max_nerve,
            next_regen_at=nerve_state.next_regen_at,
        )
