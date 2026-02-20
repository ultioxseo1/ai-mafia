"""
services/api_fastapi/domain/services/ledger_service.py

AI MAFIA — Immutable Ledger Service (rules.md compliant)

What this does:
- Single entrypoint for economy mutations
- Reserve/Capture/Release with:
  - idempotency (safe retries)
  - row-level lock on wallet
  - single DB transaction
  - append-only ledger rows (no delete; no in-place updates on ledger rows)

Assumptions:
- Models live in: services/api_fastapi/domain/models/economy.py
- SQLAlchemy 2.0 style, AsyncSession
- Amounts are integer minor units (BigInteger in DB)

Key invariants:
- Do NOT update wallet balances anywhere else.
- Always lock the wallet row (SELECT ... FOR UPDATE) inside transaction.
- IdempotencyKey table is the canonical replay cache.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api_fastapi.domain.models.economy import (
    Wallet,
    LedgerEntry,
    IdempotencyKey,
    OwnerType,
    Currency,
    LedgerEntryType,
    LedgerEntryStatus,
)


# ---------------------------
# Errors
# ---------------------------

class LedgerError(Exception):
    pass

class InsufficientFunds(LedgerError):
    pass

class IdempotencyConflict(LedgerError):
    pass

class ReserveNotFound(LedgerError):
    pass


# ---------------------------
# DTOs
# ---------------------------

@dataclass(frozen=True)
class LedgerResult:
    wallet_balance: int
    wallet_reserved: int
    ledger_entry_id: str


@dataclass(frozen=True)
class TransferResult:
    from_balance: int
    to_balance: int
    debit_entry_id: str
    credit_entry_id: str


# ---------------------------
# Helpers
# ---------------------------

def _fingerprint(payload: Dict[str, Any]) -> str:
    """Stable fingerprint to ensure the same Idempotency-Key can't be reused with different payload."""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


async def _get_or_create_wallet(
    session: AsyncSession,
    owner_type: OwnerType,
    owner_id,
    currency: Currency,
) -> Wallet:
    stmt = select(Wallet).where(
        Wallet.owner_type == owner_type,
        Wallet.owner_id == owner_id,
        Wallet.currency == currency,
        Wallet.is_active == True,  # noqa: E712
    )
    res = await session.execute(stmt)
    w = res.scalar_one_or_none()
    if w:
        return w

    w = Wallet(owner_type=owner_type, owner_id=owner_id, currency=currency, balance=0, reserved_balance=0, is_active=True)
    session.add(w)
    await session.flush()
    return w


async def _lock_wallet(session: AsyncSession, wallet_id) -> Wallet:
    """Row lock for concurrency safety."""
    res = await session.execute(select(Wallet).where(Wallet.id == wallet_id).with_for_update())
    return res.scalar_one()


