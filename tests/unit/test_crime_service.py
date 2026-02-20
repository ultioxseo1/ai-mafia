"""
Unit tests for CrimeService — CrimeResult dataclass, CrimeNotFound exception,
list_crimes, and execute_crime orchestration logic.

Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9
"""

from __future__ import annotations

import json
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from services.api_fastapi.domain.models.crime import CrimeDefinition
from services.api_fastapi.domain.services.crime_service import (
    CrimeNotFound,
    CrimeResult,
    CrimeService,
    _IDEM_PREFIX,
)
from services.api_fastapi.domain.services.nerve_service import (
    InsufficientNerve,
    NerveState,
)
from services.api_fastapi.domain.services.rank_service import RankResult


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SAMPLE_CRIMES = [
    CrimeDefinition("pickpocket", "Pickpocket", 2, 10, 50, 5, 3),
    CrimeDefinition("shakedown", "Shakedown", 5, 50, 200, 15, 8),
    CrimeDefinition("heist", "Heist", 10, 200, 1000, 40, 20),
]


def _make_service(
    redis: MagicMock | None = None,
    config: MagicMock | None = None,
    nerve: MagicMock | None = None,
    heat: MagicMock | None = None,
    rank: MagicMock | None = None,
) -> CrimeService:
    redis = redis or MagicMock()
    config = config or MagicMock()
    nerve = nerve or MagicMock()
    heat = heat or MagicMock()
    rank = rank or MagicMock()
    return CrimeService(redis, config, nerve, heat, rank)


# ---------------------------------------------------------------------------
# CrimeResult dataclass tests
# ---------------------------------------------------------------------------


def test_crime_result_fields():
    r = CrimeResult(
        crime_id="pickpocket",
        cash_earned=25,
        xp_earned=5,
        heat_added=3,
        new_rank=None,
        promoted=False,
        nerve_remaining=48,
    )
    assert r.crime_id == "pickpocket"
    assert r.cash_earned == 25
    assert r.xp_earned == 5
    assert r.heat_added == 3
    assert r.new_rank is None
    assert r.promoted is False
    assert r.nerve_remaining == 48


def test_crime_result_with_promotion():
    r = CrimeResult(
        crime_id="heist",
        cash_earned=500,
        xp_earned=40,
        heat_added=20,
        new_rank="Runner",
        promoted=True,
        nerve_remaining=40,
    )
    assert r.promoted is True
    assert r.new_rank == "Runner"


def test_crime_result_is_frozen():
    r = CrimeResult("pickpocket", 25, 5, 3, None, False, 48)
    with pytest.raises(AttributeError):
        r.cash_earned = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CrimeNotFound exception
# ---------------------------------------------------------------------------


def test_crime_not_found_message():
    exc = CrimeNotFound("Crime 'robbery' not found.")
    assert "robbery" in str(exc)


# ---------------------------------------------------------------------------
# list_crimes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_crimes_returns_definitions():
    """list_crimes delegates to load_crime_definitions."""
    config = MagicMock()
    config.get_json = AsyncMock(
        return_value=[asdict(c) for c in SAMPLE_CRIMES]
    )
    svc = _make_service(config=config)

    crimes = await svc.list_crimes()

    assert len(crimes) == 3
    assert crimes[0].crime_id == "pickpocket"
    assert crimes[2].crime_id == "heist"


