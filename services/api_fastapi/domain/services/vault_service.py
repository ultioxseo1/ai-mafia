"""
services/api_fastapi/domain/services/vault_service.py

AI MAFIA — Family Vault Service

Handles automatic tax collection on member CASH earnings and Don-authorized
withdrawals from the Family Vault.  All financial mutations flow through the
immutable Ledger Service.

Requirements: 5.1–5.6, 6.1–6.5
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api_fastapi.domain.models.economy import (
    Currency,
    LedgerEntryStatus,
    LedgerEntryType,
    OwnerType,
    Wallet,
)
from services.api_fastapi.domain.models.family import FamilyMember, FamilyRole
from services.api_fastapi.domain.services.config_service import (
    VAULT_TAX_RATE,
    ConfigService,
)
from services.api_fastapi.domain.services.ledger_service import (
    InsufficientFunds,
    LedgerResult,
    TransferResult,
    _append_ledger,
    _fingerprint,
    _get_or_create_wallet,
    _idempo_get,
    _idempo_store,
    _lock_wallet,
    earn,
    transfer,
)


# ---------------------------------------------------------------------------
# Custom errors
# ---------------------------------------------------------------------------


class InsufficientPermission(Exception):
    """Actor does not have the required role for this operation."""


class InsufficientVaultFunds(Exception):
    """Family Vault balance is too low for the requested operation."""


class InvalidTargetMember(Exception):
    """Withdrawal target is not a current member of the family."""


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TaxResult:
    net: int
    tax: int
    player_entry_id: str
    vault_entry_id: Optional[str]


@dataclass
class WithdrawResult:
    from_balance: int
    to_balance: int


# ---------------------------------------------------------------------------
# FamilyVaultService
# ---------------------------------------------------------------------------


class FamilyVaultService:
    """Tax collection on earnings and Don-authorized vault withdrawals."""

    def __init__(self, config: ConfigService) -> None:
        self._config = config

    # -- Tax collection -----------------------------------------------------

    async def earn_with_tax(
        self,
        session: AsyncSession,
        player_id: UUID,
        family_id: UUID,
        gross_amount: int,
        idempotency_key: str,
    ) -> TaxResult:
        """
        Apply vault tax to a CASH earning and credit both the Family Vault
        (TAX entry) and the player (EARN entry) atomically within the
        caller's DB transaction.

        Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6
        """
        rate = await self._config.get_int(VAULT_TAX_RATE, default=10)
        tax = math.floor(gross_amount * rate / 100)
        net = gross_amount - tax

        if tax > 0:
            # --- Vault TAX credit ---
            vault_wallet = await _get_or_create_wallet(
                session, OwnerType.FAMILY, family_id, Currency.CASH,
            )
            vault_wallet = await _lock_wallet(session, vault_wallet.id)
            vault_wallet.balance += tax

            tax_idem_key = f"{idempotency_key}:tax"

            # Idempotency for the TAX entry (scoped to FAMILY owner)
            action_tax = "vault.tax"
            tax_payload = {
                "owner_type": OwnerType.FAMILY,
                "owner_id": str(family_id),
                "currency": Currency.CASH,
                "amount": tax,
                "player_id": str(player_id),
            }
            tax_fp = _fingerprint(tax_payload)

            existing_tax = await _idempo_get(
                session, OwnerType.FAMILY, family_id, action_tax, tax_idem_key,
            )
            if existing_tax:
                vault_entry_id = existing_tax.response_body["ledger_entry_id"]
            else:
                tax_le = await _append_ledger(
                    session,
                    owner_type=OwnerType.FAMILY,
                    owner_id=family_id,
                    currency=Currency.CASH,
                    entry_type=LedgerEntryType.TAX,
                    status=LedgerEntryStatus.POSTED,
                    amount=tax,
                    reference_id=str(player_id),
                    metadata={"source": "vault_tax", "player_id": str(player_id)},
                    idempotency_key=tax_idem_key,
                    counterparty_owner_type=OwnerType.PLAYER,
                    counterparty_owner_id=player_id,
                )
                vault_entry_id = str(tax_le.id)
                await _idempo_store(
                    session, OwnerType.FAMILY, family_id, action_tax,
                    tax_idem_key, tax_fp,
                    {"ledger_entry_id": vault_entry_id},
                )

            # --- Player EARN credit (net amount) ---
            net_idem_key = f"{idempotency_key}:net"
            player_result: LedgerResult = await earn(
                session,
                owner_type=OwnerType.PLAYER,
                owner_id=player_id,
                currency=Currency.CASH,
                amount=net,
                reference_id=str(family_id),
                metadata={"source": "earn_after_tax", "gross": gross_amount, "tax": tax},
                idempotency_key=net_idem_key,
            )

            return TaxResult(
                net=net,
                tax=tax,
                player_entry_id=player_result.ledger_entry_id,
                vault_entry_id=vault_entry_id,
            )

        # tax == 0 → full gross to player, no vault entry
        player_result = await earn(
            session,
            owner_type=OwnerType.PLAYER,
            owner_id=player_id,
            currency=Currency.CASH,
            amount=gross_amount,
            reference_id=str(family_id),
            metadata={"source": "earn_no_tax", "gross": gross_amount},
            idempotency_key=idempotency_key,
        )

        return TaxResult(
            net=gross_amount,
            tax=0,
            player_entry_id=player_result.ledger_entry_id,
            vault_entry_id=None,
        )

    # -- Withdrawal ---------------------------------------------------------

    async def withdraw(
        self,
        session: AsyncSession,
        actor_id: UUID,
        family_id: UUID,
        target_member_id: UUID,
        amount: int,
        idempotency_key: str,
    ) -> WithdrawResult:
        """
        Transfer CASH from the Family Vault to a target member's wallet.
        Only the Don may perform withdrawals.

        Requirements: 6.1, 6.2, 6.3, 6.4, 6.5
        """
        # 1. Verify actor is Don
        actor_member = await self._get_member(session, actor_id, family_id)
        if actor_member is None or actor_member.role != FamilyRole.DON:
            raise InsufficientPermission("Only the Don can withdraw from the vault.")

        # 2. Verify target is a current family member
        target_member = await self._get_member(session, target_member_id, family_id)
        if target_member is None:
            raise InvalidTargetMember("Target is not a current member of this family.")

        # 3. Execute transfer via ledger
        try:
            result: TransferResult = await transfer(
                session,
                from_owner_type=OwnerType.FAMILY,
                from_owner_id=family_id,
                to_owner_type=OwnerType.PLAYER,
                to_owner_id=target_member_id,
                currency=Currency.CASH,
                amount=amount,
                reference_id=str(family_id),
                metadata={
                    "source": "vault_withdrawal",
                    "actor_id": str(actor_id),
                    "target_member_id": str(target_member_id),
                },
                idempotency_key=idempotency_key,
            )
        except InsufficientFunds:
            raise InsufficientVaultFunds("Family Vault does not have enough CASH.")

        return WithdrawResult(
            from_balance=result.from_balance,
            to_balance=result.to_balance,
        )

    # -- Balance query ------------------------------------------------------

    async def get_vault_balance(
        self,
        session: AsyncSession,
        family_id: UUID,
    ) -> int:
        """Return the Family Vault's current CASH balance."""
        stmt = select(Wallet).where(
            Wallet.owner_type == OwnerType.FAMILY,
            Wallet.owner_id == family_id,
            Wallet.currency == Currency.CASH,
            Wallet.is_active == True,  # noqa: E712
        )
        result = await session.execute(stmt)
        wallet = result.scalar_one_or_none()
        if wallet is None:
            return 0
        return wallet.balance

    # -- Internal helpers ---------------------------------------------------

    @staticmethod
    async def _get_member(
        session: AsyncSession,
        player_id: UUID,
        family_id: UUID,
    ) -> Optional[FamilyMember]:
        """Look up a FamilyMember row for the given player in the given family."""
        stmt = select(FamilyMember).where(
            FamilyMember.player_id == player_id,
            FamilyMember.family_id == family_id,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
