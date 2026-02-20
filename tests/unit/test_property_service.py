"""
Unit tests for PropertyService — purchase, upgrade, income, listing.

Validates: Requirements 8.1–8.6, 9.1–9.7
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from services.api_fastapi.domain.models.economy import Currency, OwnerType
from services.api_fastapi.domain.models.family import (
    FamilyMember,
    FamilyProperty,
    FamilyRole,
    PropertyDefinition,
)
from services.api_fastapi.domain.services.ledger_service import (
    InsufficientFunds,
    LedgerResult,
)
from services.api_fastapi.domain.services.property_service import (
    AlreadyOwned,
    MaxLevelReached,
    PropertyNotFound,
    PropertyOwnership,
    PropertyService,
)
from services.api_fastapi.domain.services.vault_service import (
    InsufficientPermission,
    InsufficientVaultFunds,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROP_MODULE = "services.api_fastapi.domain.services.property_service"

PLAYER_ID = uuid4()
FAMILY_ID = uuid4()

SPEAKEASY_DEF = PropertyDefinition(
    property_id="speakeasy",
    name="Speakeasy",
    purchase_price=50000,
    daily_income=500,
    max_level=10,
)

CASINO_DEF = PropertyDefinition(
    property_id="casino",
    name="Casino",
    purchase_price=200000,
    daily_income=2000,
    max_level=10,
)


def _make_config(defs=None) -> MagicMock:
    if defs is None:
        defs = [SPEAKEASY_DEF, CASINO_DEF]
    config = MagicMock()
    config.get_json = AsyncMock(
        return_value=[
            {
                "property_id": d.property_id,
                "name": d.name,
                "purchase_price": d.purchase_price,
                "daily_income": d.daily_income,
                "max_level": d.max_level,
            }
            for d in defs
        ]
    )
    return config


def _make_member(player_id, family_id, role: FamilyRole) -> MagicMock:
    m = MagicMock(spec=FamilyMember)
    m.player_id = player_id
    m.family_id = family_id
    m.role = role
    return m


def _make_family_property(family_id, property_id, level=1) -> MagicMock:
    fp = MagicMock(spec=FamilyProperty)
    fp.family_id = family_id
    fp.property_id = property_id
    fp.level = level
    return fp


# ---------------------------------------------------------------------------
# purchase_property tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch(f"{PROP_MODULE}.spend", new_callable=AsyncMock)
async def test_purchase_property_success(mock_spend):
    """Don can purchase a property the family doesn't own."""
    don = _make_member(PLAYER_ID, FAMILY_ID, FamilyRole.DON)
    mock_spend.return_value = LedgerResult(
        wallet_balance=0, wallet_reserved=0, ledger_entry_id=str(uuid4()),
    )

    session = AsyncMock()
    call_count = 0

    async def _mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.scalar_one_or_none.return_value = don  # _require_don
        else:
            result.scalar_one_or_none.return_value = None  # _get_ownership (not owned)
        return result

    session.execute = _mock_execute

    svc = PropertyService(_make_config())
    result = await svc.purchase_property(session, PLAYER_ID, FAMILY_ID, "speakeasy", "idem-p1")

    assert isinstance(result, PropertyOwnership)
    assert result.family_id == FAMILY_ID
    assert result.property_id == "speakeasy"
    assert result.name == "Speakeasy"
    assert result.level == 1
    assert result.daily_income == 500

    mock_spend.assert_called_once()
    spend_kwargs = mock_spend.call_args.kwargs
    assert spend_kwargs["owner_type"] == OwnerType.FAMILY
    assert spend_kwargs["amount"] == 50000
    assert spend_kwargs["currency"] == Currency.CASH

    session.add.assert_called_once()


@pytest.mark.asyncio
async def test_purchase_property_non_don_raises():
    """Non-Don actor cannot purchase a property."""
    soldier = _make_member(PLAYER_ID, FAMILY_ID, FamilyRole.SOLDIER)

    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = soldier
    session.execute = AsyncMock(return_value=result_mock)

    svc = PropertyService(_make_config())

    with pytest.raises(InsufficientPermission):
        await svc.purchase_property(session, PLAYER_ID, FAMILY_ID, "speakeasy", "idem-p2")


