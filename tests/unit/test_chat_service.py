"""
Unit tests for ChatService — send_message, get_history.

Validates: Requirements 7.2, 7.4, 7.6
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from services.api_fastapi.domain.services.chat_service import (
    ChatService,
    InvalidMessageLength,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CHAT_MODULE = "services.api_fastapi.domain.services.chat_service"

PLAYER_ID = uuid4()
FAMILY_ID = uuid4()
DISPLAY_NAME = "TestPlayer"


def _make_config(history_limit: int = 50, heartbeat: int = 30) -> MagicMock:
    config = MagicMock()

    async def _get_int(key, default=0):
        from services.api_fastapi.domain.services.config_service import (
            CHAT_HEARTBEAT_INTERVAL,
            CHAT_HISTORY_LIMIT,
        )
        if key == CHAT_HISTORY_LIMIT:
            return history_limit
        if key == CHAT_HEARTBEAT_INTERVAL:
            return heartbeat
        return default

    config.get_int = AsyncMock(side_effect=_get_int)
    return config


def _make_redis() -> MagicMock:
    r = MagicMock()
    r.publish = AsyncMock(return_value=1)
    return r


def _make_session() -> MagicMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# send_message tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_valid_body():
    """A valid message (1-500 chars) is persisted and published."""
    redis = _make_redis()
    config = _make_config()
    service = ChatService(redis=redis, config=config)
    session = _make_session()

    body = "Hello family!"
    msg = await service.send_message(session, PLAYER_ID, FAMILY_ID, DISPLAY_NAME, body)

    # ChatMessage was added to session
    session.add.assert_called_once()
    session.flush.assert_awaited_once()

    # Verify the returned ChatMessage fields
    assert msg.player_id == PLAYER_ID
    assert msg.family_id == FAMILY_ID
    assert msg.display_name == DISPLAY_NAME
    assert msg.body == body

    # Verify Redis publish
    redis.publish.assert_awaited_once()
    call_args = redis.publish.call_args
    channel = call_args[0][0]
    payload = json.loads(call_args[0][1])
    assert channel == f"family_chat:{FAMILY_ID}"
    assert payload["type"] == "message"
    assert payload["player_id"] == str(PLAYER_ID)
    assert payload["family_id"] == str(FAMILY_ID)
    assert payload["display_name"] == DISPLAY_NAME
    assert payload["body"] == body


@pytest.mark.asyncio
async def test_send_message_empty_body_raises():
    """An empty message body is rejected."""
    redis = _make_redis()
    config = _make_config()
    service = ChatService(redis=redis, config=config)
    session = _make_session()

    with pytest.raises(InvalidMessageLength):
        await service.send_message(session, PLAYER_ID, FAMILY_ID, DISPLAY_NAME, "")


@pytest.mark.asyncio
async def test_send_message_too_long_raises():
    """A message exceeding 500 chars is rejected."""
    redis = _make_redis()
    config = _make_config()
    service = ChatService(redis=redis, config=config)
    session = _make_session()

    body = "x" * 501
    with pytest.raises(InvalidMessageLength):
        await service.send_message(session, PLAYER_ID, FAMILY_ID, DISPLAY_NAME, body)


@pytest.mark.asyncio
async def test_send_message_exactly_1_char():
    """Boundary: a single character message is valid."""
    redis = _make_redis()
    config = _make_config()
    service = ChatService(redis=redis, config=config)
    session = _make_session()

    msg = await service.send_message(session, PLAYER_ID, FAMILY_ID, DISPLAY_NAME, "A")
    assert msg.body == "A"
    session.add.assert_called_once()


@pytest.mark.asyncio
async def test_send_message_exactly_500_chars():
    """Boundary: a 500-character message is valid."""
    redis = _make_redis()
    config = _make_config()
    service = ChatService(redis=redis, config=config)
    session = _make_session()

    body = "x" * 500
    msg = await service.send_message(session, PLAYER_ID, FAMILY_ID, DISPLAY_NAME, body)
    assert msg.body == body
    assert len(msg.body) == 500


# ---------------------------------------------------------------------------
# get_history tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_history_uses_config_default():
    """When no limit is passed, the config default is used."""
    redis = _make_redis()
    config = _make_config(history_limit=25)
    service = ChatService(redis=redis, config=config)
    session = _make_session()

    # Mock the query result
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    session.execute.return_value = mock_result

    result = await service.get_history(session, FAMILY_ID)
    assert result == []
    # Verify execute was called (the query was built with the config limit)
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_history_with_explicit_limit():
    """An explicit limit overrides the config default."""
    redis = _make_redis()
    config = _make_config(history_limit=50)
    service = ChatService(redis=redis, config=config)
    session = _make_session()

    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    session.execute.return_value = mock_result

    result = await service.get_history(session, FAMILY_ID, limit=10)
    assert result == []
    # config.get_int should NOT have been called since limit was explicit
    config.get_int.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_history_returns_messages():
    """History returns the messages from the DB query."""
    redis = _make_redis()
    config = _make_config()
    service = ChatService(redis=redis, config=config)
    session = _make_session()

    msg1 = MagicMock()
    msg2 = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [msg1, msg2]
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    session.execute.return_value = mock_result

    result = await service.get_history(session, FAMILY_ID)
    assert result == [msg1, msg2]
