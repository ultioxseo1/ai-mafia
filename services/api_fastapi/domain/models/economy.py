
from __future__ import annotations
import enum
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    String,
    UniqueConstraint,
    BigInteger,
    JSON,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class OwnerType(str, enum.Enum):
    PLAYER = "PLAYER"
    FAMILY = "FAMILY"
    SYSTEM = "SYSTEM"


class Currency(str, enum.Enum):
    CASH = "CASH"
    DIAMOND = "DIAMOND"
    BULLET = "BULLET"


class LedgerEntryType(str, enum.Enum):
    RESERVE = "RESERVE"
    CAPTURE = "CAPTURE"
    RELEASE = "RELEASE"
    EARN = "EARN"
    SPEND = "SPEND"
    TAX = "TAX"
    TRANSFER = "TRANSFER"


class LedgerEntryStatus(str, enum.Enum):
    PENDING = "PENDING"
    POSTED = "POSTED"
    VOID = "VOID"


class Wallet(Base):
    __tablename__ = "wallets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_type: Mapped[OwnerType] = mapped_column(Enum(OwnerType, name="owner_type"), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    currency: Mapped[Currency] = mapped_column(Enum(Currency, name="currency"), nullable=False)

    balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", "currency", name="uq_wallet_owner_currency"),
        CheckConstraint("balance >= 0", name="ck_wallet_balance_nonneg"),
        CheckConstraint("reserved_balance >= 0", name="ck_wallet_reserved_nonneg"),
        Index("ix_wallet_owner", "owner_type", "owner_id"),
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_type: Mapped[OwnerType] = mapped_column(Enum(OwnerType, name="idempo_owner_type"), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    response_body: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", "action", "idempotency_key", name="uq_idempo_scope_key"),
        Index("ix_idempo_lookup", "owner_type", "owner_id", "action", "idempotency_key"),
    )


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_type: Mapped[OwnerType] = mapped_column(Enum(OwnerType, name="ledger_owner_type"), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    currency: Mapped[Currency] = mapped_column(Enum(Currency, name="ledger_currency"), nullable=False)
    entry_type: Mapped[LedgerEntryType] = mapped_column(Enum(LedgerEntryType, name="ledger_entry_type"), nullable=False)
    status: Mapped[LedgerEntryStatus] = mapped_column(Enum(LedgerEntryStatus, name="ledger_entry_status"), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reference_id: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_metadata: Mapped[Dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    counterparty_owner_type: Mapped[Optional[OwnerType]] = mapped_column(
        Enum(OwnerType, name="ledger_counterparty_owner_type"), nullable=True
    )
    counterparty_owner_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_ledger_amount_positive"),
        Index("ix_ledger_owner_currency_time", "owner_type", "owner_id", "currency", "created_at"),
        Index("ix_ledger_reference", "reference_id"),
    )