@pytest.mark.asyncio
async def test_purchase_property_already_owned_raises():
    """Purchasing a property the family already owns raises AlreadyOwned."""
    don = _make_member(PLAYER_ID, FAMILY_ID, FamilyRole.DON)
    existing_fp = _make_family_property(FAMILY_ID, "speakeasy", level=1)

    session = AsyncMock()
    call_count = 0

    async def _mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.scalar_one_or_none.return_value = don
        else:
            result.scalar_one_or_none.return_value = existing_fp
        return result

    session.execute = _mock_execute

    svc = PropertyService(_make_config())

    with pytest.raises(AlreadyOwned):
        await svc.purchase_property(session, PLAYER_ID, FAMILY_ID, "speakeasy", "idem-p3")


@pytest.mark.asyncio
@patch(f"{PROP_MODULE}.spend", new_callable=AsyncMock)
async def test_purchase_property_insufficient_funds_raises(mock_spend):
    """InsufficientFunds from ledger.spend → InsufficientVaultFunds."""
    don = _make_member(PLAYER_ID, FAMILY_ID, FamilyRole.DON)
    mock_spend.side_effect = InsufficientFunds("Not enough balance.")

    session = AsyncMock()
    call_count = 0

    async def _mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.scalar_one_or_none.return_value = don
        else:
            result.scalar_one_or_none.return_value = None
        return result

    session.execute = _mock_execute

    svc = PropertyService(_make_config())

    with pytest.raises(InsufficientVaultFunds):
        await svc.purchase_property(session, PLAYER_ID, FAMILY_ID, "speakeasy", "idem-p4")


@pytest.mark.asyncio
async def test_purchase_property_not_found_raises():
    """Purchasing a property not in config raises PropertyNotFound."""
    don = _make_member(PLAYER_ID, FAMILY_ID, FamilyRole.DON)

    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = don
    session.execute = AsyncMock(return_value=result_mock)

    svc = PropertyService(_make_config())

    with pytest.raises(PropertyNotFound):
        await svc.purchase_property(session, PLAYER_ID, FAMILY_ID, "nonexistent", "idem-p5")


# ---------------------------------------------------------------------------
# upgrade_property tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch(f"{PROP_MODULE}.spend", new_callable=AsyncMock)
async def test_upgrade_property_success(mock_spend):
    """Don can upgrade a property from level 1 to level 2."""
    don = _make_member(PLAYER_ID, FAMILY_ID, FamilyRole.DON)
    fp = _make_family_property(FAMILY_ID, "speakeasy", level=1)
    mock_spend.return_value = LedgerResult(
        wallet_balance=0, wallet_reserved=0, ledger_entry_id=str(uuid4()),
    )

    session = AsyncMock()
    call_count = 0

    async def _mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.scalar_one_or_none.return_value = don
        else:
            result.scalar_one_or_none.return_value = fp
        return result

    session.execute = _mock_execute

    svc = PropertyService(_make_config())
    result = await svc.upgrade_property(session, PLAYER_ID, FAMILY_ID, "speakeasy", "idem-u1")

    assert isinstance(result, PropertyOwnership)
    assert result.level == 2
    assert result.daily_income == 500 * 2  # base_daily_income * new_level

    # Cost should be purchase_price * current_level = 50000 * 1
    mock_spend.assert_called_once()
    assert mock_spend.call_args.kwargs["amount"] == 50000


@pytest.mark.asyncio
@patch(f"{PROP_MODULE}.spend", new_callable=AsyncMock)
async def test_upgrade_property_cost_scales_with_level(mock_spend):
    """Upgrade cost = purchase_price * current_level."""
    don = _make_member(PLAYER_ID, FAMILY_ID, FamilyRole.DON)
    fp = _make_family_property(FAMILY_ID, "speakeasy", level=5)
    mock_spend.return_value = LedgerResult(
        wallet_balance=0, wallet_reserved=0, ledger_entry_id=str(uuid4()),
    )

    session = AsyncMock()
    call_count = 0

    async def _mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.scalar_one_or_none.return_value = don
        else:
            result.scalar_one_or_none.return_value = fp
        return result

    session.execute = _mock_execute

    svc = PropertyService(_make_config())
    result = await svc.upgrade_property(session, PLAYER_ID, FAMILY_ID, "speakeasy", "idem-u2")

    assert result.level == 6
    # Cost = 50000 * 5 = 250000
    assert mock_spend.call_args.kwargs["amount"] == 250000


@pytest.mark.asyncio
async def test_upgrade_property_max_level_raises():
    """Upgrading a property at max_level raises MaxLevelReached."""
    don = _make_member(PLAYER_ID, FAMILY_ID, FamilyRole.DON)
    fp = _make_family_property(FAMILY_ID, "speakeasy", level=10)

    session = AsyncMock()
    call_count = 0

    async def _mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.scalar_one_or_none.return_value = don
        else:
            result.scalar_one_or_none.return_value = fp
        return result

    session.execute = _mock_execute

    svc = PropertyService(_make_config())

    with pytest.raises(MaxLevelReached):
        await svc.upgrade_property(session, PLAYER_ID, FAMILY_ID, "speakeasy", "idem-u3")


