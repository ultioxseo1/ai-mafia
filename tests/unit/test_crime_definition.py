"""
Unit tests for CrimeDefinition frozen dataclass and loader function.

Validates: Requirement 6.1
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.api_fastapi.domain.models.crime import (
    CrimeDefinition,
    load_crime_definitions,
)


# ---------------------------------------------------------------------------
# CrimeDefinition dataclass tests
# ---------------------------------------------------------------------------


def test_crime_definition_fields():
    """CrimeDefinition stores all required fields."""
    crime = CrimeDefinition(
        crime_id="pickpocket",
        name="Pickpocket",
        nerve_cost=2,
        cash_min=10,
        cash_max=50,
        xp_reward=5,
        heat_increase=3,
    )
    assert crime.crime_id == "pickpocket"
    assert crime.name == "Pickpocket"
    assert crime.nerve_cost == 2
    assert crime.cash_min == 10
    assert crime.cash_max == 50
    assert crime.xp_reward == 5
    assert crime.heat_increase == 3


def test_crime_definition_is_frozen():
    """CrimeDefinition instances are immutable."""
    crime = CrimeDefinition(
        crime_id="heist",
        name="Heist",
        nerve_cost=10,
        cash_min=200,
        cash_max=1000,
        xp_reward=40,
        heat_increase=20,
    )
    with pytest.raises(AttributeError):
        crime.nerve_cost = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# load_crime_definitions tests
# ---------------------------------------------------------------------------

SAMPLE_CRIMES_JSON = [
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
]


@pytest.mark.asyncio
async def test_load_crime_definitions_returns_three_crimes():
    """Loader reads exactly 3 crime definitions from ConfigService."""
    config = MagicMock()
    config.get_json = AsyncMock(return_value=SAMPLE_CRIMES_JSON)

    crimes = await load_crime_definitions(config)

    assert len(crimes) == 3
    assert all(isinstance(c, CrimeDefinition) for c in crimes)


@pytest.mark.asyncio
async def test_load_crime_definitions_correct_values():
    """Loader maps JSON fields to CrimeDefinition attributes correctly."""
    config = MagicMock()
    config.get_json = AsyncMock(return_value=SAMPLE_CRIMES_JSON)

    crimes = await load_crime_definitions(config)

    assert crimes[0].crime_id == "pickpocket"
    assert crimes[1].crime_id == "shakedown"
    assert crimes[2].crime_id == "heist"
    assert crimes[2].cash_max == 1000


@pytest.mark.asyncio
async def test_load_crime_definitions_empty_config():
    """Loader returns empty list when config has no crime definitions."""
    config = MagicMock()
    config.get_json = AsyncMock(return_value=[])

    crimes = await load_crime_definitions(config)

    assert crimes == []
