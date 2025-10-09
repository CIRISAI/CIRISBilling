"""
Database Session Management - Async SQLAlchemy session factory.

Provides separate read and write database connections.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings


# Global engine instances
_write_engine: AsyncEngine | None = None
_read_engine: AsyncEngine | None = None

# Session factories
_write_session_factory: async_sessionmaker[AsyncSession] | None = None
_read_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_write_engine() -> AsyncEngine:
    """Get or create the write database engine (primary)."""
    global _write_engine
    if _write_engine is None:
        _write_engine = create_async_engine(
            settings.database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout,
            pool_recycle=settings.database_pool_recycle,
            echo=settings.log_level == "DEBUG",
        )
    return _write_engine


def get_read_engine() -> AsyncEngine:
    """Get or create the read database engine (replica)."""
    global _read_engine
    if _read_engine is None:
        _read_engine = create_async_engine(
            settings.read_database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout,
            pool_recycle=settings.database_pool_recycle,
            echo=settings.log_level == "DEBUG",
        )
    return _read_engine


def get_write_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create the write session factory."""
    global _write_session_factory
    if _write_session_factory is None:
        engine = get_write_engine()
        _write_session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _write_session_factory


def get_read_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create the read session factory."""
    global _read_session_factory
    if _read_session_factory is None:
        engine = get_read_engine()
        _read_session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _read_session_factory


@asynccontextmanager
async def get_write_session() -> AsyncIterator[AsyncSession]:
    """
    Get an async database session for write operations.

    Usage:
        async with get_write_session() as session:
            await session.execute(...)
            await session.commit()
    """
    factory = get_write_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


@asynccontextmanager
async def get_read_session() -> AsyncIterator[AsyncSession]:
    """
    Get an async database session for read operations (from replica).

    Usage:
        async with get_read_session() as session:
            result = await session.execute(...)
    """
    factory = get_read_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_write_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency for write database session.

    Usage:
        @app.post("/endpoint")
        async def endpoint(db: AsyncSession = Depends(get_write_db)):
            ...
    """
    factory = get_write_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_read_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency for read database session.

    Usage:
        @app.get("/endpoint")
        async def endpoint(db: AsyncSession = Depends(get_read_db)):
            ...
    """
    factory = get_read_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def close_engines() -> None:
    """Close all database engines (for graceful shutdown)."""
    global _write_engine, _read_engine

    if _write_engine:
        await _write_engine.dispose()
        _write_engine = None

    if _read_engine:
        await _read_engine.dispose()
        _read_engine = None
