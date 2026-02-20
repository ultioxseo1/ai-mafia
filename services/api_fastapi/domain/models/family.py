"""
services/api_fastapi/domain/models/family.py

AI MAFIA — Family, FamilyMember, FamilyProperty Models + PropertyDefinition

SQLAlchemy 2.0 models for the syndicate system: families, membership roster,
and property ownership.  PropertyDefinition is a frozen dataclass loaded from
ConfigService (same pattern as CrimeDefinition).

Requirements: 1.1, 1.2, 1.3, 2.1, 3.1, 8.1, 9.1
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from services.api_fastapi.domain.models.economy import Base
from services.api_fastapi.domain.services.config_service import (
    PROPERTY_DEFINITIONS,
    ConfigService,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class FamilyStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DISBANDED = "DISBANDED"


class FamilyRole(str, enum.Enum):
    SOLDIER = "SOLDIER"
    CAPO = "CAPO"
    UNDERBOSS = "UNDERBOSS"
    DON = "DON"


# Numeric mapping for permission comparisons (higher = more authority)
ROLE_RANK: Dict[FamilyRole, int] = {
    FamilyRole.SOLDIER: 1,
    FamilyRole.CAPO: 2,
    FamilyRole.UNDERBOSS: 3,
    FamilyRole.DON: 4,
}


# ---------------------------------------------------------------------------
# Family
# ---------------------------------------------------------------------------

class Family(Base):
    __tablename__ = "families"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(24), nullable=False)
    tag: Mapped[str] = mapped_column(String(5), nullable=False)
    status: Mapped[FamilyStatus] = mapped_column(
        Enum(FamilyStatus, name="family_status"), nullable=False, default=FamilyStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow,
    )
    disbanded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_family_name"),
        UniqueConstraint("tag", name="uq_family_tag"),
        CheckConstraint(
            "char_length(name) >= 3 AND char_length(name) <= 24",
            name="ck_family_name_length",
        ),
        CheckConstraint(
            "char_length(tag) >= 2 AND char_length(tag) <= 5",
            name="ck_family_tag_length",
        ),
    )


# ---------------------------------------------------------------------------
# FamilyMember
# ---------------------------------------------------------------------------

class FamilyMember(Base):
    __tablename__ = "family_members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("families.id"), nullable=False,
    )
    player_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("players.id"), nullable=False,
    )
    role: Mapped[FamilyRole] = mapped_column(
        Enum(FamilyRole, name="family_role"), nullable=False,
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow,
    )

    __table_args__ = (
        UniqueConstraint("player_id", name="uq_family_member_player"),
        Index("ix_family_member_family", "family_id"),
    )


# ---------------------------------------------------------------------------
# FamilyProperty
# ---------------------------------------------------------------------------

class FamilyProperty(Base):
    __tablename__ = "family_properties"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("families.id"), nullable=False,
    )
    property_id: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    purchased_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow,
    )

    __table_args__ = (
        UniqueConstraint("family_id", "property_id", name="uq_family_property"),
        CheckConstraint("level >= 1", name="ck_property_level_min"),
    )


# ---------------------------------------------------------------------------
# PropertyDefinition (config-driven, not a DB model)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PropertyDefinition:
    """An immutable property definition loaded from configuration."""

    property_id: str        # e.g. "speakeasy", "casino", "docks"
    name: str
    purchase_price: int     # integer CASH
    daily_income: int       # base daily income at level 1
    max_level: int


async def load_property_definitions(config: ConfigService) -> List[PropertyDefinition]:
    """
    Read property definitions from ConfigService and return a list of
    ``PropertyDefinition`` instances.

    The config value under ``PROPERTY_DEFINITIONS`` is a JSON array of objects,
    each containing the fields required by ``PropertyDefinition``.
    """
    raw: list[dict] = await config.get_json(PROPERTY_DEFINITIONS, default=[])
    return [PropertyDefinition(**entry) for entry in raw]
