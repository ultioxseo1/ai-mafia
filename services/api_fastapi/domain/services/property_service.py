"""
services/api_fastapi/domain/services/property_service.py

AI MAFIA — Property Service

Handles Property purchase, upgrade, income calculation, and listing.
All financial mutations flow through the immutable Ledger Service.

Requirements: 8.1–8.6, 9.1–9.7
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api_fastapi.domain.models.economy import Currency, OwnerType
from services.api_fastapi.domain.models.family import (
    FamilyMember,
    FamilyProperty,
    FamilyRole,
    PropertyDefinition,
    load_property_definitions,
)
from services.api_fastapi.domain.services.config_service import ConfigService
from services.api_fastapi.domain.services.ledger_service import InsufficientFunds, spend
from services.api_fastapi.domain.services.vault_service import (
    InsufficientPermission,
    InsufficientVaultFunds,
)


# ---------------------------------------------------------------------------
# Custom errors
# ---------------------------------------------------------------------------


class AlreadyOwned(Exception):
    """Family already owns this property."""


class MaxLevelReached(Exception):
    """Property is at its maximum upgrade level."""


class PropertyNotFound(Exception):
    """Property ID not found in config definitions."""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class PropertyOwnership:
    family_id: UUID
    property_id: str
    name: str
    level: int
    daily_income: int


# ---------------------------------------------------------------------------
# PropertyService
# ---------------------------------------------------------------------------


class PropertyService:
    """Property purchase, upgrade, income calculation, and listing."""

    def __init__(self, config: ConfigService) -> None:
        self._config = config

    # -- Purchase -----------------------------------------------------------

    async def purchase_property(
        self,
        session: AsyncSession,
        actor_id: UUID,
        family_id: UUID,
        property_id: str,
        idempotency_key: str,
    ) -> PropertyOwnership:
        """
        Purchase a property for the family. Only the Don may purchase.

        Requirements: 8.2, 8.3, 8.4, 8.5, 8.6
        """
        # 1. Verify actor is Don
        await self._require_don(session, actor_id, family_id)

        # 2. Load property definition from config
        prop_def = await self._get_property_def(property_id)

        # 3. Check family doesn't already own this property
        existing = await self._get_ownership(session, family_id, property_id)
        if existing is not None:
            raise AlreadyOwned(f"Family already owns property '{property_id}'.")

        # 4. Deduct purchase price from family vault
        try:
            await spend(
                session,
                owner_type=OwnerType.FAMILY,
                owner_id=family_id,
                currency=Currency.CASH,
                amount=prop_def.purchase_price,
                reference_id=property_id,
                metadata={"source": "property_purchase", "property_id": property_id},
                idempotency_key=idempotency_key,
            )
        except InsufficientFunds:
            raise InsufficientVaultFunds(
                "Family Vault does not have enough CASH to purchase this property."
            )

        # 5. Create FamilyProperty record at level 1
        now = datetime.utcnow()
        fp = FamilyProperty(
            family_id=family_id,
            property_id=property_id,
            level=1,
            purchased_at=now,
            updated_at=now,
        )
        session.add(fp)
        await session.flush()

        return PropertyOwnership(
            family_id=family_id,
            property_id=property_id,
            name=prop_def.name,
            level=1,
            daily_income=prop_def.daily_income,
        )

    # -- Upgrade ------------------------------------------------------------

    async def upgrade_property(
        self,
        session: AsyncSession,
        actor_id: UUID,
        family_id: UUID,
        property_id: str,
        idempotency_key: str,
    ) -> PropertyOwnership:
        """
        Upgrade a family-owned property by one level. Only the Don may upgrade.

        Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7
        """
        # 1. Verify actor is Don
        await self._require_don(session, actor_id, family_id)

        # 2. Load ownership record
        ownership = await self._get_ownership(session, family_id, property_id)
        if ownership is None:
            raise PropertyNotFound(f"Family does not own property '{property_id}'.")

        # 3. Load property definition to get max_level and purchase_price
        prop_def = await self._get_property_def(property_id)

        # 4. Check not at max level
        if ownership.level >= prop_def.max_level:
            raise MaxLevelReached(
                f"Property '{property_id}' is already at max level {prop_def.max_level}."
            )

        # 5. Calculate upgrade cost: purchase_price * current_level
        cost = prop_def.purchase_price * ownership.level

        # 6. Deduct cost from family vault
        try:
            await spend(
                session,
                owner_type=OwnerType.FAMILY,
                owner_id=family_id,
                currency=Currency.CASH,
                amount=cost,
                reference_id=property_id,
                metadata={
                    "source": "property_upgrade",
                    "property_id": property_id,
                    "from_level": ownership.level,
                    "to_level": ownership.level + 1,
                },
                idempotency_key=idempotency_key,
            )
        except InsufficientFunds:
            raise InsufficientVaultFunds(
                "Family Vault does not have enough CASH to upgrade this property."
            )

        # 7. Increment level and update timestamp
        ownership.level += 1
        ownership.updated_at = datetime.utcnow()
        await session.flush()

        new_daily_income = prop_def.daily_income * ownership.level

        return PropertyOwnership(
            family_id=family_id,
            property_id=property_id,
            name=prop_def.name,
            level=ownership.level,
            daily_income=new_daily_income,
        )

    # -- Income calculation -------------------------------------------------

    async def calculate_daily_income(
        self,
        session: AsyncSession,
        family_id: UUID,
    ) -> int:
        """
        Sum of (base_daily_income * level) for all owned properties.

        Requirements: 10.2
        """
        defs = await load_property_definitions(self._config)
        def_map = {d.property_id: d for d in defs}

        owned = await self.list_family_properties(session, family_id)
        total = 0
        for fp in owned:
            prop_def = def_map.get(fp.property_id)
            if prop_def is not None:
                total += prop_def.daily_income * fp.level
        return total

    # -- Listing ------------------------------------------------------------

    async def list_properties(self, config: ConfigService) -> List[PropertyDefinition]:
        """Return all property definitions from config."""
        return await load_property_definitions(config)

    async def list_family_properties(
        self,
        session: AsyncSession,
        family_id: UUID,
    ) -> List[FamilyProperty]:
        """Return all properties owned by the given family."""
        stmt = select(FamilyProperty).where(
            FamilyProperty.family_id == family_id,
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    # -- Internal helpers ---------------------------------------------------

    async def _require_don(
        self,
        session: AsyncSession,
        actor_id: UUID,
        family_id: UUID,
    ) -> FamilyMember:
        """Verify the actor is the Don of the given family. Raises InsufficientPermission."""
        stmt = select(FamilyMember).where(
            FamilyMember.player_id == actor_id,
            FamilyMember.family_id == family_id,
        )
        result = await session.execute(stmt)
        member = result.scalar_one_or_none()
        if member is None or member.role != FamilyRole.DON:
            raise InsufficientPermission(
                "Only the Don can perform this property operation."
            )
        return member

    async def _get_property_def(self, property_id: str) -> PropertyDefinition:
        """Load a single PropertyDefinition by ID. Raises PropertyNotFound."""
        defs = await load_property_definitions(self._config)
        for d in defs:
            if d.property_id == property_id:
                return d
        raise PropertyNotFound(f"Property '{property_id}' not found in definitions.")

    @staticmethod
    async def _get_ownership(
        session: AsyncSession,
        family_id: UUID,
        property_id: str,
    ) -> FamilyProperty | None:
        """Look up a FamilyProperty record."""
        stmt = select(FamilyProperty).where(
            FamilyProperty.family_id == family_id,
            FamilyProperty.property_id == property_id,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
