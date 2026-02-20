"""
services/api_fastapi/domain/services/family_service.py

AI MAFIA — Family Service

Manages Family CRUD, membership (join/leave/kick), role hierarchy
(promote/demote/transfer-don), dissolution, and read queries.
All financial mutations flow through the immutable Ledger Service.

Requirements: 1.1–1.6, 2.1–2.11, 3.1–3.8, 4.1–4.4, 11.1–11.3
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api_fastapi.domain.models.economy import Currency, OwnerType
from services.api_fastapi.domain.models.family import (
    Family,
    FamilyMember,
    FamilyRole,
    FamilyStatus,
    ROLE_RANK,
)
from services.api_fastapi.domain.models.player import Player
from services.api_fastapi.domain.services.config_service import (
    MAX_CAPO_COUNT,
    MAX_FAMILY_MEMBERS,
    ConfigService,
)
from services.api_fastapi.domain.services.ledger_service import (
    InsufficientFunds,
    _get_or_create_wallet,
    transfer,
)
from services.api_fastapi.domain.services.vault_service import InsufficientPermission


# ---------------------------------------------------------------------------
# Validation patterns
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[a-zA-Z0-9_ ]{3,24}$")
_TAG_RE = re.compile(r"^[A-Z0-9]{2,5}$")

# Ranks that are >= Capo (Rank 4+)
CAPO_AND_ABOVE = {"Capo", "Fixer", "Underboss", "Godfather"}


# ---------------------------------------------------------------------------
# Custom errors
# ---------------------------------------------------------------------------


class RankTooLow(Exception):
    """Player rank is below the required minimum (Capo)."""


class AlreadyInFamily(Exception):
    """Player is already a member of a family."""


class FamilyFull(Exception):
    """Family has reached its maximum member count."""


class DonMustTransferOrDisband(Exception):
    """Don cannot leave while other members remain."""


class FamilyHasMembers(Exception):
    """Cannot disband a family that still has other members."""


class FamilyNotFound(Exception):
    """The requested family does not exist."""


class NotInFamily(Exception):
    """Player is not a member of any family."""


class RoleLimitReached(Exception):
    """Maximum count for the target role has been reached."""


class InvalidName(Exception):
    """Family name does not match validation rules."""


class InvalidTag(Exception):
    """Family tag does not match validation rules."""


class NameTaken(Exception):
    """Family name is already in use by an active family."""


class TagTaken(Exception):
    """Family tag is already in use by an active family."""


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class FamilyResult:
    family_id: UUID
    name: str
    tag: str


@dataclass
class FamilyDetail:
    family_id: UUID
    name: str
    tag: str
    status: FamilyStatus
    created_at: datetime
    member_count: int
    vault_balance: Optional[int] = None


@dataclass
class MemberInfo:
    player_id: UUID
    display_name: str
    role: FamilyRole
    joined_at: datetime


@dataclass
class RoleChangeResult:
    player_id: UUID
    old_role: FamilyRole
    new_role: FamilyRole


@dataclass
class DisbandResult:
    family_id: UUID
    vault_transferred: int


# ---------------------------------------------------------------------------
# FamilyService
# ---------------------------------------------------------------------------


class FamilyService:
    """Family CRUD, membership, role hierarchy, dissolution, and queries."""

    def __init__(self, redis: aioredis.Redis, config: ConfigService) -> None:
        self._redis = redis
        self._config = config

    # -----------------------------------------------------------------------
    # Task 7.1 — Creation
    # -----------------------------------------------------------------------

    async def create_family(
        self,
        session: AsyncSession,
        player_id: UUID,
        name: str,
        tag: str,
        idempotency_key: str,
    ) -> FamilyResult:
        """
        Create a new Family. The founding player becomes the Don.

        Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 11.1
        """
        # 1. Rank gate
        player = await self._get_player(session, player_id)
        if player.rank not in CAPO_AND_ABOVE:
            raise RankTooLow("You must be Capo or higher to create a Family.")

        # 2. Already in a family?
        existing = await self._get_membership(session, player_id)
        if existing is not None:
            raise AlreadyInFamily("You are already in a family.")

        # 3. Validate name
        if not _NAME_RE.fullmatch(name):
            raise InvalidName(
                "Family name must be 3-24 characters: letters, digits, spaces, underscores."
            )

        # 4. Validate tag
        if not _TAG_RE.fullmatch(tag):
            raise InvalidTag(
                "Family tag must be 2-5 uppercase alphanumeric characters."
            )

        # 5. Uniqueness among ACTIVE families
        await self._check_name_unique(session, name)
        await self._check_tag_unique(session, tag)

        # 6. Create Family record
        family = Family(
            name=name,
            tag=tag,
            status=FamilyStatus.ACTIVE,
        )
        session.add(family)
        await session.flush()

        # 7. Add founding member as DON
        member = FamilyMember(
            family_id=family.id,
            player_id=player_id,
            role=FamilyRole.DON,
        )
        session.add(member)
        await session.flush()

        # 8. Create FAMILY Wallet (CASH, zero balance)
        await _get_or_create_wallet(
            session, OwnerType.FAMILY, family.id, Currency.CASH,
        )

        return FamilyResult(family_id=family.id, name=family.name, tag=family.tag)

    # -----------------------------------------------------------------------
    # Task 7.1 — Join
    # -----------------------------------------------------------------------

    async def join_family(
        self,
        session: AsyncSession,
        player_id: UUID,
        family_id: UUID,
        idempotency_key: str,
    ) -> FamilyResult:
        """
        Join an existing family as a Soldier.

        Requirements: 2.1, 2.2, 2.3, 2.4, 2.11, 11.1
        """
        # 1. Rank gate
        player = await self._get_player(session, player_id)
        if player.rank not in CAPO_AND_ABOVE:
            raise RankTooLow("You must be Capo or higher to join a Family.")

        # 2. Already in a family?
        existing = await self._get_membership(session, player_id)
        if existing is not None:
            raise AlreadyInFamily("You are already in a family.")

        # 3. Family exists?
        family = await self._get_active_family(session, family_id)

        # 4. Family full?
        max_members = await self._config.get_int(MAX_FAMILY_MEMBERS, default=25)
        count = await self._member_count(session, family_id)
        if count >= max_members:
            raise FamilyFull("This family has reached its maximum member count.")

        # 5. Add as Soldier
        member = FamilyMember(
            family_id=family_id,
            player_id=player_id,
            role=FamilyRole.SOLDIER,
        )
        session.add(member)
        await session.flush()

        return FamilyResult(family_id=family.id, name=family.name, tag=family.tag)

    # -----------------------------------------------------------------------
    # Task 7.1 — Leave
    # -----------------------------------------------------------------------

    async def leave_family(
        self,
        session: AsyncSession,
        player_id: UUID,
        idempotency_key: str,
    ) -> None:
        """
        Leave the current family.

        Requirements: 2.5, 2.6, 2.11
        """
        membership = await self._get_membership(session, player_id)
        if membership is None:
            raise NotInFamily("You are not in a family.")

        # Don cannot leave while other members remain
        if membership.role == FamilyRole.DON:
            count = await self._member_count(session, membership.family_id)
            if count > 1:
                raise DonMustTransferOrDisband(
                    "The Don must transfer leadership or disband before leaving."
                )

        # Remove from roster (hard delete)
        await session.execute(
            delete(FamilyMember).where(FamilyMember.id == membership.id)
        )
        await session.flush()

    # -----------------------------------------------------------------------
    # Task 7.1 — Kick
    # -----------------------------------------------------------------------

    async def kick_member(
        self,
        session: AsyncSession,
        actor_id: UUID,
        target_id: UUID,
        idempotency_key: str,
    ) -> None:
        """
        Kick a member from the family. Actor must have strictly higher role.

        Requirements: 2.7, 2.8, 2.9, 2.10, 2.11
        """
        actor_mem = await self._get_membership(session, actor_id)
        if actor_mem is None:
            raise NotInFamily("You are not in a family.")

        target_mem = await self._get_membership(session, target_id)
        if target_mem is None or target_mem.family_id != actor_mem.family_id:
            raise NotInFamily("Target is not in your family.")

        # Strictly higher role required
        if ROLE_RANK[actor_mem.role] <= ROLE_RANK[target_mem.role]:
            raise InsufficientPermission(
                "You cannot kick a member of equal or higher role."
            )

        # Remove target from roster
        await session.execute(
            delete(FamilyMember).where(FamilyMember.id == target_mem.id)
        )
        await session.flush()


    # -----------------------------------------------------------------------
    # Task 7.2 — Promote
    # -----------------------------------------------------------------------

    async def promote_member(
        self,
        session: AsyncSession,
        actor_id: UUID,
        target_id: UUID,
        new_role: FamilyRole,
        idempotency_key: str,
    ) -> RoleChangeResult:
        """
        Promote a family member. Only the Don can promote.

        Requirements: 3.1, 3.2, 3.5, 3.6, 3.8
        """
        actor_mem = await self._get_membership(session, actor_id)
        if actor_mem is None:
            raise NotInFamily("You are not in a family.")

        # Only Don can promote
        if actor_mem.role != FamilyRole.DON:
            raise InsufficientPermission("Only the Don can promote members.")

        target_mem = await self._get_membership(session, target_id)
        if target_mem is None or target_mem.family_id != actor_mem.family_id:
            raise NotInFamily("Target is not in your family.")

        old_role = target_mem.role

        # Soldier → Capo: check capo limit
        if new_role == FamilyRole.CAPO:
            max_capos = await self._config.get_int(MAX_CAPO_COUNT, default=3)
            capo_count = await self._role_count(
                session, actor_mem.family_id, FamilyRole.CAPO,
            )
            if capo_count >= max_capos:
                raise RoleLimitReached("Maximum number of Capos reached.")

        # Capo → Underboss: check no Underboss exists
        if new_role == FamilyRole.UNDERBOSS:
            ub_count = await self._role_count(
                session, actor_mem.family_id, FamilyRole.UNDERBOSS,
            )
            if ub_count > 0:
                raise RoleLimitReached("An Underboss already exists in this family.")

        target_mem.role = new_role
        await session.flush()

        return RoleChangeResult(
            player_id=target_id, old_role=old_role, new_role=new_role,
        )

    # -----------------------------------------------------------------------
    # Task 7.2 — Demote
    # -----------------------------------------------------------------------

    async def demote_member(
        self,
        session: AsyncSession,
        actor_id: UUID,
        target_id: UUID,
        new_role: FamilyRole,
        idempotency_key: str,
    ) -> RoleChangeResult:
        """
        Demote a family member.

        Requirements: 3.3, 3.4, 3.6, 3.8
        """
        actor_mem = await self._get_membership(session, actor_id)
        if actor_mem is None:
            raise NotInFamily("You are not in a family.")

        target_mem = await self._get_membership(session, target_id)
        if target_mem is None or target_mem.family_id != actor_mem.family_id:
            raise NotInFamily("Target is not in your family.")

        # actor.role must be strictly higher than target.role
        if ROLE_RANK[actor_mem.role] <= ROLE_RANK[target_mem.role]:
            raise InsufficientPermission(
                "You cannot demote a member of equal or higher role."
            )

        # Only Don can demote Underboss → Capo
        if target_mem.role == FamilyRole.UNDERBOSS and actor_mem.role != FamilyRole.DON:
            raise InsufficientPermission("Only the Don can demote the Underboss.")

        old_role = target_mem.role
        target_mem.role = new_role
        await session.flush()

        return RoleChangeResult(
            player_id=target_id, old_role=old_role, new_role=new_role,
        )

    # -----------------------------------------------------------------------
    # Task 7.2 — Transfer Don
    # -----------------------------------------------------------------------

    async def transfer_don(
        self,
        session: AsyncSession,
        actor_id: UUID,
        target_id: UUID,
        idempotency_key: str,
    ) -> RoleChangeResult:
        """
        Transfer the Don role to another member.

        Requirements: 3.7, 3.8
        """
        actor_mem = await self._get_membership(session, actor_id)
        if actor_mem is None:
            raise NotInFamily("You are not in a family.")

        if actor_mem.role != FamilyRole.DON:
            raise InsufficientPermission("Only the current Don can transfer leadership.")

        target_mem = await self._get_membership(session, target_id)
        if target_mem is None or target_mem.family_id != actor_mem.family_id:
            raise NotInFamily("Target is not in your family.")

        # Assign DON to target
        old_target_role = target_mem.role
        target_mem.role = FamilyRole.DON

        # Demote former Don: Underboss if slot is free, else Capo
        ub_count = await self._role_count(
            session, actor_mem.family_id, FamilyRole.UNDERBOSS,
        )
        # The target was potentially the Underboss; after becoming Don the slot
        # may now be free. Re-check excluding the target.
        ub_count_excluding_target = await self._role_count_excluding(
            session, actor_mem.family_id, FamilyRole.UNDERBOSS, target_id,
        )
        if ub_count_excluding_target == 0:
            actor_mem.role = FamilyRole.UNDERBOSS
        else:
            actor_mem.role = FamilyRole.CAPO

        await session.flush()

        return RoleChangeResult(
            player_id=target_id, old_role=old_target_role, new_role=FamilyRole.DON,
        )


    # -----------------------------------------------------------------------
    # Task 7.3 — Disband
    # -----------------------------------------------------------------------

    async def disband_family(
        self,
        session: AsyncSession,
        player_id: UUID,
        idempotency_key: str,
    ) -> DisbandResult:
        """
        Disband the family. Only the Don can disband, and only when no other
        members remain.

        Requirements: 4.1, 4.2, 4.3, 4.4
        """
        membership = await self._get_membership(session, player_id)
        if membership is None:
            raise NotInFamily("You are not in a family.")

        if membership.role != FamilyRole.DON:
            raise InsufficientPermission("Only the Don can disband the family.")

        family_id = membership.family_id
        count = await self._member_count(session, family_id)
        if count > 1:
            raise FamilyHasMembers(
                "Cannot disband while other members remain. Kick or wait for them to leave."
            )

        # Transfer vault balance to Don (if any)
        vault_transferred = 0
        from services.api_fastapi.domain.services.vault_service import (
            FamilyVaultService,
        )
        vault_svc = FamilyVaultService(self._config)
        balance = await vault_svc.get_vault_balance(session, family_id)
        if balance > 0:
            await transfer(
                session,
                from_owner_type=OwnerType.FAMILY,
                from_owner_id=family_id,
                to_owner_type=OwnerType.PLAYER,
                to_owner_id=player_id,
                currency=Currency.CASH,
                amount=balance,
                reference_id=str(family_id),
                metadata={"source": "family_disband"},
                idempotency_key=f"{idempotency_key}:disband_transfer",
            )
            vault_transferred = balance

        # Mark family as DISBANDED
        family = await session.get(Family, family_id)
        family.status = FamilyStatus.DISBANDED
        family.disbanded_at = datetime.now(timezone.utc)

        # Remove Don from roster
        await session.execute(
            delete(FamilyMember).where(FamilyMember.id == membership.id)
        )
        await session.flush()

        return DisbandResult(family_id=family_id, vault_transferred=vault_transferred)

    # -----------------------------------------------------------------------
    # Task 7.3 — Queries
    # -----------------------------------------------------------------------

    async def get_family(
        self,
        session: AsyncSession,
        family_id: UUID,
    ) -> Optional[FamilyDetail]:
        """Return family details or None if not found."""
        family = await session.get(Family, family_id)
        if family is None:
            return None

        count = await self._member_count(session, family_id)
        return FamilyDetail(
            family_id=family.id,
            name=family.name,
            tag=family.tag,
            status=family.status,
            created_at=family.created_at,
            member_count=count,
        )

    async def get_player_family(
        self,
        session: AsyncSession,
        player_id: UUID,
    ) -> Optional[FamilyDetail]:
        """Return the family the player belongs to, or None."""
        membership = await self._get_membership(session, player_id)
        if membership is None:
            return None
        return await self.get_family(session, membership.family_id)

    async def list_members(
        self,
        session: AsyncSession,
        family_id: UUID,
    ) -> List[MemberInfo]:
        """Return all members of a family with display names."""
        stmt = (
            select(FamilyMember, Player.display_name)
            .join(Player, FamilyMember.player_id == Player.id)
            .where(FamilyMember.family_id == family_id)
            .order_by(FamilyMember.joined_at)
        )
        result = await session.execute(stmt)
        rows = result.all()
        return [
            MemberInfo(
                player_id=member.player_id,
                display_name=display_name or "",
                role=member.role,
                joined_at=member.joined_at,
            )
            for member, display_name in rows
        ]

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    @staticmethod
    async def _get_player(session: AsyncSession, player_id: UUID) -> Player:
        player = await session.get(Player, player_id)
        if player is None:
            raise FamilyNotFound(f"Player {player_id} not found.")
        return player

    @staticmethod
    async def _get_membership(
        session: AsyncSession, player_id: UUID,
    ) -> Optional[FamilyMember]:
        stmt = select(FamilyMember).where(FamilyMember.player_id == player_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def _get_active_family(session: AsyncSession, family_id: UUID) -> Family:
        stmt = select(Family).where(
            Family.id == family_id,
            Family.status == FamilyStatus.ACTIVE,
        )
        result = await session.execute(stmt)
        family = result.scalar_one_or_none()
        if family is None:
            raise FamilyNotFound(f"Family {family_id} not found or not active.")
        return family

    @staticmethod
    async def _member_count(session: AsyncSession, family_id: UUID) -> int:
        stmt = select(func.count()).select_from(FamilyMember).where(
            FamilyMember.family_id == family_id,
        )
        result = await session.execute(stmt)
        return result.scalar_one()

    @staticmethod
    async def _role_count(
        session: AsyncSession, family_id: UUID, role: FamilyRole,
    ) -> int:
        stmt = select(func.count()).select_from(FamilyMember).where(
            FamilyMember.family_id == family_id,
            FamilyMember.role == role,
        )
        result = await session.execute(stmt)
        return result.scalar_one()

    @staticmethod
    async def _role_count_excluding(
        session: AsyncSession,
        family_id: UUID,
        role: FamilyRole,
        exclude_player_id: UUID,
    ) -> int:
        stmt = select(func.count()).select_from(FamilyMember).where(
            FamilyMember.family_id == family_id,
            FamilyMember.role == role,
            FamilyMember.player_id != exclude_player_id,
        )
        result = await session.execute(stmt)
        return result.scalar_one()

    @staticmethod
    async def _check_name_unique(session: AsyncSession, name: str) -> None:
        stmt = select(func.count()).select_from(Family).where(
            Family.name == name,
            Family.status == FamilyStatus.ACTIVE,
        )
        result = await session.execute(stmt)
        if result.scalar_one() > 0:
            raise NameTaken(f"Family name '{name}' is already taken.")

    @staticmethod
    async def _check_tag_unique(session: AsyncSession, tag: str) -> None:
        stmt = select(func.count()).select_from(Family).where(
            Family.tag == tag,
            Family.status == FamilyStatus.ACTIVE,
        )
        result = await session.execute(stmt)
        if result.scalar_one() > 0:
            raise TagTaken(f"Family tag '{tag}' is already taken.")
