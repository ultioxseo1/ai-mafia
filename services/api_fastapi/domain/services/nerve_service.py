"""
services/api_fastapi/domain/services/nerve_service.py

AI MAFIA — Nerve (Energy) Service

Manages Nerve regeneration, consumption, restoration, and cap updates
backed by Redis. Uses lazy regeneration: nerve is computed on-demand as
``min(stored + floor((now - last_update) / regen_interval), cap)`` rather
than via a background tick process.

Consumption is performed atomically via a Redis Lua script to prevent
race conditions on concurrent requests.

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from uuid import UUID

import redis.asyncio as aioredis

from services.api_fastapi.domain.services.config_service import (
    NERVE_DEFAULT_CAP,
    NERVE_REGEN_INTERVAL,
    ConfigService,
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NerveState:
    """Snapshot of a player's nerve after lazy regeneration."""

    current: int
    max_nerve: int
    next_regen_at: float | None  # epoch timestamp, or None if at cap


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class InsufficientNerve(Exception):
    """Raised when a player lacks enough nerve for the requested action."""


# ---------------------------------------------------------------------------
# Lua scripts
# ---------------------------------------------------------------------------

# Atomic consume: regenerate, check, deduct — all in one round-trip.
#   KEYS[1] = nerve:{player_id}
#   ARGV[1] = cost
#   ARGV[2] = now_ts  (float seconds)
#   ARGV[3] = regen_interval (int seconds)
# Returns the new nerve value, or -1 if insufficient.
_CONSUME_LUA = """
local val      = tonumber(redis.call('HGET', KEYS[1], 'value'))
local last     = tonumber(redis.call('HGET', KEYS[1], 'last_update'))
local cap      = tonumber(redis.call('HGET', KEYS[1], 'cap'))
local now      = tonumber(ARGV[2])
local interval = tonumber(ARGV[3])

local regen   = math.floor((now - last) / interval)
local current = math.min(val + regen, cap)
local cost    = tonumber(ARGV[1])

if current < cost then return -1 end

local new_val = current - cost
redis.call('HSET', KEYS[1], 'value', new_val, 'last_update', now)
return new_val
"""

# Atomic restore: regenerate first, then add amount (capped).
#   KEYS[1] = nerve:{player_id}
#   ARGV[1] = amount
#   ARGV[2] = now_ts
#   ARGV[3] = regen_interval
# Returns the new nerve value.
_RESTORE_LUA = """
local val      = tonumber(redis.call('HGET', KEYS[1], 'value'))
local last     = tonumber(redis.call('HGET', KEYS[1], 'last_update'))
local cap      = tonumber(redis.call('HGET', KEYS[1], 'cap'))
local now      = tonumber(ARGV[2])
local interval = tonumber(ARGV[3])

local regen   = math.floor((now - last) / interval)
local current = math.min(val + regen, cap)

local restored = math.min(current + tonumber(ARGV[1]), cap)
redis.call('HSET', KEYS[1], 'value', restored, 'last_update', now)
return restored
"""


def _redis_key(player_id: UUID) -> str:
    return f"nerve:{player_id}"


# ---------------------------------------------------------------------------
# Helper — compute NerveState from raw hash values
# ---------------------------------------------------------------------------

def compute_nerve(
    stored_value: int,
    last_update: float,
    cap: int,
    now: float,
    regen_interval: int,
) -> NerveState:
    """
    Pure function: apply lazy regeneration formula.

    current = min(stored + floor((now - last_update) / regen_interval), cap)

    This is intentionally a module-level function so it can be tested
    without Redis (Property 8).
    """
    elapsed = now - last_update
    regen_ticks = max(int(math.floor(elapsed / regen_interval)), 0)
    current = min(stored_value + regen_ticks, cap)

    if current >= cap:
        next_regen_at = None
    else:
        # Time of the *next* +1 tick after the last update that was recorded.
        # We need to figure out when the next tick fires relative to now.
        ticks_used = current - stored_value  # ticks already applied
        next_tick_time = last_update + (ticks_used + 1) * regen_interval
        next_regen_at = next_tick_time

    return NerveState(current=current, max_nerve=cap, next_regen_at=next_regen_at)


# ---------------------------------------------------------------------------
# NerveService
# ---------------------------------------------------------------------------

