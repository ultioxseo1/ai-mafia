"""
services/api_fastapi/api/routers/chat_router.py

Family chat endpoints: SSE subscription, send message, and history backfill.
Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7
"""

from __future__ import annotations

import json
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from services.api_fastapi.api.deps import (
    get_chat_service,
    get_session,
    require_age_confirmed,
    require_family_membership,
)
from services.api_fastapi.domain.models.family import FamilyMember
from services.api_fastapi.domain.models.player import Player
from services.api_fastapi.domain.services.chat_service import ChatService


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class SendMessageRequest(BaseModel):
    body: str


class ChatMessageResponse(BaseModel):
    id: UUID
    player_id: UUID
    display_name: str
    body: str
    created_at: str


# ---------------------------------------------------------------------------
# Router — JWT + age gate + family membership on all endpoints
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/families/me/chat", tags=["chat"])


@router.get("")
async def subscribe_chat(
    _player_id: UUID = Depends(require_age_confirmed),
    member: FamilyMember = Depends(require_family_membership),
    chat_svc: ChatService = Depends(get_chat_service),
) -> StreamingResponse:
    """SSE endpoint — streams real-time chat messages for the player's family."""

    async def event_stream():
        async for event in chat_svc.subscribe(member.family_id):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("", response_model=ChatMessageResponse, status_code=201)
async def send_message(
    body: SendMessageRequest,
    _player_id: UUID = Depends(require_age_confirmed),
    member: FamilyMember = Depends(require_family_membership),
    session: AsyncSession = Depends(get_session),
    chat_svc: ChatService = Depends(get_chat_service),
) -> ChatMessageResponse:
    """Send a chat message to the player's family channel."""
    # Query the player's display_name from the DB to prevent spoofing
    stmt = select(Player.display_name).where(Player.id == member.player_id)
    result = await session.execute(stmt)
    display_name = result.scalar_one()

    msg = await chat_svc.send_message(
        session,
        player_id=member.player_id,
        family_id=member.family_id,
        display_name=display_name,
        body=body.body,
    )
    return ChatMessageResponse(
        id=msg.id,
        player_id=msg.player_id,
        display_name=msg.display_name,
        body=msg.body,
        created_at=msg.created_at.isoformat(),
    )


@router.get("/history", response_model=List[ChatMessageResponse])
async def get_history(
    _player_id: UUID = Depends(require_age_confirmed),
    member: FamilyMember = Depends(require_family_membership),
    session: AsyncSession = Depends(get_session),
    chat_svc: ChatService = Depends(get_chat_service),
    limit: Optional[int] = Query(default=None, ge=1, le=500),
) -> List[ChatMessageResponse]:
    """Return the most recent chat messages for the player's family."""
    messages = await chat_svc.get_history(session, member.family_id, limit=limit)
    return [
        ChatMessageResponse(
            id=m.id,
            player_id=m.player_id,
            display_name=m.display_name,
            body=m.body,
            created_at=m.created_at.isoformat(),
        )
        for m in messages
    ]
