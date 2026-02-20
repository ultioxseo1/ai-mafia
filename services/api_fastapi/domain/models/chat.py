"""
services/api_fastapi/domain/models/chat.py

AI MAFIA — ChatMessage Model

SQLAlchemy 2.0 model for family chat messages.  Messages are persisted to
PostgreSQL for history backfill on reconnect; real-time delivery uses Redis
PubSub (handled by ChatService, not the model layer).

Requirements: 7.2
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from services.api_fastapi.domain.models.economy import Base


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("families.id"), nullable=False,
    )
    player_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("players.id"), nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(20), nullable=False)
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow,
    )

    __table_args__ = (
        Index("ix_chat_family_time", "family_id", "created_at"),
        CheckConstraint(
            "char_length(body) >= 1 AND char_length(body) <= 500",
            name="ck_chat_body_length",
        ),
    )
