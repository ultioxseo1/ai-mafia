"""
services/api_fastapi/domain/services/chat_service.py

AI MAFIA — Chat Service

Handles real-time SSE message delivery, message persistence, and history
backfill for Family chat.  Messages are persisted to PostgreSQL and
published to Redis PubSub for fan-out across server instances.

Requirements: 7.1, 7.2, 7.3, 7.4, 7.6, 7.7
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import AsyncGenerator, List, Optional
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api_fastapi.domain.models.chat import ChatMessage
from services.api_fastapi.domain.services.config_service import (
    CHAT_HEARTBEAT_INTERVAL,
    CHAT_HISTORY_LIMIT,
    ConfigService,
)


# ---------------------------------------------------------------------------
# Custom errors
# ---------------------------------------------------------------------------


class InvalidMessageLength(Exception):
    """Message body is empty or exceeds 500 characters."""


# ---------------------------------------------------------------------------
# ChatService
# ---------------------------------------------------------------------------


class ChatService:
    """Family chat: send, persist, history backfill, and SSE subscription."""

    def __init__(self, redis: aioredis.Redis, config: ConfigService) -> None:
        self._redis = redis
        self._config = config

    # -- Send ---------------------------------------------------------------

    async def send_message(
        self,
        session: AsyncSession,
        player_id: UUID,
        family_id: UUID,
        display_name: str,
        body: str,
    ) -> ChatMessage:
        """
        Validate, persist, and publish a chat message.

        1. Validate body length (1–500 chars)
        2. Persist ChatMessage to DB
        3. Publish JSON to Redis PubSub channel ``family_chat:{family_id}``
        4. Return the ChatMessage record

        Raises:
            InvalidMessageLength: body is empty or exceeds 500 characters.

        Requirements: 7.2, 7.4
        """
        if not body or len(body) > 500:
            raise InvalidMessageLength(
                "Message must be between 1 and 500 characters."
            )

        now = datetime.utcnow()
        chat_msg = ChatMessage(
            player_id=player_id,
            family_id=family_id,
            display_name=display_name,
            body=body,
            created_at=now,
        )
        session.add(chat_msg)
        await session.flush()

        # Publish to Redis PubSub for real-time delivery
        channel = f"family_chat:{family_id}"
        payload = {
            "type": "message",
            "id": str(chat_msg.id),
            "player_id": str(player_id),
            "family_id": str(family_id),
            "display_name": display_name,
            "body": body,
            "created_at": chat_msg.created_at.isoformat(),
        }
        await self._redis.publish(channel, json.dumps(payload))

        return chat_msg

    # -- History ------------------------------------------------------------

    async def get_history(
        self,
        session: AsyncSession,
        family_id: UUID,
        limit: Optional[int] = None,
    ) -> List[ChatMessage]:
        """
        Return the most recent N messages for a family, ordered by
        created_at descending (most recent first).

        Default limit comes from CHAT_HISTORY_LIMIT config (default 50).

        Requirements: 7.6
        """
        if limit is None:
            limit = await self._config.get_int(CHAT_HISTORY_LIMIT, default=50)

        stmt = (
            select(ChatMessage)
            .where(ChatMessage.family_id == family_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    # -- Subscribe (SSE) ----------------------------------------------------

    async def subscribe(self, family_id: UUID) -> AsyncGenerator[dict, None]:
        """
        Subscribe to a family's Redis PubSub channel and yield message
        dicts.  Includes periodic heartbeat events to keep the SSE
        connection alive and detect stale clients.

        Requirements: 7.1, 7.3, 7.7
        """
        channel = f"family_chat:{family_id}"
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            heartbeat_interval = await self._config.get_int(
                CHAT_HEARTBEAT_INTERVAL, default=30,
            )
            while True:
                try:
                    msg = await asyncio.wait_for(
                        pubsub.get_message(
                            ignore_subscribe_messages=True, timeout=1.0,
                        ),
                        timeout=heartbeat_interval,
                    )
                    if msg and msg["type"] == "message":
                        yield json.loads(msg["data"])
                except asyncio.TimeoutError:
                    yield {
                        "type": "heartbeat",
                        "timestamp": datetime.utcnow().isoformat(),
                    }
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
