"""
tests/conftest.py

Shared test fixtures for AI MAFIA test suite.

Provides:
  - db_engine / db_session  — async SQLite engine + auto-rollback session
  - redis_client            — fakeredis async client
  - config_service          — ConfigService backed by fakeredis
  - player_factory          — creates Player records with configurable defaults
  - wallet_factory          — creates Wallet records with configurable defaults

Requirements: all
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

import fakeredis.aioredis as fakeredis_aio

from services.api_fastapi.domain.models.economy import Base, Currency, OwnerType, Wallet
from services.api_fastapi.domain.models.player import Player
from services.api_fastapi.domain.services.config_service import ConfigService


# ---------------------------------------------------------------------------
# Database fixtures (async SQLite for unit tests)
# ---------------------------------------------------------------------------

def _register_sqlite_functions(dbapi_conn, connection_record):
    """Register PostgreSQL-compatible functions so CHECK constraints work in SQLite."""
    dbapi_conn.create_function("char_length", 1, lambda s: len(s) if s else 0)


@pytest_asyncio.fixture()
async def db_engine():
    """Create an in-memory async SQLite engine and provision all tables."""
    from sqlalchemy import event

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    event.listen(engine.sync_engine, "connect", _register_sqlite_functions)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture()
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Async session that rolls back after each test."""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        async with session.begin():
            yield session
            await session.rollback()


# ---------------------------------------------------------------------------
# Redis fixtures (fakeredis)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def redis_client():
    """Fakeredis async client — isolated per test."""
    client = fakeredis_aio.FakeRedis()
    yield client
    await client.flushall()
    await client.aclose()


# ---------------------------------------------------------------------------
# Config Service fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def config_service(redis_client) -> ConfigService:
    """ConfigService backed by the fakeredis client."""
    return ConfigService(redis=redis_client)


# ---------------------------------------------------------------------------
# Factory fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def player_factory(db_session: AsyncSession):
    """
    Factory that creates Player records with sensible defaults.

    Usage:
        player = await player_factory()
        player = await player_factory(display_name="Tony", rank="Runner", xp=1500)
    """

    async def _create(
        *,
        player_id: uuid.UUID | None = None,
        apple_sub: str | None = None,
        email: str | None = None,
        display_name: str | None = None,
        rank: str = "Empty-Suit",
        xp: int = 0,
        age_confirmed: bool = True,
        is_active: bool = True,
    ) -> Player:
        player = Player(
            id=player_id or uuid.uuid4(),
            apple_sub=apple_sub,
            email=email,
            display_name=display_name,
            rank=rank,
            xp=xp,
            age_confirmed=age_confirmed,
            is_active=is_active,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db_session.add(player)
        await db_session.flush()
        return player

    return _create


@pytest.fixture()
def wallet_factory(db_session: AsyncSession):
    """
    Factory that creates Wallet records with sensible defaults.

    Usage:
        wallet = await wallet_factory(owner_id=player.id)
        wallet = await wallet_factory(owner_id=player.id, balance=500)
    """

    async def _create(
        *,
        owner_id: uuid.UUID,
        owner_type: OwnerType = OwnerType.PLAYER,
        currency: Currency = Currency.CASH,
        balance: int = 0,
        reserved_balance: int = 0,
    ) -> Wallet:
        wallet = Wallet(
            id=uuid.uuid4(),
            owner_type=owner_type,
            owner_id=owner_id,
            currency=currency,
            balance=balance,
            reserved_balance=reserved_balance,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db_session.add(wallet)
        await db_session.flush()
        return wallet

    return _create