# ---------------------------------------------------------------------------
# execute_crime — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_crime_happy_path():
    """Successful crime: nerve consumed, CASH earned, XP awarded, heat added."""
    player_id = uuid4()
    idem_key = "test-idem-001"

    # Redis mock — no cached result
    redis_mock = MagicMock()
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.set = AsyncMock()

    # Config mock — return crime definitions
    config_mock = MagicMock()
    config_mock.get_json = AsyncMock(
        return_value=[asdict(c) for c in SAMPLE_CRIMES]
    )

    # Nerve mock
    nerve_mock = MagicMock()
    nerve_mock.consume_nerve = AsyncMock(
        return_value=NerveState(current=48, max_nerve=50, next_regen_at=None)
    )
    nerve_mock.restore_nerve = AsyncMock()

    # Heat mock
    heat_mock = MagicMock()
    heat_mock.add_heat = AsyncMock(return_value=3)

    # Rank mock
    rank_mock = MagicMock()
    rank_mock.award_xp = AsyncMock(
        return_value=RankResult(
            rank_name="Empty-Suit", nerve_cap=50, total_xp=5, promoted=False
        )
    )

    # Session mock
    session_mock = AsyncMock()

    svc = _make_service(
        redis=redis_mock,
        config=config_mock,
        nerve=nerve_mock,
        heat=heat_mock,
        rank=rank_mock,
    )

    with patch(
        "services.api_fastapi.domain.services.crime_service.earn",
        new_callable=AsyncMock,
    ) as earn_mock, patch(
        "services.api_fastapi.domain.services.crime_service.random.randint",
        return_value=25,
    ):
        result = await svc.execute_crime(
            session_mock, player_id, "pickpocket", idem_key
        )

    assert result.crime_id == "pickpocket"
    assert result.cash_earned == 25
    assert result.xp_earned == 5
    assert result.heat_added == 3
    assert result.promoted is False
    assert result.new_rank is None
    assert result.nerve_remaining == 48

    # Verify service calls
    nerve_mock.consume_nerve.assert_awaited_once_with(player_id, 2)
    heat_mock.add_heat.assert_awaited_once_with(player_id, 3)
    rank_mock.award_xp.assert_awaited_once()
    earn_mock.assert_awaited_once()
    nerve_mock.restore_nerve.assert_not_awaited()


# ---------------------------------------------------------------------------
# execute_crime — idempotency replay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_crime_idempotency_replay():
    """Replayed request returns cached CrimeResult without side effects."""
    player_id = uuid4()
    idem_key = "test-idem-replay"

    cached_result = CrimeResult(
        crime_id="pickpocket",
        cash_earned=30,
        xp_earned=5,
        heat_added=3,
        new_rank=None,
        promoted=False,
        nerve_remaining=48,
    )

    redis_mock = MagicMock()
    redis_mock.get = AsyncMock(
        return_value=json.dumps(asdict(cached_result)).encode()
    )

    config_mock = MagicMock()
    config_mock.get_json = AsyncMock(
        return_value=[asdict(c) for c in SAMPLE_CRIMES]
    )

    nerve_mock = MagicMock()
    nerve_mock.consume_nerve = AsyncMock()

    svc = _make_service(redis=redis_mock, config=config_mock, nerve=nerve_mock)

    session_mock = AsyncMock()
    result = await svc.execute_crime(
        session_mock, player_id, "pickpocket", idem_key
    )

    assert result == cached_result
    # No nerve consumed on replay
    nerve_mock.consume_nerve.assert_not_awaited()


# ---------------------------------------------------------------------------
# execute_crime — crime not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_crime_unknown_crime_raises():
    """Unknown crime_id raises CrimeNotFound."""
    redis_mock = MagicMock()
    redis_mock.get = AsyncMock(return_value=None)

    config_mock = MagicMock()
    config_mock.get_json = AsyncMock(
        return_value=[asdict(c) for c in SAMPLE_CRIMES]
    )

    svc = _make_service(redis=redis_mock, config=config_mock)
    session_mock = AsyncMock()

    with pytest.raises(CrimeNotFound):
        await svc.execute_crime(session_mock, uuid4(), "robbery", "idem-1")