async def _idempo_get(
    session: AsyncSession,
    owner_type: OwnerType,
    owner_id,
    action: str,
    idem_key: str,
) -> Optional[IdempotencyKey]:
    stmt = select(IdempotencyKey).where(
        IdempotencyKey.owner_type == owner_type,
        IdempotencyKey.owner_id == owner_id,
        IdempotencyKey.action == action,
        IdempotencyKey.idempotency_key == idem_key,
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def _idempo_store(
    session: AsyncSession,
    owner_type: OwnerType,
    owner_id,
    action: str,
    idem_key: str,
    request_fingerprint: str,
    response_body: Dict[str, Any],
) -> None:
    row = IdempotencyKey(
        owner_type=owner_type,
        owner_id=owner_id,
        action=action,
        idempotency_key=idem_key,
        request_fingerprint=request_fingerprint,
        response_body=response_body,
    )
    session.add(row)
    await session.flush()


async def _append_ledger(
    session: AsyncSession,
    *,
    owner_type: OwnerType,
    owner_id,
    currency: Currency,
    entry_type: LedgerEntryType,
    status: LedgerEntryStatus,
    amount: int,
    reference_id: str,
    metadata: Dict[str, Any],
    idempotency_key: str,
    counterparty_owner_type: Optional[OwnerType] = None,
    counterparty_owner_id=None,
) -> LedgerEntry:
    le = LedgerEntry(
        owner_type=owner_type,
        owner_id=owner_id,
        currency=currency,
        entry_type=entry_type,
        status=status,
        amount=amount,
        reference_id=reference_id,
        metadata=metadata or {},
        idempotency_key=idempotency_key,
        counterparty_owner_type=counterparty_owner_type,
        counterparty_owner_id=counterparty_owner_id,
    )
    session.add(le)
    await session.flush()
    return le


async def _find_latest_pending_reserve(
    session: AsyncSession,
    owner_type: OwnerType,
    owner_id,
    currency: Currency,
    reference_id: str,
) -> Optional[LedgerEntry]:
    stmt = (
        select(LedgerEntry)
        .where(
            LedgerEntry.owner_type == owner_type,
            LedgerEntry.owner_id == owner_id,
            LedgerEntry.currency == currency,
            LedgerEntry.entry_type == LedgerEntryType.RESERVE,
            LedgerEntry.status == LedgerEntryStatus.PENDING,
            LedgerEntry.reference_id == reference_id,
        )
        .order_by(LedgerEntry.created_at.desc())
        .limit(1)
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


# ---------------------------
# Public API (call inside: async with session.begin():)
# ---------------------------

async def reserve(
    session: AsyncSession,
    *,
    owner_type: OwnerType,
    owner_id,
    currency: Currency,
    amount: int,
    reference_id: str,
    metadata: Dict[str, Any],
    idempotency_key: str,
) -> LedgerResult:
    """RESERVE: locks funds into reserved_balance, appends RESERVE(PENDING) row, idempotent."""
    if amount <= 0:
        raise ValueError("amount must be > 0")

    action = "ledger.reserve"
    payload = {
        "owner_type": owner_type,
        "owner_id": str(owner_id),
        "currency": currency,
        "amount": amount,
        "reference_id": reference_id,
        "metadata": metadata or {},
    }
    fp = _fingerprint(payload)

    existing = await _idempo_get(session, owner_type, owner_id, action, idempotency_key)
    if existing:
        if existing.request_fingerprint != fp:
            raise IdempotencyConflict("Idempotency-Key reused with different payload.")
        rb = existing.response_body
        return LedgerResult(rb["wallet_balance"], rb["wallet_reserved"], rb["ledger_entry_id"])

    wallet = await _get_or_create_wallet(session, owner_type, owner_id, currency)
    wallet = await _lock_wallet(session, wallet.id)

    available = wallet.balance - wallet.reserved_balance
    if available < amount:
        raise InsufficientFunds("Not enough available balance to reserve.")

    wallet.reserved_balance += amount

    le = await _append_ledger(
        session,
        owner_type=owner_type,
        owner_id=owner_id,
        currency=currency,
        entry_type=LedgerEntryType.RESERVE,
        status=LedgerEntryStatus.PENDING,
        amount=amount,
        reference_id=reference_id,
        metadata=metadata or {},
        idempotency_key=idempotency_key,
    )

    resp = {"wallet_balance": wallet.balance, "wallet_reserved": wallet.reserved_balance, "ledger_entry_id": str(le.id)}
    await _idempo_store(session, owner_type, owner_id, action, idempotency_key, fp, resp)
    return LedgerResult(resp["wallet_balance"], resp["wallet_reserved"], resp["ledger_entry_id"])


async def capture(
    session: AsyncSession,
    *,
    owner_type: OwnerType,
    owner_id,
    currency: Currency,
    amount: int,
    reference_id: str,
    metadata: Dict[str, Any],
    idempotency_key: str,
) -> LedgerResult:
    """CAPTURE: consumes reserved funds and balance, appends CAPTURE(POSTED) row, idempotent."""
    if amount <= 0:
        raise ValueError("amount must be > 0")

    action = "ledger.capture"
    payload = {
        "owner_type": owner_type,
        "owner_id": str(owner_id),
        "currency": currency,
        "amount": amount,
        "reference_id": reference_id,
        "metadata": metadata or {},
    }
    fp = _fingerprint(payload)

    existing = await _idempo_get(session, owner_type, owner_id, action, idempotency_key)
    if existing:
        if existing.request_fingerprint != fp:
            raise IdempotencyConflict("Idempotency-Key reused with different payload.")
        rb = existing.response_body
        return LedgerResult(rb["wallet_balance"], rb["wallet_reserved"], rb["ledger_entry_id"])

    wallet = await _get_or_create_wallet(session, owner_type, owner_id, currency)
    wallet = await _lock_wallet(session, wallet.id)

    reserve_entry = await _find_latest_pending_reserve(session, owner_type, owner_id, currency, reference_id)
    if not reserve_entry:
        raise ReserveNotFound("No pending RESERVE found for reference_id.")

    if wallet.reserved_balance < amount:
        raise InsufficientFunds("Reserved balance insufficient to capture.")
    if wallet.balance < amount:
        raise InsufficientFunds("Wallet balance insufficient to capture.")

    wallet.reserved_balance -= amount
    wallet.balance -= amount

    le = await _append_ledger(
        session,
        owner_type=owner_type,
        owner_id=owner_id,
        currency=currency,
        entry_type=LedgerEntryType.CAPTURE,
        status=LedgerEntryStatus.POSTED,
        amount=amount,
        reference_id=reference_id,
        metadata=metadata or {},
        idempotency_key=idempotency_key,
    )

    resp = {"wallet_balance": wallet.balance, "wallet_reserved": wallet.reserved_balance, "ledger_entry_id": str(le.id)}
    await _idempo_store(session, owner_type, owner_id, action, idempotency_key, fp, resp)
    return LedgerResult(resp["wallet_balance"], resp["wallet_reserved"], resp["ledger_entry_id"])


async def release(
    session: AsyncSession,
    *,
    owner_type: OwnerType,
    owner_id,
    currency: Currency,
    amount: int,
    reference_id: str,
    metadata: Dict[str, Any],
    idempotency_key: str,
) -> LedgerResult:
    """RELEASE: unlocks reserved funds, appends RELEASE(VOID) row, idempotent."""
    if amount <= 0:
        raise ValueError("amount must be > 0")

    action = "ledger.release"
    payload = {
        "owner_type": owner_type,
        "owner_id": str(owner_id),
        "currency": currency,
        "amount": amount,
        "reference_id": reference_id,
        "metadata": metadata or {},
    }
    fp = _fingerprint(payload)

    existing = await _idempo_get(session, owner_type, owner_id, action, idempotency_key)
    if existing:
        if existing.request_fingerprint != fp:
            raise IdempotencyConflict("Idempotency-Key reused with different payload.")
        rb = existing.response_body
        return LedgerResult(rb["wallet_balance"], rb["wallet_reserved"], rb["ledger_entry_id"])

    wallet = await _get_or_create_wallet(session, owner_type, owner_id, currency)
    wallet = await _lock_wallet(session, wallet.id)

    reserve_entry = await _find_latest_pending_reserve(session, owner_type, owner_id, currency, reference_id)
    if not reserve_entry:
        raise ReserveNotFound("No pending RESERVE found for reference_id.")

    if wallet.reserved_balance < amount:
        raise InsufficientFunds("Reserved balance insufficient to release.")

    wallet.reserved_balance -= amount

    le = await _append_ledger(
        session,
        owner_type=owner_type,
        owner_id=owner_id,
        currency=currency,
        entry_type=LedgerEntryType.RELEASE,
        status=LedgerEntryStatus.VOID,
        amount=amount,
        reference_id=reference_id,
        metadata=metadata or {},
        idempotency_key=idempotency_key,
    )

    resp = {"wallet_balance": wallet.balance, "wallet_reserved": wallet.reserved_balance, "ledger_entry_id": str(le.id)}
    await _idempo_store(session, owner_type, owner_id, action, idempotency_key, fp, resp)
    return LedgerResult(resp["wallet_balance"], resp["wallet_reserved"], resp["ledger_entry_id"])

async def earn(
    session: AsyncSession,
    *,
    owner_type: OwnerType,
    owner_id,
    currency: Currency,
    amount: int,
    reference_id: str,
    metadata: Dict[str, Any],
    idempotency_key: str,
) -> LedgerResult:
    """EARN: direct credit — increases wallet balance, appends EARN(POSTED) row, idempotent."""
    if amount <= 0:
        raise ValueError("amount must be > 0")

    action = "ledger.earn"
    payload = {
        "owner_type": owner_type,
        "owner_id": str(owner_id),
        "currency": currency,
        "amount": amount,
        "reference_id": reference_id,
        "metadata": metadata or {},
    }
    fp = _fingerprint(payload)

    existing = await _idempo_get(session, owner_type, owner_id, action, idempotency_key)
    if existing:
        if existing.request_fingerprint != fp:
            raise IdempotencyConflict("Idempotency-Key reused with different payload.")
        rb = existing.response_body
        return LedgerResult(rb["wallet_balance"], rb["wallet_reserved"], rb["ledger_entry_id"])

    wallet = await _get_or_create_wallet(session, owner_type, owner_id, currency)
    wallet = await _lock_wallet(session, wallet.id)

    wallet.balance += amount

    le = await _append_ledger(
        session,
        owner_type=owner_type,
        owner_id=owner_id,
        currency=currency,
        entry_type=LedgerEntryType.EARN,
        status=LedgerEntryStatus.POSTED,
        amount=amount,
        reference_id=reference_id,
        metadata=metadata or {},
        idempotency_key=idempotency_key,
    )

    resp = {"wallet_balance": wallet.balance, "wallet_reserved": wallet.reserved_balance, "ledger_entry_id": str(le.id)}
    await _idempo_store(session, owner_type, owner_id, action, idempotency_key, fp, resp)
    return LedgerResult(resp["wallet_balance"], resp["wallet_reserved"], resp["ledger_entry_id"])

async def spend(
    session: AsyncSession,
    *,
    owner_type: OwnerType,
    owner_id,
    currency: Currency,
    amount: int,
    reference_id: str,
    metadata: Dict[str, Any],
    idempotency_key: str,
) -> LedgerResult:
    """SPEND: debit wallet balance, append SPEND(POSTED) row, idempotent.
    Raises InsufficientFunds if balance < amount."""
    if amount <= 0:
        raise ValueError("amount must be > 0")

    action = "ledger.spend"
    payload = {
        "owner_type": owner_type,
        "owner_id": str(owner_id),
        "currency": currency,
        "amount": amount,
        "reference_id": reference_id,
        "metadata": metadata or {},
    }
    fp = _fingerprint(payload)

    existing = await _idempo_get(session, owner_type, owner_id, action, idempotency_key)
    if existing:
        if existing.request_fingerprint != fp:
            raise IdempotencyConflict("Idempotency-Key reused with different payload.")
        rb = existing.response_body
        return LedgerResult(rb["wallet_balance"], rb["wallet_reserved"], rb["ledger_entry_id"])

    wallet = await _get_or_create_wallet(session, owner_type, owner_id, currency)
    wallet = await _lock_wallet(session, wallet.id)

    if wallet.balance < amount:
        raise InsufficientFunds("Not enough balance to spend.")

    wallet.balance -= amount

    le = await _append_ledger(
        session,
        owner_type=owner_type,
        owner_id=owner_id,
        currency=currency,
        entry_type=LedgerEntryType.SPEND,
        status=LedgerEntryStatus.POSTED,
        amount=amount,
        reference_id=reference_id,
        metadata=metadata or {},
        idempotency_key=idempotency_key,
    )

    resp = {"wallet_balance": wallet.balance, "wallet_reserved": wallet.reserved_balance, "ledger_entry_id": str(le.id)}
    await _idempo_store(session, owner_type, owner_id, action, idempotency_key, fp, resp)
    return LedgerResult(resp["wallet_balance"], resp["wallet_reserved"], resp["ledger_entry_id"])



async def transfer(
    session: AsyncSession,
    *,
    from_owner_type: OwnerType,
    from_owner_id,
    to_owner_type: OwnerType,
    to_owner_id,
    currency: Currency,
    amount: int,
    reference_id: str,
    metadata: Dict[str, Any],
    idempotency_key: str,
) -> TransferResult:
    """TRANSFER: debit source wallet, credit target wallet,
    append two TRANSFER(POSTED) rows (one debit, one credit), idempotent.
    Raises InsufficientFunds if source balance < amount."""
    if amount <= 0:
        raise ValueError("amount must be > 0")

    action = "ledger.transfer"
    payload = {
        "from_owner_type": from_owner_type,
        "from_owner_id": str(from_owner_id),
        "to_owner_type": to_owner_type,
        "to_owner_id": str(to_owner_id),
        "currency": currency,
        "amount": amount,
        "reference_id": reference_id,
        "metadata": metadata or {},
    }
    fp = _fingerprint(payload)

    # Idempotency check — keyed on the source owner
    existing = await _idempo_get(session, from_owner_type, from_owner_id, action, idempotency_key)
    if existing:
        if existing.request_fingerprint != fp:
            raise IdempotencyConflict("Idempotency-Key reused with different payload.")
        rb = existing.response_body
        return TransferResult(rb["from_balance"], rb["to_balance"], rb["debit_entry_id"], rb["credit_entry_id"])

    # Lock source wallet, verify balance
    src_wallet = await _get_or_create_wallet(session, from_owner_type, from_owner_id, currency)
    src_wallet = await _lock_wallet(session, src_wallet.id)

    if src_wallet.balance < amount:
        raise InsufficientFunds("Not enough balance to transfer.")

    src_wallet.balance -= amount

    # Lock/create target wallet, credit
    tgt_wallet = await _get_or_create_wallet(session, to_owner_type, to_owner_id, currency)
    tgt_wallet = await _lock_wallet(session, tgt_wallet.id)

    tgt_wallet.balance += amount

    # Debit entry (source side) — counterparty is the target
    debit_le = await _append_ledger(
        session,
        owner_type=from_owner_type,
        owner_id=from_owner_id,
        currency=currency,
        entry_type=LedgerEntryType.TRANSFER,
        status=LedgerEntryStatus.POSTED,
        amount=amount,
        reference_id=reference_id,
        metadata=metadata or {},
        idempotency_key=idempotency_key,
        counterparty_owner_type=to_owner_type,
        counterparty_owner_id=to_owner_id,
    )

    # Credit entry (target side) — counterparty is the source
    credit_le = await _append_ledger(
        session,
        owner_type=to_owner_type,
        owner_id=to_owner_id,
        currency=currency,
        entry_type=LedgerEntryType.TRANSFER,
        status=LedgerEntryStatus.POSTED,
        amount=amount,
        reference_id=reference_id,
        metadata=metadata or {},
        idempotency_key=idempotency_key,
        counterparty_owner_type=from_owner_type,
        counterparty_owner_id=from_owner_id,
    )

    resp = {
        "from_balance": src_wallet.balance,
        "to_balance": tgt_wallet.balance,
        "debit_entry_id": str(debit_le.id),
        "credit_entry_id": str(credit_le.id),
    }
    await _idempo_store(session, from_owner_type, from_owner_id, action, idempotency_key, fp, resp)
    return TransferResult(resp["from_balance"], resp["to_balance"], resp["debit_entry_id"], resp["credit_entry_id"])

