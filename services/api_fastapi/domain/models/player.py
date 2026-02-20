from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from services.api_fastapi.domain.models.economy import Base


class Player(Base):
    __tablename__ = "players"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    apple_sub: Mapped[Optional[str]] = mapped_column(
        String(256), unique=True, nullable=True
    )
    email: Mapped[Optional[str]] = mapped_column(
        String(320), unique=True, nullable=True
    )
    display_name: Mapped[Optional[str]] = mapped_column(
        String(20), unique=True, nullable=True
    )
    rank: Mapped[str] = mapped_column(
        String(32), nullable=False, default="Empty-Suit"
    )
    xp: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    age_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    __table_args__ = (
        CheckConstraint("xp >= 0", name="ck_player_xp_nonneg"),
        CheckConstraint(
            "char_length(display_name) >= 3 AND char_length(display_name) <= 20",
            name="ck_player_name_length",
        ),
    )
