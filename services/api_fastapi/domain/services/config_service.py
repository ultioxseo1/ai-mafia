"""
services/api_fastapi/domain/services/config_service.py

AI MAFIA — Configuration Service

Provides runtime-tunable game constants via a two-tier lookup:
  1. Redis override  (key: config:{KEY}) — hot-reload without restart
  2. Environment variable fallback       — 12-factor base config

All game constants are defined as keys with sensible defaults so the
system boots cleanly even when neither Redis nor env vars are set.

Requirements: 12.1, 12.2
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import redis.asyncio as aioredis


# ---------------------------------------------------------------------------
# Game constant keys and their defaults
# ---------------------------------------------------------------------------

# Nerve
NERVE_REGEN_INTERVAL = "NERVE_REGEN_INTERVAL"          # seconds between +1 nerve (default 180)
NERVE_DEFAULT_CAP = "NERVE_DEFAULT_CAP"                 # starting nerve cap (default 50)

# Heat
HEAT_DECAY_INTERVAL = "HEAT_DECAY_INTERVAL"             # seconds between -1 heat (default 300)

# Crime definitions (JSON array)
CRIME_DEFINITIONS = "CRIME_DEFINITIONS"

# Rank table (JSON array of [name, xp_threshold, nerve_cap])
RANK_TABLE = "RANK_TABLE"

# Reconciliation
RECONCILIATION_SCHEDULE = "RECONCILIATION_SCHEDULE"     # cron expression (default "0 4 * * *")

# Alert channel for reconciliation mismatches
RECONCILIATION_ALERT_CHANNEL = "RECONCILIATION_ALERT_CHANNEL"

# ---------------------------------------------------------------------------
# Milestone 2 — Syndicate & Social
# ---------------------------------------------------------------------------

# Family
VAULT_TAX_RATE = "VAULT_TAX_RATE"                       # percent (default 10)
MAX_FAMILY_MEMBERS = "MAX_FAMILY_MEMBERS"                # default 25
MAX_CAPO_COUNT = "MAX_CAPO_COUNT"                        # default 3

# Property
PROPERTY_DEFINITIONS = "PROPERTY_DEFINITIONS"            # JSON array

# Income Job
INCOME_JOB_SCHEDULE = "INCOME_JOB_SCHEDULE"              # cron expression (default "0 5 * * *")

# Chat
CHAT_HISTORY_LIMIT = "CHAT_HISTORY_LIMIT"                # default 50
CHAT_HEARTBEAT_INTERVAL = "CHAT_HEARTBEAT_INTERVAL"      # seconds (default 30)

_DEFAULTS: dict[str, str] = {
    NERVE_REGEN_INTERVAL: "180",
    NERVE_DEFAULT_CAP: "50",
    HEAT_DECAY_INTERVAL: "300",
    CRIME_DEFINITIONS: json.dumps([
        {
            "crime_id": "pickpocket",
            "name": "Pickpocket",
            "nerve_cost": 2,
            "cash_min": 10,
            "cash_max": 50,
            "xp_reward": 5,
            "heat_increase": 3,
        },
        {
            "crime_id": "shakedown",
            "name": "Shakedown",
            "nerve_cost": 5,
            "cash_min": 50,
            "cash_max": 200,
            "xp_reward": 15,
            "heat_increase": 8,
        },
        {
            "crime_id": "heist",
            "name": "Heist",
            "nerve_cost": 10,
            "cash_min": 200,
            "cash_max": 1000,
            "xp_reward": 40,
            "heat_increase": 20,
        },
    ]),
    RANK_TABLE: json.dumps([
        ["Empty-Suit", 0, 50],
        ["Runner", 1000, 75],
        ["Enforcer", 5000, 100],
        ["Capo", 25000, 150],
        ["Fixer", 100000, 200],
        ["Underboss", 500000, 250],
        ["Godfather", 2000000, 300],
    ]),
    RECONCILIATION_SCHEDULE: "0 4 * * *",
    RECONCILIATION_ALERT_CHANNEL: "",
    # Milestone 2
    VAULT_TAX_RATE: "10",
    MAX_FAMILY_MEMBERS: "25",
    MAX_CAPO_COUNT: "3",
    PROPERTY_DEFINITIONS: json.dumps([
        {
            "property_id": "speakeasy",
            "name": "Speakeasy",
            "purchase_price": 50000,
            "daily_income": 500,
            "max_level": 10,
        },
        {
            "property_id": "casino",
            "name": "Casino",
            "purchase_price": 200000,
            "daily_income": 2000,
            "max_level": 10,
        },
        {
            "property_id": "docks",
            "name": "Docks",
            "purchase_price": 100000,
            "daily_income": 1000,
            "max_level": 10,
        },
    ]),
    INCOME_JOB_SCHEDULE: "0 5 * * *",
    CHAT_HISTORY_LIMIT: "50",
    CHAT_HEARTBEAT_INTERVAL: "30",
}

REDIS_PREFIX = "config:"


# ---------------------------------------------------------------------------
# ConfigService
# ---------------------------------------------------------------------------

class ConfigService:
    """
    Two-tier configuration: Redis override → env var → built-in default.

    Usage:
        config = ConfigService(redis_client)
        interval = await config.get(NERVE_REGEN_INTERVAL)   # returns str
        interval_int = await config.get_int(NERVE_REGEN_INTERVAL)
        crimes = await config.get_json(CRIME_DEFINITIONS)
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def get(self, key: str) -> Optional[str]:
        """
        Look up a config value:
          1. Redis key  ``config:{key}``
          2. Environment variable ``{key}``
          3. Built-in default from ``_DEFAULTS``

        Returns the raw string value, or ``None`` if the key is unknown
        and not set anywhere.
        """
        # 1. Redis override (hot-reload path — Requirement 12.2)
        value = await self._redis.get(f"{REDIS_PREFIX}{key}")
        if value is not None:
            # redis-py returns bytes by default; handle both
            return value.decode("utf-8") if isinstance(value, bytes) else value

        # 2. Environment variable (12-factor base config — Requirement 12.1)
        env_val = os.environ.get(key)
        if env_val is not None:
            return env_val

        # 3. Built-in default
        return _DEFAULTS.get(key)

    # -- Typed convenience helpers ------------------------------------------

    async def get_int(self, key: str, default: int = 0) -> int:
        """Return the config value as an ``int``."""
        raw = await self.get(key)
        if raw is None:
            return default
        return int(raw)

    async def get_float(self, key: str, default: float = 0.0) -> float:
        """Return the config value as a ``float``."""
        raw = await self.get(key)
        if raw is None:
            return default
        return float(raw)

    async def get_json(self, key: str, default: Any = None) -> Any:
        """Return the config value parsed as JSON."""
        raw = await self.get(key)
        if raw is None:
            return default
        return json.loads(raw)
