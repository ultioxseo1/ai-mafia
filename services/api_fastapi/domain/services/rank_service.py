"""
services/api_fastapi/domain/services/rank_service.py

AI MAFIA — Rank & XP Service

Manages XP accumulation and rank promotion using the locked rank table.
``compute_rank`` is a pure module-level function for easy property testing.
``award_xp`` orchestrates XP addition, rank promotion detection, and nerve
cap updates within a single DB transaction.

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api_fastapi.domain.models.player import Player
from services.api_fastapi.domain.services.nerve_service import NerveService


# ---------------------------------------------------------------------------
# Locked rank table  (name, xp_threshold, nerve_cap)
# ---------------------------------------------------------------------------

RANK_TABLE: list[Tuple[str, int, int]] = [
    ("Empty-Suit",  0,         50),
    ("Runner",      1_000,     75),
    ("Enforcer",    5_000,     100),
    ("Capo",        25_000,    150),
    ("Fixer",       100_000,   200),
    ("Underboss",   500_000,   250),
    ("Godfather",   2_000_000, 300),
]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RankResult:
    """Outcome of an ``award_xp`` call."""

    rank_name: str
    nerve_cap: int
    total_xp: int
    promoted: bool


# ---------------------------------------------------------------------------
# Pure function — rank computation
# ---------------------------------------------------------------------------

def compute_rank(total_xp: int) -> Tuple[str, int]:
    """
    Given cumulative XP, return ``(rank_name, nerve_cap)`` for the highest
    rank whose threshold is ≤ *total_xp*.

    Supports multi-rank jumps: a single large XP award that skips
    intermediate ranks lands on the correct final rank.

    The table is iterated in order (ascending thresholds) and the last
    matching entry wins.
    """
    rank_name = RANK_TABLE[0][0]
    nerve_cap = RANK_TABLE[0][2]

    for name, threshold, cap in RANK_TABLE:
        if total_xp >= threshold:
            rank_name = name
            nerve_cap = cap
        else:
            break

    return rank_name, nerve_cap


# ---------------------------------------------------------------------------
# RankService
# ---------------------------------------------------------------------------

class RankService:
    """
    Manages XP accumulation and rank promotion.

    ``award_xp`` is designed to run inside an existing DB transaction
    (the caller owns the ``session``).  Rank changes are persisted in the
    same transaction as the XP update (Requirement 8.6).
    """

    def __init__(self, nerve_service: NerveService) -> None:
        self._nerve = nerve_service

    async def award_xp(
        self,
        session: AsyncSession,
        player_id: UUID,
        xp: int,
        idem_key: str,
    ) -> RankResult:
        """
        Add *xp* to the player's cumulative total, check for rank promotion
        (including multi-rank jumps), and update the nerve cap if promoted.

        Steps:
          1. Lock the player row (SELECT FOR UPDATE)
          2. Increase ``player.xp`` by *xp*
          3. Compute the new rank from the updated total
          4. If rank changed, update ``player.rank`` and call
             ``NerveService.update_cap()``
          5. All DB mutations happen within the caller's transaction

        Raises ``ValueError`` if *xp* is not positive.
        """
        if xp <= 0:
            raise ValueError("xp must be > 0")

        # 1. Lock the player row
        stmt = (
            select(Player)
            .where(Player.id == player_id)
            .with_for_update()
        )
        result = await session.execute(stmt)
        player = result.scalar_one()

        # 2. Add XP (cumulative, only increases — Requirement 8.4)
        old_rank = player.rank
        player.xp += xp

        # 3. Compute new rank from total XP
        new_rank, nerve_cap = compute_rank(player.xp)

        # 4. Promote if rank changed
        promoted = new_rank != old_rank
        if promoted:
            player.rank = new_rank
            await self._nerve.update_cap(player_id, nerve_cap)

        return RankResult(
            rank_name=new_rank,
            nerve_cap=nerve_cap,
            total_xp=player.xp,
            promoted=promoted,
        )
