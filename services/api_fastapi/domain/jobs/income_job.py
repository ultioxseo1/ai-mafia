"""
services/api_fastapi/domain/jobs/income_job.py

AI MAFIA — Daily Income Job

Calculates and distributes passive CASH income from owned Properties to
Family Vaults.  Runs once per day on a configurable schedule.  Uses
date-scoped idempotency keys to prevent duplicate payouts on retry.

Requirements: 10.1, 10.2, 10.3, 10.4, 10.5
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date
from typing import Dict, List
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from services.api_fastapi.domain.models.economy import Currency, OwnerType
from services.api_fastapi.domain.models.family import (
    Family,
    FamilyProperty,
    FamilyStatus,
    PropertyDefinition,
    load_property_definitions,
)
from services.api_fastapi.domain.services.config_service import (
    ConfigService,
    INCOME_JOB_SCHEDULE,
)
from services.api_fastapi.domain.services.ledger_service import earn

logger = logging.getLogger(__name__)


@dataclass
class IncomeReport:
    families_processed: int
    families_failed: int
    total_distributed: int
    execution_time_ms: float


class IncomeJob:
    """
    Daily passive income distribution from Properties to Family Vaults.

    For each active family with at least one property, calculates
    total_income = sum(base_daily_income * level) and credits the
    family vault via ``ledger.earn()``.

    Schedule is read from ConfigService (``INCOME_JOB_SCHEDULE``).
    """

    def __init__(
        self,
        session: AsyncSession,
        config: ConfigService,
    ) -> None:
        self._session = session
        self._config = config

    async def get_schedule(self) -> str:
        """Return the cron expression for this job (from ConfigService)."""
        schedule = await self._config.get(INCOME_JOB_SCHEDULE)
        return schedule or "0 5 * * *"

    async def run(self) -> IncomeReport:
        """
        1. Load all PropertyDefinitions from config once
        2. Query all active families that have at least one FamilyProperty
        3. For each family:
           a. Query their FamilyProperty records
           b. Calculate total_income = sum(prop_def.daily_income * fp.level)
           c. If total_income > 0, call earn() with date-scoped idempotency key
           d. On error: log and increment failed count, continue
        4. Return IncomeReport with stats
        """
        start = time.monotonic()
        today = date.today().isoformat()

        # 1. Load property definitions and build lookup
        prop_defs = await load_property_definitions(self._config)
        prop_map: Dict[str, PropertyDefinition] = {
            pd.property_id: pd for pd in prop_defs
        }

        # 2. Query active families that own at least one property
        stmt = (
            select(Family)
            .join(FamilyProperty, Family.id == FamilyProperty.family_id)
            .where(Family.status == FamilyStatus.ACTIVE)
            .distinct()
        )
        result = await self._session.execute(stmt)
        families: List[Family] = list(result.scalars().all())

        families_processed = 0
        families_failed = 0
        total_distributed = 0

        # 3. Process each family
        for family in families:
            try:
                # Query family's properties
                fp_stmt = select(FamilyProperty).where(
                    FamilyProperty.family_id == family.id,
                )
                fp_result = await self._session.execute(fp_stmt)
                family_props: List[FamilyProperty] = list(fp_result.scalars().all())

                # Calculate total income
                total_income = 0
                for fp in family_props:
                    prop_def = prop_map.get(fp.property_id)
                    if prop_def is not None:
                        total_income += prop_def.daily_income * fp.level

                if total_income > 0:
                    idem_key = f"income:{family.id}:{today}"
                    reference_id = f"income:{today}"
                    await earn(
                        self._session,
                        owner_type=OwnerType.FAMILY,
                        owner_id=family.id,
                        currency=Currency.CASH,
                        amount=total_income,
                        reference_id=reference_id,
                        metadata={
                            "source": "income_job",
                            "family_id": str(family.id),
                            "date": today,
                            "total_income": total_income,
                        },
                        idempotency_key=idem_key,
                    )
                    total_distributed += total_income

                families_processed += 1

            except Exception:
                families_failed += 1
                logger.error(
                    "Income job error for family=%s",
                    family.id,
                    exc_info=True,
                )

        elapsed_ms = (time.monotonic() - start) * 1000

        logger.info(
            "Income job complete: families_processed=%d total_distributed=%d execution_time=%.1fms",
            families_processed,
            total_distributed,
            elapsed_ms,
        )

        return IncomeReport(
            families_processed=families_processed,
            families_failed=families_failed,
            total_distributed=total_distributed,
            execution_time_ms=round(elapsed_ms, 1),
        )
