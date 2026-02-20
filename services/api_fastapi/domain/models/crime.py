"""
services/api_fastapi/domain/models/crime.py

AI MAFIA — Crime Definition Model

Frozen dataclass representing a PvE crime definition loaded from
ConfigService. Not a DB model — crime definitions live in config
(env vars / Redis) and are read at runtime.

Requirements: 6.1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from services.api_fastapi.domain.services.config_service import (
    CRIME_DEFINITIONS,
    ConfigService,
)


@dataclass(frozen=True)
class CrimeDefinition:
    """An immutable PvE crime definition loaded from configuration."""

    crime_id: str           # e.g. "pickpocket", "shakedown", "heist"
    name: str
    nerve_cost: int
    cash_min: int
    cash_max: int
    xp_reward: int
    heat_increase: int


async def load_crime_definitions(config: ConfigService) -> List[CrimeDefinition]:
    """
    Read crime definitions from ConfigService and return a list of
    ``CrimeDefinition`` instances.

    The config value under ``CRIME_DEFINITIONS`` is a JSON array of objects,
    each containing the fields required by ``CrimeDefinition``.
    """
    raw: list[dict] = await config.get_json(CRIME_DEFINITIONS, default=[])
    return [CrimeDefinition(**entry) for entry in raw]