class NerveService:
    """
    Manages per-player Nerve backed by Redis.

    Redis key ``nerve:{player_id}`` stores a hash with:
      - ``value``       — last-persisted nerve count
      - ``last_update`` — epoch timestamp of last write
      - ``cap``         — current nerve cap (rank-dependent)
    """

    def __init__(self, redis: aioredis.Redis, config: ConfigService) -> None:
        self._redis = redis
        self._config = config

    # -- Internal helpers ---------------------------------------------------

    async def _regen_interval(self) -> int:
        return await self._config.get_int(NERVE_REGEN_INTERVAL, default=180)

    async def _default_cap(self) -> int:
        return await self._config.get_int(NERVE_DEFAULT_CAP, default=50)

    async def _ensure_initialised(self, player_id: UUID) -> None:
        """
        Lazily initialise nerve state on first access.

        Uses HSETNX so concurrent first-access calls are safe — only the
        first one writes.
        """
        key = _redis_key(player_id)
        exists = await self._redis.exists(key)
        if not exists:
            cap = await self._default_cap()
            now = time.time()
            # HSETNX per field avoids overwriting if another request raced us.
            pipe = self._redis.pipeline(transaction=True)
            pipe.hsetnx(key, "value", cap)
            pipe.hsetnx(key, "last_update", now)
            pipe.hsetnx(key, "cap", cap)
            await pipe.execute()

    # -- Public API ---------------------------------------------------------

    async def get_nerve(self, player_id: UUID) -> NerveState:
        """
        Return the player's current nerve after applying lazy regeneration.

        Requirement 5.2: +1 every ``NERVE_REGEN_INTERVAL`` seconds.
        Requirement 5.3: regenerate until cap.
        Requirement 5.4: stop at cap.
        """
        await self._ensure_initialised(player_id)

        key = _redis_key(player_id)
        raw = await self._redis.hgetall(key)

        stored_value = int(raw[b"value"])
        last_update = float(raw[b"last_update"])
        cap = int(raw[b"cap"])
        interval = await self._regen_interval()

        return compute_nerve(stored_value, last_update, cap, time.time(), interval)

    async def consume_nerve(self, player_id: UUID, amount: int) -> NerveState:
        """
        Atomically deduct *amount* nerve.

        Uses a Lua script so the read-check-write is a single Redis
        operation — no TOCTOU race.

        Requirement 5.6: atomic decrement.
        Requirement 5.7: reject if insufficient.

        Returns the updated ``NerveState`` on success.
        Raises ``InsufficientNerve`` if the player cannot afford the cost.
        """
        if amount <= 0:
            raise ValueError("amount must be > 0")

        await self._ensure_initialised(player_id)

        key = _redis_key(player_id)
        interval = await self._regen_interval()
        now = time.time()

        result = await self._redis.eval(
            _CONSUME_LUA,
            1,
            key,
            str(amount),
            str(now),
            str(interval),
        )

        new_value = int(result)
        if new_value < 0:
            raise InsufficientNerve(
                f"Need {amount} nerve but player {player_id} has insufficient nerve."
            )

        # Read cap back for the response (Lua already wrote the new value).
        cap = int(await self._redis.hget(key, "cap"))

        if new_value >= cap:
            next_regen_at = None
        else:
            next_regen_at = now + interval

        return NerveState(current=new_value, max_nerve=cap, next_regen_at=next_regen_at)

    async def restore_nerve(self, player_id: UUID, amount: int) -> None:
        """
        Compensating action: give back *amount* nerve (capped).

        Called when a downstream transaction (e.g. PG commit) fails after
        nerve was already consumed.
        """
        if amount <= 0:
            raise ValueError("amount must be > 0")

        await self._ensure_initialised(player_id)

        key = _redis_key(player_id)
        interval = await self._regen_interval()
        now = time.time()

        await self._redis.eval(
            _RESTORE_LUA,
            1,
            key,
            str(amount),
            str(now),
            str(interval),
        )

    async def update_cap(self, player_id: UUID, new_cap: int) -> None:
        """
        Update the nerve cap — typically on rank promotion.

        Requirement 5.5: new cap allows further regeneration.
        """
        if new_cap <= 0:
            raise ValueError("new_cap must be > 0")

        await self._ensure_initialised(player_id)

        key = _redis_key(player_id)
        await self._redis.hset(key, "cap", new_cap)
