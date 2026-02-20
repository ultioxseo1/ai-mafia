"""
Unit tests for RankService — compute_rank pure function and RankResult dataclass.

Validates: Requirements 8.1, 8.2, 8.5
"""

from __future__ import annotations

import pytest

from services.api_fastapi.domain.services.rank_service import (
    RANK_TABLE,
    RankResult,
    compute_rank,
)


# ---------------------------------------------------------------------------
# RANK_TABLE constant tests
# ---------------------------------------------------------------------------


def test_rank_table_has_seven_entries():
    assert len(RANK_TABLE) == 7


def test_rank_table_thresholds_ascending():
    thresholds = [t[1] for t in RANK_TABLE]
    assert thresholds == sorted(thresholds)


def test_rank_table_locked_values():
    """Requirement 8.1: locked rank table values."""
    expected = [
        ("Empty-Suit", 0, 50),
        ("Runner", 1_000, 75),
        ("Enforcer", 5_000, 100),
        ("Capo", 25_000, 150),
        ("Fixer", 100_000, 200),
        ("Underboss", 500_000, 250),
        ("Godfather", 2_000_000, 300),
    ]
    assert RANK_TABLE == expected


# ---------------------------------------------------------------------------
# compute_rank tests
# ---------------------------------------------------------------------------


def test_compute_rank_zero_xp():
    assert compute_rank(0) == ("Empty-Suit", 50)


def test_compute_rank_just_below_runner():
    assert compute_rank(999) == ("Empty-Suit", 50)


def test_compute_rank_exact_runner_threshold():
    assert compute_rank(1_000) == ("Runner", 75)


def test_compute_rank_exact_godfather_threshold():
    assert compute_rank(2_000_000) == ("Godfather", 300)


def test_compute_rank_above_godfather():
    assert compute_rank(99_999_999) == ("Godfather", 300)


def test_compute_rank_multi_rank_jump():
    """Requirement 8.5: single large XP skips intermediate ranks."""
    # 25_000 XP should land on Capo, skipping Runner and Enforcer
    assert compute_rank(25_000) == ("Capo", 150)


def test_compute_rank_each_threshold():
    """Every exact threshold maps to the correct rank."""
    for name, threshold, cap in RANK_TABLE:
        assert compute_rank(threshold) == (name, cap)


# ---------------------------------------------------------------------------
# RankResult dataclass tests
# ---------------------------------------------------------------------------


def test_rank_result_fields():
    r = RankResult(rank_name="Runner", nerve_cap=75, total_xp=1500, promoted=True)
    assert r.rank_name == "Runner"
    assert r.nerve_cap == 75
    assert r.total_xp == 1500
    assert r.promoted is True


def test_rank_result_is_frozen():
    r = RankResult(rank_name="Runner", nerve_cap=75, total_xp=1500, promoted=True)
    with pytest.raises(AttributeError):
        r.rank_name = "Capo"  # type: ignore[misc]
