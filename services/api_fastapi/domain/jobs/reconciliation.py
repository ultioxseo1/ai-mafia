"""
services/api_fastapi/domain/jobs/reconciliation.py

AI MAFIA — Daily Reconciliation Job

Verifies that every wallet balance matches the sum of its POSTED ledger
entries.  On mismatch, emits a SEV-1 alert to the configured alerting
channel and logs a summary.

Requirements: 10.1, 10.2, 10.3, 10.4
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api_fastapi.domain.models.economy import (
    LedgerEntry,
    LedgerEntryStatus,
    LedgerEntryType,
    Wallet,
)
from services.api_fastapi.domain.services.config_service import (
    ConfigService,
    RECONCILIATION_SCHEDULE,
)

logger = logging.getLogger(__name__)

# Credit entry types increase balance; debit entry types decrease it.
_CREDIT_TYPES = {LedgerEntryType.EARN, LedgerEntryType.CAPTURE}
_DEBIT_TYPES = {LedgerEntryType.SPEND, LedgerEntryType.TAX}


# ---------------------------------------------------------------------------
# Alert channel protocol — implementations can be webhook, PagerDuty, etc.
# ---------------------------------------------------------------------------

@runtime_checkable
class AlertChannel(Protocol):
    async def send_alert(self, severity: str, message: str, details: dict[str, Any]) -> None:
        """Emit an alert with the given severity, message, and structured details."""
        ...


# ---------------------------------------------------------------------------
# Reconciliation report
# ---------------------------------------------------------------------------

@dataclass
class MismatchDetail:
    wallet_id: str
    expected: int
    actual: int
    delta: int


@dataclass
class ReconciliationReport:
    wallets_checked: int = 0
    mismatches: int = 0
    execution_time_seconds: float = 0.0
    mismatch_details: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Reconciliation job
# ---------------------------------------------------------------------------

class ReconciliationJob:
    """
    Compares every wallet balance to the ledger-derived balance and raises
    SEV-1 alerts on any mismatch.

    Schedule is read from ConfigService (``RECONCILIATION_SCHEDULE``).
    """

    def __init__(
        self,
        session: AsyncSession,
        config: ConfigService,
        alert_channel: AlertChannel | None = None,
    ) -> None:
        self._session = session
        self._config = config
        self._alert_channel = alert_channel

    # -- public API ---------------------------------------------------------

    async def get_schedule(self) -> str:
        """Return the cron expression for this job (from ConfigService)."""
        schedule = await self._config.get(RECONCILIATION_SCHEDULE)
        return schedule or "0 4 * * *"

    async def run(self) -> ReconciliationReport:
        """
        For each wallet:
          expected = SUM(amount) of POSTED entries with matching
                     (owner_type, owner_id, currency), treating
                     EARN/CAPTURE as credits and SPEND/TAX as debits
          actual   = wallet.balance
          if expected != actual → flag SEV-1

        Returns a summary report.
        """
        start = time.monotonic()

        # Fetch all wallets
        wallet_result = await self._session.execute(select(Wallet))
        wallets = wallet_result.scalars().all()

        report = ReconciliationReport()
        report.wallets_checked = len(wallets)

        for wallet in wallets:
            expected = await self._compute_expected_balance(wallet)
            actual = wallet.balance

            if expected != actual:
                delta = actual - expected
                detail = {
                    "wallet_id": str(wallet.id),
                    "expected": expected,
                    "actual": actual,
                    "delta": delta,
                }
                report.mismatches += 1
                report.mismatch_details.append(detail)

                logger.error(
                    "Reconciliation mismatch: wallet=%s expected=%d actual=%d delta=%d",
                    wallet.id,
                    expected,
                    actual,
                    delta,
                )

                await self._emit_sev1_alert(wallet, expected, actual, delta)

        elapsed = time.monotonic() - start
        report.execution_time_seconds = round(elapsed, 3)

        logger.info(
            "Reconciliation complete: wallets_checked=%d mismatches=%d execution_time=%.3fs",
            report.wallets_checked,
            report.mismatches,
            report.execution_time_seconds,
        )

        return report

    # -- internals ----------------------------------------------------------

    async def _compute_expected_balance(self, wallet: Wallet) -> int:
        """
        SUM of POSTED ledger entries for this wallet's
        (owner_type, owner_id, currency) tuple, with EARN/CAPTURE as
        credits (+) and SPEND/TAX as debits (-).
        """
        credit_debit = case(
            (
                LedgerEntry.entry_type.in_([t.value for t in _CREDIT_TYPES]),
                LedgerEntry.amount,
            ),
            (
                LedgerEntry.entry_type.in_([t.value for t in _DEBIT_TYPES]),
                -LedgerEntry.amount,
            ),
            else_=0,
        )

        stmt = (
            select(func.coalesce(func.sum(credit_debit), 0))
            .where(
                LedgerEntry.owner_type == wallet.owner_type,
                LedgerEntry.owner_id == wallet.owner_id,
                LedgerEntry.currency == wallet.currency,
                LedgerEntry.status == LedgerEntryStatus.POSTED,
            )
        )

        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def _emit_sev1_alert(
        self,
        wallet: Wallet,
        expected: int,
        actual: int,
        delta: int,
    ) -> None:
        """Send a SEV-1 alert if an alert channel is configured."""
        if self._alert_channel is None:
            return

        await self._alert_channel.send_alert(
            severity="SEV-1",
            message=f"Wallet balance mismatch detected for wallet {wallet.id}",
            details={
                "wallet_id": str(wallet.id),
                "owner_type": wallet.owner_type.value,
                "owner_id": str(wallet.owner_id),
                "currency": wallet.currency.value,
                "expected_balance": expected,
                "actual_balance": actual,
                "delta": delta,
            },
        )
