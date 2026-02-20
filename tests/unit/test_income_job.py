"""
Unit tests for IncomeJob.

Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5
"""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.api_fastapi.domain.jobs.income_job import IncomeJob, IncomeReport
from services.api_fastapi.domain.models.economy import Currency, OwnerType
from services.api_fastapi.domain.models.family import (
    Family,
    FamilyProperty,
    FamilyStatus,
    PropertyDefinition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_PROP_DEFS = [
    PropertyDefinition("speakeasy", "Speakeasy", 50000, 500, 10),
    PropertyDefinition("casino", "Casino", 200000, 2000, 10),
]


def _make_family(family_id: uuid.UUID | None = None) -> Family:
    f = Family()
    f.id = family_id or uuid.uuid4()
    f.name = "TestFamily"
    f.tag = "TF"
    f.status = FamilyStatus.ACTIVE
    return f


def _make_family_property(
    family_id: uuid.UUID,
    property_id: str = "speakeasy",
    level: int = 1,
) -> FamilyProperty:
    fp = FamilyProperty()
    fp.id = uuid.uuid4()
    fp.family_id = family_id
    fp.property_id = property_id
    fp.level = level
    return fp


def _build_session(families: list[Family], family_props: dict[uuid.UUID, list[FamilyProperty]]) -> AsyncMock:
    """
    Build a mock AsyncSession:
      - First execute → families (the JOIN query)
      - Subsequent executes → FamilyProperty list per family (in order)
    """
    session = AsyncMock()
    call_count = {"n": 0}
    family_order = list(families)

    async def _execute_side_effect(stmt, *args, **kwargs):
        result = MagicMock()
        if call_count["n"] == 0:
            result.scalars.return_value.all.return_value = families
        else:
            idx = call_count["n"] - 1
            fam = family_order[idx]
            props = family_props.get(fam.id, [])
            result.scalars.return_value.all.return_value = props
        call_count["n"] += 1
        return result

    session.execute = AsyncMock(side_effect=_execute_side_effect)
    return session


def _build_config(prop_defs: list[PropertyDefinition] | None = None) -> AsyncMock:
    defs = prop_defs if prop_defs is not None else _DEFAULT_PROP_DEFS
    config = AsyncMock()
    config.get = AsyncMock(return_value="0 5 * * *")
    config.get_json = AsyncMock(return_value=[
        {
            "property_id": pd.property_id,
            "name": pd.name,
            "purchase_price": pd.purchase_price,
            "daily_income": pd.daily_income,
            "max_level": pd.max_level,
        }
        for pd in defs
    ])
    return config


# ---------------------------------------------------------------------------
# IncomeReport dataclass tests
# ---------------------------------------------------------------------------


def test_income_report_defaults():
    r = IncomeReport(families_processed=0, families_failed=0, total_distributed=0, execution_time_ms=0.0)
    assert r.families_processed == 0
    assert r.families_failed == 0
    assert r.total_distributed == 0
    assert r.execution_time_ms == 0.0


# ---------------------------------------------------------------------------
# IncomeJob.run() tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("services.api_fastapi.domain.jobs.income_job.earn", new_callable=AsyncMock)
async def test_run_no_families(mock_earn):
    """No active families with properties → zero processed, no earn calls."""
    session = _build_session([], {})
    config = _build_config()
    job = IncomeJob(session=session, config=config)

    report = await job.run()

    assert report.families_processed == 0
    assert report.families_failed == 0
    assert report.total_distributed == 0
    assert report.execution_time_ms >= 0
    mock_earn.assert_not_called()


@pytest.mark.asyncio
@patch("services.api_fastapi.domain.jobs.income_job.earn", new_callable=AsyncMock)
async def test_run_single_family_single_property(mock_earn):
    """One family with one level-1 speakeasy → earns 500 CASH."""
    fam = _make_family()
    fp = _make_family_property(fam.id, "speakeasy", level=1)
    session = _build_session([fam], {fam.id: [fp]})
    config = _build_config()
    mock_earn.return_value = MagicMock()

    job = IncomeJob(session=session, config=config)
    report = await job.run()

    assert report.families_processed == 1
    assert report.families_failed == 0
    assert report.total_distributed == 500

    mock_earn.assert_called_once()
    call_kwargs = mock_earn.call_args
    assert call_kwargs.kwargs["owner_type"] == OwnerType.FAMILY
    assert call_kwargs.kwargs["owner_id"] == fam.id
    assert call_kwargs.kwargs["currency"] == Currency.CASH
    assert call_kwargs.kwargs["amount"] == 500


@pytest.mark.asyncio
@patch("services.api_fastapi.domain.jobs.income_job.earn", new_callable=AsyncMock)
async def test_run_income_scales_with_level(mock_earn):
    """Level-3 speakeasy → earns 500 * 3 = 1500 CASH."""
    fam = _make_family()
    fp = _make_family_property(fam.id, "speakeasy", level=3)
    session = _build_session([fam], {fam.id: [fp]})
    config = _build_config()
    mock_earn.return_value = MagicMock()

    job = IncomeJob(session=session, config=config)
    report = await job.run()

    assert report.total_distributed == 1500
    assert mock_earn.call_args.kwargs["amount"] == 1500


@pytest.mark.asyncio
@patch("services.api_fastapi.domain.jobs.income_job.earn", new_callable=AsyncMock)
async def test_run_multiple_properties_summed(mock_earn):
    """Family with speakeasy(lv2) + casino(lv1) → 500*2 + 2000*1 = 3000."""
    fam = _make_family()
    fp1 = _make_family_property(fam.id, "speakeasy", level=2)
    fp2 = _make_family_property(fam.id, "casino", level=1)
    session = _build_session([fam], {fam.id: [fp1, fp2]})
    config = _build_config()
    mock_earn.return_value = MagicMock()

    job = IncomeJob(session=session, config=config)
    report = await job.run()

    assert report.total_distributed == 3000
    assert mock_earn.call_args.kwargs["amount"] == 3000


@pytest.mark.asyncio
@patch("services.api_fastapi.domain.jobs.income_job.earn", new_callable=AsyncMock)
async def test_run_multiple_families(mock_earn):
    """Two families each get their own earn call."""
    fam1 = _make_family()
    fam2 = _make_family()
    fp1 = _make_family_property(fam1.id, "speakeasy", level=1)
    fp2 = _make_family_property(fam2.id, "casino", level=2)
    session = _build_session([fam1, fam2], {fam1.id: [fp1], fam2.id: [fp2]})
    config = _build_config()
    mock_earn.return_value = MagicMock()

    job = IncomeJob(session=session, config=config)
    report = await job.run()

    assert report.families_processed == 2
    assert report.total_distributed == 500 + 4000  # 500*1 + 2000*2
    assert mock_earn.call_count == 2


@pytest.mark.asyncio
@patch("services.api_fastapi.domain.jobs.income_job.earn", new_callable=AsyncMock)
async def test_run_per_family_error_continues(mock_earn):
    """Requirement 10.5: error on one family doesn't halt others."""
    fam1 = _make_family()
    fam2 = _make_family()
    fp1 = _make_family_property(fam1.id, "speakeasy", level=1)
    fp2 = _make_family_property(fam2.id, "casino", level=1)
    session = _build_session([fam1, fam2], {fam1.id: [fp1], fam2.id: [fp2]})
    config = _build_config()

    # First earn call raises, second succeeds
    mock_earn.side_effect = [RuntimeError("DB error"), MagicMock()]

    job = IncomeJob(session=session, config=config)
    report = await job.run()

    assert report.families_processed == 1
    assert report.families_failed == 1
    assert report.total_distributed == 2000


@pytest.mark.asyncio
@patch("services.api_fastapi.domain.jobs.income_job.earn", new_callable=AsyncMock)
async def test_run_idempotency_key_contains_date(mock_earn):
    """Idempotency key includes family_id and today's date."""
    fam = _make_family()
    fp = _make_family_property(fam.id, "speakeasy", level=1)
    session = _build_session([fam], {fam.id: [fp]})
    config = _build_config()
    mock_earn.return_value = MagicMock()

    job = IncomeJob(session=session, config=config)
    await job.run()

    idem_key = mock_earn.call_args.kwargs["idempotency_key"]
    today = date.today().isoformat()
    assert str(fam.id) in idem_key
    assert today in idem_key
    assert idem_key == f"income:{fam.id}:{today}"


@pytest.mark.asyncio
@patch("services.api_fastapi.domain.jobs.income_job.earn", new_callable=AsyncMock)
async def test_run_unknown_property_id_skipped(mock_earn):
    """Property with no matching definition contributes zero income."""
    fam = _make_family()
    fp = _make_family_property(fam.id, "unknown_prop", level=5)
    session = _build_session([fam], {fam.id: [fp]})
    config = _build_config()

    job = IncomeJob(session=session, config=config)
    report = await job.run()

    # No income → no earn call, but family still counted as processed
    assert report.families_processed == 1
    assert report.total_distributed == 0
    mock_earn.assert_not_called()


@pytest.mark.asyncio
@patch("services.api_fastapi.domain.jobs.income_job.earn", new_callable=AsyncMock)
async def test_run_logs_summary(mock_earn, caplog):
    """Requirement 10.4: summary logged with families_processed, total_distributed, execution_time."""
    fam = _make_family()
    fp = _make_family_property(fam.id, "speakeasy", level=1)
    session = _build_session([fam], {fam.id: [fp]})
    config = _build_config()
    mock_earn.return_value = MagicMock()

    job = IncomeJob(session=session, config=config)

    with caplog.at_level("INFO", logger="services.api_fastapi.domain.jobs.income_job"):
        await job.run()

    assert any("families_processed=1" in msg for msg in caplog.messages)
    assert any("total_distributed=500" in msg for msg in caplog.messages)
    assert any("execution_time=" in msg for msg in caplog.messages)


# ---------------------------------------------------------------------------
# get_schedule tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_schedule_from_config():
    config = AsyncMock()
    config.get = AsyncMock(return_value="30 6 * * *")
    session = AsyncMock()
    job = IncomeJob(session=session, config=config)

    assert await job.get_schedule() == "30 6 * * *"


@pytest.mark.asyncio
async def test_get_schedule_default_fallback():
    config = AsyncMock()
    config.get = AsyncMock(return_value=None)
    session = AsyncMock()
    job = IncomeJob(session=session, config=config)

    assert await job.get_schedule() == "0 5 * * *"