# ---------------------------------------------------------------------------
# execute_crime — insufficient nerve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_crime_insufficient_nerve():
    """Insufficient nerve raises InsufficientNerve, no state changes."""
    player_id = uuid4()

    redis_mock = MagicMock()
    redis_mock.get = AsyncMock(return_value=None)

    config_mock = MagicMock()
    config_mock.get_json = AsyncMock(
        return_value=[asdict(c) for c in SAMPLE_CRIMES]
    )

    nerve_mock = MagicMock()
    nerve_mock.consume_nerve = AsyncMock(side_effect=InsufficientNerve("Not enough"))

    heat_mock = MagicMock()
    heat_mock.add_heat = AsyncMock()

    svc = _make_service(
        redis=redis_mock, config=config_mock, nerve=nerve_mock, heat=heat_mock
    )
    session_mock = AsyncMock()

    with pytest.raises(InsufficientNerve):
        await svc.execute_crime(session_mock, player_id, "heist", "idem-2")

    # No heat added, no nerve restored (nerve wasn't consumed)
    heat_mock.add_heat.assert_not_awaited()


# ---------------------------------------------------------------------------
# execute_crime — PG failure triggers nerve restoration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_crime_pg_failure_restores_nerve():
    """If PG transaction fails, nerve is restored (compensation)."""
    player_id = uuid4()

    redis_mock = MagicMock()
    redis_mock.get = AsyncMock(return_value=None)

    config_mock = MagicMock()
    config_mock.get_json = AsyncMock(
        return_value=[asdict(c) for c in SAMPLE_CRIMES]
    )

    nerve_mock = MagicMock()
    nerve_mock.consume_nerve = AsyncMock(
        return_value=NerveState(current=48, max_nerve=50, next_regen_at=None)
    )
    nerve_mock.restore_nerve = AsyncMock()

    heat_mock = MagicMock()
    heat_mock.add_heat = AsyncMock()

    svc = _make_service(
        redis=redis_mock, config=config_mock, nerve=nerve_mock, heat=heat_mock
    )
    session_mock = AsyncMock()

    with patch(
        "services.api_fastapi.domain.services.crime_service.earn",
        new_callable=AsyncMock,
        side_effect=RuntimeError("PG down"),
    ), pytest.raises(RuntimeError, match="PG down"):
        await svc.execute_crime(
            session_mock, player_id, "pickpocket", "idem-3"
        )

    # Nerve should be restored
    nerve_mock.restore_nerve.assert_awaited_once_with(player_id, 2)
    # Heat should NOT be added
    heat_mock.add_heat.assert_not_awaited()


# ---------------------------------------------------------------------------
# execute_crime — promotion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_crime_with_promotion():
    """Crime that triggers rank promotion includes new_rank in result."""
    player_id = uuid4()

    redis_mock = MagicMock()
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.set = AsyncMock()

    config_mock = MagicMock()
    config_mock.get_json = AsyncMock(
        return_value=[asdict(c) for c in SAMPLE_CRIMES]
    )

    nerve_mock = MagicMock()
    nerve_mock.consume_nerve = AsyncMock(
        return_value=NerveState(current=40, max_nerve=50, next_regen_at=None)
    )

    heat_mock = MagicMock()
    heat_mock.add_heat = AsyncMock(return_value=20)

    rank_mock = MagicMock()
    rank_mock.award_xp = AsyncMock(
        return_value=RankResult(
            rank_name="Runner", nerve_cap=75, total_xp=1040, promoted=True
        )
    )

    session_mock = AsyncMock()

    svc = _make_service(
        redis=redis_mock,
        config=config_mock,
        nerve=nerve_mock,
        heat=heat_mock,
        rank=rank_mock,
    )

    with patch(
        "services.api_fastapi.domain.services.crime_service.earn",
        new_callable=AsyncMock,
    ), patch(
        "services.api_fastapi.domain.services.crime_service.random.randint",
        return_value=500,
    ):
        result = await svc.execute_crime(
            session_mock, player_id, "heist", "idem-promo"
        )

    assert result.promoted is True
    assert result.new_rank == "Runner"
    assert result.xp_earned == 40
