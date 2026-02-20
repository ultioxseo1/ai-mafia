"""
services/api_fastapi/domain/services/heat_service.py

AI MAFIA — Heat Service

Manages per-player Heat backed by Redis.  Heat increases when crimes are
committed and decays lazily over time.  Uses the same lazy-computation
pattern as NerveService: heat is computed on-demand as
``max(stored - floor((now - last_update) / decay_interval), 0)`` rather
than via a background tick process.

Adding heat is performed atomically via a Redis Lua script to prevent
race conditions on concurrent requests.  The value is always clamped to
the [0, 100] range.

Requirements: 7.1, 7.2, 7.4, 7.5
"""

from __future__ import annotations

import math
import time
from uuid import UUID

import redis.asyncio as aioredis

from services.api_fastapi.domain.services.config_service import (
    HEAT_DECAY_INTERVAL,
    ConfigService,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HEAT_MAX = 100
HEAT_MIN = 0


# ---------------------------------------------------------------------------
# Lua scripts
# ---------------------------------------------------------------------------

# Atomic add-heat: apply lazy decay first, then add amount (clamped at 100).
#   KEYS[1] = heat:{player_id}
#   ARGV[1] = amount to add
#   ARGV[2] = now_ts  (float seconds)
#   ARGV[3] = decay_interval (int seconds)
# Returns the new heat value after add + clamp.
_ADD_HEAT_LUA = """
local val      = tonumber(redis.call('HGET', KEYS[1], 'value'))
local last     = tonumber(redis.call('HGET', KEYS[1], 'last_update'))
local now      = tonumber(ARGV[2])
local interval = tonumber(ARGV[3])

local decay   = math.floor((now - last) / interval)
local current = math.max(val - decay, 0)

local added   = math.min(current + tonumber(ARGV[1]), 100)
redis.call('HSET', KEYS[1], 'value', added, 'last_update', now)
return added
"""


def _redis_key(player_id: UUID) -> str:
    return f"heat:{player_id}"


# ---------------------------------------------------------------------------
# Pure helper — compute heat from raw values (for property testing)
# ---------------------------------------------------------------------------

def compute_heat(
    stored_value: int,
    last_update: float,
    now: float,
    decay_interval: int,
) -> int:
    """
    Pure function: apply lazy decay formula.

    current = max(stored - floor((now - last_update) / decay_interval), 0)

    This is intentionally a module-level function so it can be tested
    without Redis (Property 15).
    """
    elapsed = now - last_update
    decay_ticks = max(int(math.floor(elapsed / decay_interval)), 0)
    return max(stored_value - decay_ticks, HEAT_MIN)


# ---------------------------------------------------------------------------
# HeatService
# ---------------------------------------------------------------------------

class HeatService:
    """
    Manages per-player Heat backed by Redis.

    Redis key ``heat:{player_id}`` stores a hash with:
      - ``value``       — last-persisted heat count
      - ``last_update`` — epoch timestamp of last write

    Heat is an integer in [0, 100].  It increases when crimes are committed
    and decays by -1 every ``HEAT_DECAY_INTERVAL`` seconds.
    """

    def __init__(self, redis: aioredis.Redis, config: ConfigService) -> None:
        self._redis = redis
        self._config = config

    # -- Internal helpers ---------------------------------------------------

    async def _decay_interval(self) -> int:
        return await self._config.get_int(HEAT_DECAY_INTERVAL, default=300)

    async def _ensure_initialised(self, player_id: UUID) -> None:
        """
        Lazily initialise heat state on first access.

        Uses HSETNX so concurrent first-access calls are safe — only the
        first one writes.
        """
        key = _redis_key(player_id)
        exists = await self._redis.exists(key)
        if not exists:
            now = time.time()
            pipe = self._redis.pipeline(transaction=True)
            pipe.hsetnx(key, "value", 0)
            pipe.hsetnx(key, "last_update", now)
            await pipe.execute()

    # -- Public API ---------------------------------------------------------

    async def get_heat(self, player_id: UUID) -> int:
        """
        Return the player's current heat after applying lazy decay.

        Requirement 7.4: -1 every ``HEAT_DECAY_INTERVAL`` seconds.
        Requirement 7.1: heat is always in [0, 100].
        """
        await self._ensure_initialised(player_id)

        key = _redis_key(player_id)
        raw = await self._redis.hgetall(key)

        stored_value = int(raw[b"value"])
        last_update = float(raw[b"last_update"])
        interval = await self._decay_interval()

        return compute_heat(stored_value, last_update, time.time(), interval)

    async def add_heat(self, player_id: UUID, amount: int) -> int:
        """
        Atomically add heat, clamp at 100.

        Uses a Lua script so the read-decay-add-write is a single Redis
        operation — no TOCTOU race.

        Requirement 7.2: add crime's heat value, capped at 100.
        Requirement 7.5: clamp at 100.

        Returns the new heat value after the addition.
        """
        if amount < 0:
            raise ValueError("amount must be >= 0")

        await self._ensure_initialised(player_id)

        key = _redis_key(player_id)
        interval = await self._decay_interval()
        now = time.time()

        result = await self._redis.eval(
            _ADD_HEAT_LUA,
            1,
            key,
            str(amount),
            str(now),
            str(interval),
        )

        return int(result)
