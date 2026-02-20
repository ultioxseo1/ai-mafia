"""
Unit tests for FamilyVaultService — earn_with_tax, withdraw, get_vault_balance.

Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 6.1, 6.2, 6.3, 6.4, 6.5
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from services.api_fastapi.domain.models.economy import (
    Currency,
    LedgerEntryStatus,
    LedgerEntryType,
    OwnerType,
)
from services.api_fastapi.domain.models.family import FamilyMember, FamilyRole
from services.api_fastapi.domain.services.ledger_service import (
    InsufficientFunds,
    LedgerResult,
    TransferResult,
)
from services.api_fastapi.domain.services.vault_service import (
    FamilyVaultService,
    InsufficientPermission,
    InsufficientVaultFunds,
    InvalidTargetMember,
    TaxResult,
    WithdrawResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VAULT_MODULE = "services.api_fastapi.domain.services.vault_service"

PLAYER_ID = uuid4()
FAMILY_ID = uuid4()
TARGET_ID = uuid4()


def _make_config(tax_rate: int = 10) -> MagicMock:
    config = MagicMock()
    config.get_int = AsyncMock(return_value=tax_rate)
    return config


def _make_wallet(balance: int = 0) -> MagicMock:
    w = MagicMock()
    w.id = uuid4()
    w.balance = balance
    w.reserved_balance = 0
    return w


def _make_ledger_entry(entry_id=None) -> MagicMock:
    le = MagicMock()
    le.id = entry_id or uuid4()
    return le


def _make_member(player_id, family_id, role: FamilyRole) -> FamilyMember:
    m = MagicMock(spec=FamilyMember)
    m.player_id = player_id
    m.family_id = family_id
    m.role = role
    return m


# ---------------------------------------------------------------------------
# earn_with_tax tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch(f"{VAULT_MODULE}._idempo_store", new_callable=AsyncMock)
@patch(f"{VAULT_MODULE}._idempo_get", new_callable=AsyncMock, return_value=None)
@patch(f"{VAULT_MODULE}._append_ledger", new_callable=AsyncMock)
@patch(f"{VAULT_MODULE}._lock_wallet", new_callable=AsyncMock)
@patch(f"{VAULT_MODULE}._get_or_create_wallet", new_callable=AsyncMock)
@patch(f"{VAULT_MODULE}.earn", new_callable=AsyncMock)
async def test_earn_with_tax_applies_10_percent(
    mock_earn, mock_get_wallet, mock_lock, mock_append, mock_idem_get, mock_idem_store,
):
    """Tax of 10% on 100 → tax=10, net=90."""
    wallet = _make_wallet(balance=0)
    mock_get_wallet.return_value = wallet
    mock_lock.return_value = wallet

    tax_le = _make_ledger_entry()
    mock_append.return_value = tax_le

    player_result = LedgerResult(wallet_balance=90, wallet_reserved=0, ledger_entry_id=str(uuid4()))
    mock_earn.return_value = player_result

    session = AsyncMock()
    svc = FamilyVaultService(_make_config(tax_rate=10))

    result = await svc.earn_with_tax(session, PLAYER_ID, FAMILY_ID, 100, "idem-1")

    assert result.tax == 10
    assert result.net == 90
    assert result.vault_entry_id == str(tax_le.id)
    assert result.player_entry_id == player_result.ledger_entry_id

    # Vault wallet credited with tax
    assert wallet.balance == 10

    # TAX entry created via _append_ledger
    mock_append.assert_called_once()
    call_kwargs = mock_append.call_args.kwargs
    assert call_kwargs["entry_type"] == LedgerEntryType.TAX
    assert call_kwargs["amount"] == 10
    assert call_kwargs["owner_type"] == OwnerType.FAMILY

    # Player EARN called with net amount
    mock_earn.assert_called_once()
    earn_kwargs = mock_earn.call_args.kwargs
    assert earn_kwargs["amount"] == 90
    assert earn_kwargs["owner_type"] == OwnerType.PLAYER


@pytest.mark.asyncio
@patch(f"{VAULT_MODULE}.earn", new_callable=AsyncMock)
async def test_earn_with_tax_zero_tax_no_vault_entry(mock_earn):
    """When gross < 10 at 10% rate, tax=0 → full amount to player, no vault entry."""
    player_result = LedgerResult(wallet_balance=9, wallet_reserved=0, ledger_entry_id=str(uuid4()))
    mock_earn.return_value = player_result

    session = AsyncMock()
    svc = FamilyVaultService(_make_config(tax_rate=10))

    result = await svc.earn_with_tax(session, PLAYER_ID, FAMILY_ID, 9, "idem-2")

    assert result.tax == 0
    assert result.net == 9
    assert result.vault_entry_id is None
    assert result.player_entry_id == player_result.ledger_entry_id

    # earn() called with full gross
    mock_earn.assert_called_once()
    assert mock_earn.call_args.kwargs["amount"] == 9


@pytest.mark.asyncio
@patch(f"{VAULT_MODULE}._idempo_store", new_callable=AsyncMock)
@patch(f"{VAULT_MODULE}._idempo_get", new_callable=AsyncMock, return_value=None)
@patch(f"{VAULT_MODULE}._append_ledger", new_callable=AsyncMock)
@patch(f"{VAULT_MODULE}._lock_wallet", new_callable=AsyncMock)
@patch(f"{VAULT_MODULE}._get_or_create_wallet", new_callable=AsyncMock)
@patch(f"{VAULT_MODULE}.earn", new_callable=AsyncMock)
async def test_earn_with_tax_exact_boundary(
    mock_earn, mock_get_wallet, mock_lock, mock_append, mock_idem_get, mock_idem_store,
):
    """Tax on amount=10 at 10% → tax=1, net=9."""
    wallet = _make_wallet(balance=0)
    mock_get_wallet.return_value = wallet
    mock_lock.return_value = wallet
    mock_append.return_value = _make_ledger_entry()
    mock_earn.return_value = LedgerResult(wallet_balance=9, wallet_reserved=0, ledger_entry_id=str(uuid4()))

    session = AsyncMock()
    svc = FamilyVaultService(_make_config(tax_rate=10))

    result = await svc.earn_with_tax(session, PLAYER_ID, FAMILY_ID, 10, "idem-3")

    assert result.tax == 1
    assert result.net == 9


@pytest.mark.asyncio
@patch(f"{VAULT_MODULE}._idempo_store", new_callable=AsyncMock)
@patch(f"{VAULT_MODULE}._idempo_get", new_callable=AsyncMock, return_value=None)
@patch(f"{VAULT_MODULE}._append_ledger", new_callable=AsyncMock)
@patch(f"{VAULT_MODULE}._lock_wallet", new_callable=AsyncMock)
@patch(f"{VAULT_MODULE}._get_or_create_wallet", new_callable=AsyncMock)
@patch(f"{VAULT_MODULE}.earn", new_callable=AsyncMock)
async def test_earn_with_tax_uses_floor(
    mock_earn, mock_get_wallet, mock_lock, mock_append, mock_idem_get, mock_idem_store,
):
    """Tax uses floor: 15 * 10 / 100 = 1.5 → floor = 1."""
    wallet = _make_wallet(balance=0)
    mock_get_wallet.return_value = wallet
    mock_lock.return_value = wallet
    mock_append.return_value = _make_ledger_entry()
    mock_earn.return_value = LedgerResult(wallet_balance=14, wallet_reserved=0, ledger_entry_id=str(uuid4()))

    session = AsyncMock()
    svc = FamilyVaultService(_make_config(tax_rate=10))

    result = await svc.earn_with_tax(session, PLAYER_ID, FAMILY_ID, 15, "idem-4")

    assert result.tax == 1
    assert result.net == 14
    assert result.net + result.tax == 15


@pytest.mark.asyncio
@patch(f"{VAULT_MODULE}._idempo_store", new_callable=AsyncMock)
@patch(f"{VAULT_MODULE}._idempo_get", new_callable=AsyncMock, return_value=None)
@patch(f"{VAULT_MODULE}._append_ledger", new_callable=AsyncMock)
@patch(f"{VAULT_MODULE}._lock_wallet", new_callable=AsyncMock)
@patch(f"{VAULT_MODULE}._get_or_create_wallet", new_callable=AsyncMock)
@patch(f"{VAULT_MODULE}.earn", new_callable=AsyncMock)
async def test_earn_with_tax_idempotency_keys_suffixed(
    mock_earn, mock_get_wallet, mock_lock, mock_append, mock_idem_get, mock_idem_store,
):
    """Tax and net entries use suffixed idempotency keys."""
    wallet = _make_wallet(balance=0)
    mock_get_wallet.return_value = wallet
    mock_lock.return_value = wallet
    mock_append.return_value = _make_ledger_entry()
    mock_earn.return_value = LedgerResult(wallet_balance=90, wallet_reserved=0, ledger_entry_id=str(uuid4()))

    session = AsyncMock()
    svc = FamilyVaultService(_make_config(tax_rate=10))

    await svc.earn_with_tax(session, PLAYER_ID, FAMILY_ID, 100, "base-key")

    # TAX entry uses ":tax" suffix
    tax_idem = mock_append.call_args.kwargs["idempotency_key"]
    assert tax_idem == "base-key:tax"

    # Player EARN uses ":net" suffix
    earn_idem = mock_earn.call_args.kwargs["idempotency_key"]
    assert earn_idem == "base-key:net"


# ---------------------------------------------------------------------------
# withdraw tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_withdraw_non_don_raises_insufficient_permission():
    """Non-Don actor cannot withdraw."""
    session = AsyncMock()
    soldier = _make_member(PLAYER_ID, FAMILY_ID, FamilyRole.SOLDIER)

    # Mock the session.execute to return the soldier member
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = soldier
    session.execute = AsyncMock(return_value=result_mock)

    svc = FamilyVaultService(_make_config())

    with pytest.raises(InsufficientPermission):
        await svc.withdraw(session, PLAYER_ID, FAMILY_ID, TARGET_ID, 100, "idem-w1")


@pytest.mark.asyncio
async def test_withdraw_invalid_target_raises():
    """Withdrawal to non-member raises InvalidTargetMember."""
    session = AsyncMock()
    don = _make_member(PLAYER_ID, FAMILY_ID, FamilyRole.DON)

    call_count = 0

    async def _mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.scalar_one_or_none.return_value = don  # actor is Don
        else:
            result.scalar_one_or_none.return_value = None  # target not found
        return result

    session.execute = _mock_execute

    svc = FamilyVaultService(_make_config())

    with pytest.raises(InvalidTargetMember):
        await svc.withdraw(session, PLAYER_ID, FAMILY_ID, TARGET_ID, 100, "idem-w2")


@pytest.mark.asyncio
@patch(f"{VAULT_MODULE}.transfer", new_callable=AsyncMock)
async def test_withdraw_success(mock_transfer):
    """Successful withdrawal transfers from vault to target."""
    session = AsyncMock()
    don = _make_member(PLAYER_ID, FAMILY_ID, FamilyRole.DON)
    target = _make_member(TARGET_ID, FAMILY_ID, FamilyRole.SOLDIER)

    call_count = 0

    async def _mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.scalar_one_or_none.return_value = don
        else:
            result.scalar_one_or_none.return_value = target
        return result

    session.execute = _mock_execute

    mock_transfer.return_value = TransferResult(
        from_balance=900, to_balance=100, debit_entry_id=str(uuid4()), credit_entry_id=str(uuid4()),
    )

    svc = FamilyVaultService(_make_config())
    result = await svc.withdraw(session, PLAYER_ID, FAMILY_ID, TARGET_ID, 100, "idem-w3")

    assert isinstance(result, WithdrawResult)
    assert result.from_balance == 900
    assert result.to_balance == 100

    mock_transfer.assert_called_once()
    t_kwargs = mock_transfer.call_args.kwargs
    assert t_kwargs["from_owner_type"] == OwnerType.FAMILY
    assert t_kwargs["to_owner_type"] == OwnerType.PLAYER
    assert t_kwargs["amount"] == 100


@pytest.mark.asyncio
@patch(f"{VAULT_MODULE}.transfer", new_callable=AsyncMock)
async def test_withdraw_insufficient_funds_raises(mock_transfer):
    """InsufficientFunds from ledger.transfer → InsufficientVaultFunds."""
    session = AsyncMock()
    don = _make_member(PLAYER_ID, FAMILY_ID, FamilyRole.DON)
    target = _make_member(TARGET_ID, FAMILY_ID, FamilyRole.SOLDIER)

    call_count = 0

    async def _mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.scalar_one_or_none.return_value = don
        else:
            result.scalar_one_or_none.return_value = target
        return result

    session.execute = _mock_execute
    mock_transfer.side_effect = InsufficientFunds("Not enough balance.")

    svc = FamilyVaultService(_make_config())

    with pytest.raises(InsufficientVaultFunds):
        await svc.withdraw(session, PLAYER_ID, FAMILY_ID, TARGET_ID, 10000, "idem-w4")


# ---------------------------------------------------------------------------
# get_vault_balance tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_vault_balance_returns_balance():
    """Returns wallet balance when wallet exists."""
    session = AsyncMock()
    wallet = _make_wallet(balance=5000)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = wallet
    session.execute = AsyncMock(return_value=result_mock)

    svc = FamilyVaultService(_make_config())
    balance = await svc.get_vault_balance(session, FAMILY_ID)

    assert balance == 5000


@pytest.mark.asyncio
async def test_get_vault_balance_returns_zero_when_no_wallet():
    """Returns 0 when no wallet exists for the family."""
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result_mock)

    svc = FamilyVaultService(_make_config())
    balance = await svc.get_vault_balance(session, FAMILY_ID)

    assert balance == 0
