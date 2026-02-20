"""
services/api_fastapi/domain/services/crime_service.py

AI MAFIA — Crime Service

Orchestrates PvE crime execution: spend Nerve → earn CASH + XP → gain
Heat → (maybe) rank up.  Uses a compensation pattern for Redis/PG
consistency: Nerve is consumed in Redis first; if the subsequent PG
transaction fails, Nerve is restored via a compensating Redis write.

Idempotency is handled at two levels:
  - CrimeService checks a Redis cache for fast replay of the full
    CrimeResult.
  - The Ledger ``earn()`` call has its own DB-level idempotency.

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api_fastapi.domain.models.crime import (
    CrimeDefinition,
    load_crime_definitions,
)
from services.api_fastapi.domain.models.economy import Currency, OwnerType
from services.api_fastapi.domain.models.family import FamilyMember
from services.api_fastapi.domain.services.config_service import ConfigService
from services.api_fastapi.domain.services.heat_service import HeatService
from services.api_fastapi.domain.services.ledger_service import earn
from services.api_fastapi.domain.services.nerve_service import NerveService
from services.api_fastapi.domain.services.rank_service import RankService
from services.api_fastapi.domain.services.vault_service import FamilyVaultService


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CrimeNotFound(Exception):
    """Raised when the requested crime_id does not match any definition."""


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CrimeResult:
    """Outcome of a successful crime execution."""

    crime_id: str
    cash_earned: int
    xp_earned: int
    heat_added: int
    new_rank: Optional[str]
    promoted: bool
    nerve_remaining: int


# ---------------------------------------------------------------------------
# Redis idempotency helpers
# ---------------------------------------------------------------------------

_IDEM_PREFIX = "crime_idem:"
_IDEM_TTL = 86_400  # 24 hours


def _idem_key(player_id: UUID, idempotency_key: str) -> str:
    return f"{_IDEM_PREFIX}{player_id}:{idempotency_key}"


# ---------------------------------------------------------------------------
# CrimeService
# ---------------------------------------------------------------------------


class CrimeService:
    """
    Orchestrates PvE crime execution.

    Dependencies are injected via the constructor so the router can wire
    them from the application's dependency container.
    """

    def __init__(
        self,
        redis: aioredis.Redis,
        config: ConfigService,
        nerve_service: NerveService,
        heat_service: HeatService,
        rank_service: RankService,
        vault_service: Optional[FamilyVaultService] = None,
    ) -> None:
        self._redis = redis
        self._config = config
        self._nerve = nerve_service
        self._heat = heat_service
        self._rank = rank_service
        self._vault = vault_service

    # -- Public API ---------------------------------------------------------

    async def execute_crime(
        self,
        session: AsyncSession,
        player_id: UUID,
        crime_id: str,
        idempotency_key: str,
    ) -> CrimeResult:
        """
        Full crime execution pipeline:

        1. Look up crime definition by *crime_id*
        2. Check idempotency — return cached ``CrimeResult`` on replay
        3. Consume nerve (Redis, atomic)
        4. Calculate random CASH reward in [cash_min, cash_max]
        5. PG transaction (caller-owned session):
           a. Ledger ``earn()`` — credit CASH
           b. ``RankService.award_xp()`` — add XP, check promotion
        6. On PG success: add heat (Redis)
        7. On PG failure: restore nerve (Redis compensation)
        8. Store idempotency result (Redis)
        9. Return ``CrimeResult``

        Raises:
          - ``CrimeNotFound`` if *crime_id* is invalid
          - ``InsufficientNerve`` (from NerveService) if not enough nerve
        """
        # 1. Look up crime definition
        crime = await self._find_crime(crime_id)

        # 2. Idempotency check (fast Redis cache)
        cached = await self._get_cached_result(player_id, idempotency_key)
        if cached is not None:
            return cached

        # 3. Consume nerve (Redis — before PG transaction)
        nerve_state = await self._nerve.consume_nerve(player_id, crime.nerve_cost)

        # 4. Calculate CASH reward
        cash_reward = random.randint(crime.cash_min, crime.cash_max)

        try:
            # 5. PG transaction: ledger earn + XP award
            # 5a. Credit CASH via ledger (with vault tax if in a family)
            if self._vault is not None:
                membership = await self._get_family_membership(
                    session, player_id,
                )
            else:
                membership = None

            if membership is not None and self._vault is not None:
                # Player is in a family — route through vault tax
                await self._vault.earn_with_tax(
                    session,
                    player_id=player_id,
                    family_id=membership.family_id,
                    gross_amount=cash_reward,
                    idempotency_key=idempotency_key,
                )
            else:
                # Not in a family or no vault service — direct earn
                await earn(
                    session,
                    owner_type=OwnerType.PLAYER,
                    owner_id=player_id,
                    currency=Currency.CASH,
                    amount=cash_reward,
                    reference_id=f"crime:{crime_id}:{idempotency_key}",
                    metadata={"crime_id": crime_id, "idempotency_key": idempotency_key},
                    idempotency_key=idempotency_key,
                )

            # 5b. Award XP (may trigger rank promotion)
            rank_result = await self._rank.award_xp(
                session,
                player_id,
                crime.xp_reward,
                idempotency_key,
            )

            # Flush to ensure PG writes are consistent before we proceed
            await session.flush()

        except Exception:
            # 7. On PG failure: restore nerve (compensation)
            await self._nerve.restore_nerve(player_id, crime.nerve_cost)
            raise

        # 6. Add heat (Redis — after PG success)
        await self._heat.add_heat(player_id, crime.heat_increase)

        # Build result
        result = CrimeResult(
            crime_id=crime_id,
            cash_earned=cash_reward,
            xp_earned=crime.xp_reward,
            heat_added=crime.heat_increase,
            new_rank=rank_result.rank_name if rank_result.promoted else None,
            promoted=rank_result.promoted,
            nerve_remaining=nerve_state.current,
        )

        # 8. Store idempotency result (Redis cache)
        await self._store_cached_result(player_id, idempotency_key, result)

        return result

    async def list_crimes(self) -> List[CrimeDefinition]:
        """Return all configured crime definitions."""
        return await load_crime_definitions(self._config)

    # -- Internal helpers ---------------------------------------------------

    async def _find_crime(self, crime_id: str) -> CrimeDefinition:
        """Look up a crime definition by ID, or raise ``CrimeNotFound``."""
        crimes = await load_crime_definitions(self._config)
        for crime in crimes:
            if crime.crime_id == crime_id:
                return crime
        raise CrimeNotFound(f"Crime '{crime_id}' not found.")

    async def _get_cached_result(
        self, player_id: UUID, idempotency_key: str
    ) -> Optional[CrimeResult]:
        """Return a cached ``CrimeResult`` if this is a replay, else None."""
        raw = await self._redis.get(_idem_key(player_id, idempotency_key))
        if raw is None:
            return None
        data: Dict = json.loads(
            raw.decode("utf-8") if isinstance(raw, bytes) else raw
        )
        return CrimeResult(**data)

    async def _store_cached_result(
        self, player_id: UUID, idempotency_key: str, result: CrimeResult
    ) -> None:
        """Cache the ``CrimeResult`` in Redis for fast idempotency replay."""
        payload = json.dumps(asdict(result))
        await self._redis.set(
            _idem_key(player_id, idempotency_key),
            payload,
            ex=_IDEM_TTL,
        )

    @staticmethod
    async def _get_family_membership(
        session: AsyncSession, player_id: UUID,
    ) -> Optional[FamilyMember]:
        """Return the player's FamilyMember record, or None if not in a family."""
        stmt = select(FamilyMember).where(FamilyMember.player_id == player_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