@pytest.mark.asyncio
async def test_upgrade_property_non_don_raises():
    """Non-Don actor cannot upgrade a property."""
    capo = _make_member(PLAYER_ID, FAMILY_ID, FamilyRole.CAPO)

    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = capo
    session.execute = AsyncMock(return_value=result_mock)

    svc = PropertyService(_make_config())

    with pytest.raises(InsufficientPermission):
        await svc.upgrade_property(session, PLAYER_ID, FAMILY_ID, "speakeasy", "idem-u4")


@pytest.mark.asyncio
@patch(f"{PROP_MODULE}.spend", new_callable=AsyncMock)
async def test_upgrade_property_insufficient_funds_raises(mock_spend):
    """InsufficientFunds from ledger.spend → InsufficientVaultFunds."""
    don = _make_member(PLAYER_ID, FAMILY_ID, FamilyRole.DON)
    fp = _make_family_property(FAMILY_ID, "speakeasy", level=1)
    mock_spend.side_effect = InsufficientFunds("Not enough balance.")

    session = AsyncMock()
    call_count = 0

    async def _mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.scalar_one_or_none.return_value = don
        else:
            result.scalar_one_or_none.return_value = fp
        return result

    session.execute = _mock_execute

    svc = PropertyService(_make_config())

    with pytest.raises(InsufficientVaultFunds):
        await svc.upgrade_property(session, PLAYER_ID, FAMILY_ID, "speakeasy", "idem-u5")


@pytest.mark.asyncio
async def test_upgrade_property_not_owned_raises():
    """Upgrading a property the family doesn't own raises PropertyNotFound."""
    don = _make_member(PLAYER_ID, FAMILY_ID, FamilyRole.DON)

    session = AsyncMock()
    call_count = 0

    async def _mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.scalar_one_or_none.return_value = don
        else:
            result.scalar_one_or_none.return_value = None  # not owned
        return result

    session.execute = _mock_execute

    svc = PropertyService(_make_config())

    with pytest.raises(PropertyNotFound):
        await svc.upgrade_property(session, PLAYER_ID, FAMILY_ID, "speakeasy", "idem-u6")


# ---------------------------------------------------------------------------
# calculate_daily_income tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calculate_daily_income_sums_correctly():
    """Income = sum(base_daily_income * level) for all owned properties."""
    fp1 = _make_family_property(FAMILY_ID, "speakeasy", level=3)
    fp2 = _make_family_property(FAMILY_ID, "casino", level=2)

    session = AsyncMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = [fp1, fp2]
    result_mock = MagicMock()
    result_mock.scalars.return_value = scalars_mock
    session.execute = AsyncMock(return_value=result_mock)

    svc = PropertyService(_make_config())
    income = await svc.calculate_daily_income(session, FAMILY_ID)

    # speakeasy: 500 * 3 = 1500, casino: 2000 * 2 = 4000
    assert income == 5500


@pytest.mark.asyncio
async def test_calculate_daily_income_no_properties():
    """Income is 0 when family owns no properties."""
    session = AsyncMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = []
    result_mock = MagicMock()
    result_mock.scalars.return_value = scalars_mock
    session.execute = AsyncMock(return_value=result_mock)

    svc = PropertyService(_make_config())
    income = await svc.calculate_daily_income(session, FAMILY_ID)

    assert income == 0


# ---------------------------------------------------------------------------
# list_properties tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_properties_returns_all_definitions():
    """list_properties returns all PropertyDefinitions from config."""
    config = _make_config()
    svc = PropertyService(config)
    result = await svc.list_properties(config)

    assert len(result) == 2
    assert result[0].property_id == "speakeasy"
    assert result[1].property_id == "casino"


# ---------------------------------------------------------------------------
# list_family_properties tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_family_properties_returns_owned():
    """list_family_properties returns all FamilyProperty records for the family."""
    fp1 = _make_family_property(FAMILY_ID, "speakeasy", level=1)
    fp2 = _make_family_property(FAMILY_ID, "casino", level=3)

    session = AsyncMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = [fp1, fp2]
    result_mock = MagicMock()
    result_mock.scalars.return_value = scalars_mock
    session.execute = AsyncMock(return_value=result_mock)

    svc = PropertyService(_make_config())
    result = await svc.list_family_properties(session, FAMILY_ID)

    assert len(result) == 2
