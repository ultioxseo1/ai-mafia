"""
Unit tests for ReconciliationJob.

Validates: Requirements 10.1, 10.2, 10.3, 10.4
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.api_fastapi.domain.jobs.reconciliation import (
    AlertChannel,
    ReconciliationJob,
    ReconciliationReport,
)
from services.api_fastapi.domain.models.economy import (
    Currency,
    LedgerEntryType,
    OwnerType,
    Wallet,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_wallet(balance: int = 100, owner_id: uuid.UUID | None = None) -> Wallet:
    w = Wallet()
    w.id = uuid.uuid4()
    w.owner_type = OwnerType.PLAYER
    w.owner_id = owner_id or uuid.uuid4()
    w.currency = Currency.CASH
    w.balance = balance
    w.reserved_balance = 0
    w.is_active = True
    return w


class FakeAlertChannel:
    """In-memory alert channel for testing."""

    def __init__(self) -> None:
        self.alerts: list[dict[str, Any]] = []

    async def send_alert(self, severity: str, message: str, details: dict[str, Any]) -> None:
        self.alerts.append({"severity": severity, "message": message, "details": details})


def _build_session(wallets: list[Wallet], expected_balances: dict[uuid.UUID, int]) -> AsyncMock:
    """
    Build a mock AsyncSession that returns *wallets* on the first execute
    (the SELECT wallets query) and the matching expected balance on
    subsequent executes (the SUM query per wallet).
    """
    session = AsyncMock()

    # Track call count to route first call → wallets, rest → balances
    call_count = {"n": 0}
    wallet_queue = list(wallets)  # copy for the per-wallet balance lookups

    async def _execute_side_effect(stmt, *args, **kwargs):
        result = MagicMock()
        if call_count["n"] == 0:
            # First call: SELECT wallets
            result.scalars.return_value.all.return_value = wallets
        else:
            # Subsequent calls: SUM query per wallet (in order)
            idx = call_count["n"] - 1
            w = wallet_queue[idx]
            result.scalar_one.return_value = expected_balances.get(w.id, 0)
        call_count["n"] += 1
        return result

    session.execute = AsyncMock(side_effect=_execute_side_effect)
    return session


def _build_config() -> AsyncMock:
    config = AsyncMock()
    config.get = AsyncMock(return_value="0 4 * * *")
    return config


# ---------------------------------------------------------------------------
# ReconciliationReport dataclass tests
# ---------------------------------------------------------------------------


def test_report_defaults():
    r = ReconciliationReport()
    assert r.wallets_checked == 0
    assert r.mismatches == 0
    assert r.execution_time_seconds == 0.0
    assert r.mismatch_details == []


# ---------------------------------------------------------------------------
# AlertChannel protocol tests
# ---------------------------------------------------------------------------


def test_fake_alert_channel_satisfies_protocol():
    assert isinstance(FakeAlertChannel(), AlertChannel)


# ---------------------------------------------------------------------------
# ReconciliationJob.run() tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_no_wallets():
    """Empty database → zero wallets checked, zero mismatches."""
    session = _build_session([], {})
    config = _build_config()
    job = ReconciliationJob(session=session, config=config)

    report = await job.run()

    assert report.wallets_checked == 0
    assert report.mismatches == 0
    assert report.mismatch_details == []
    assert report.execution_time_seconds >= 0


@pytest.mark.asyncio
async def test_run_all_match():
    """All wallets match their ledger sum → zero mismatches, no alerts."""
    w1 = _make_wallet(balance=500)
    w2 = _make_wallet(balance=0)
    session = _build_session([w1, w2], {w1.id: 500, w2.id: 0})
    alert = FakeAlertChannel()
    config = _build_config()
    job = ReconciliationJob(session=session, config=config, alert_channel=alert)

    report = await job.run()

    assert report.wallets_checked == 2
    assert report.mismatches == 0
    assert report.mismatch_details == []
    assert len(alert.alerts) == 0


@pytest.mark.asyncio
async def test_run_with_mismatch_emits_sev1():
    """Mismatch triggers SEV-1 alert and appears in report details."""
    w = _make_wallet(balance=100)
    session = _build_session([w], {w.id: 80})  # expected 80, actual 100
    alert = FakeAlertChannel()
    config = _build_config()
    job = ReconciliationJob(session=session, config=config, alert_channel=alert)

    report = await job.run()

    assert report.wallets_checked == 1
    assert report.mismatches == 1
    assert len(report.mismatch_details) == 1

    detail = report.mismatch_details[0]
    assert detail["wallet_id"] == str(w.id)
    assert detail["expected"] == 80
    assert detail["actual"] == 100
    assert detail["delta"] == 20

    # SEV-1 alert emitted
    assert len(alert.alerts) == 1
    assert alert.alerts[0]["severity"] == "SEV-1"
    assert str(w.id) in alert.alerts[0]["message"]


@pytest.mark.asyncio
async def test_run_no_alert_channel_does_not_crash():
    """Mismatch with no alert channel configured still produces report."""
    w = _make_wallet(balance=50)
    session = _build_session([w], {w.id: 30})
    config = _build_config()
    job = ReconciliationJob(session=session, config=config, alert_channel=None)

    report = await job.run()

    assert report.mismatches == 1


@pytest.mark.asyncio
async def test_run_logs_summary(caplog):
    """Requirement 10.4: summary is logged with wallets_checked, mismatches, execution_time."""
    w = _make_wallet(balance=100)
    session = _build_session([w], {w.id: 100})
    config = _build_config()
    job = ReconciliationJob(session=session, config=config)

    with caplog.at_level("INFO", logger="services.api_fastapi.domain.jobs.reconciliation"):
        report = await job.run()

    assert any("wallets_checked=1" in msg for msg in caplog.messages)
    assert any("mismatches=0" in msg for msg in caplog.messages)
    assert any("execution_time=" in msg for msg in caplog.messages)


# ---------------------------------------------------------------------------
# get_schedule tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_schedule_from_config():
    config = AsyncMock()
    config.get = AsyncMock(return_value="30 2 * * *")
    session = AsyncMock()
    job = ReconciliationJob(session=session, config=config)

    assert await job.get_schedule() == "30 2 * * *"


@pytest.mark.asyncio
async def test_get_schedule_default_fallback():
    config = AsyncMock()
    config.get = AsyncMock(return_value=None)
    session = AsyncMock()
    job = ReconciliationJob(session=session, config=config)

    assert await job.get_schedule() == "0 4 * * *"
