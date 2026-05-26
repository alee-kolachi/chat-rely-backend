from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.engine import get_engine

session_factory: async_sessionmaker[AsyncSession] | None = None


def init_session_factory() -> async_sessionmaker[AsyncSession]:
    global session_factory
    session_factory = async_sessionmaker(bind=get_engine(), class_=AsyncSession, expire_on_commit=False)
    return session_factory


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if session_factory is None:
        raise RuntimeError("Session factory is not initialized")
    return session_factory


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session


async def check_db_ready() -> bool:
    import structlog
    import time

    from app.core.settings import get_settings

    log = structlog.get_logger("db")
    t0 = time.perf_counter()
    async with get_session_factory()() as session:
        result = await session.execute(text("select 1"))
        ok = result.scalar_one() == 1
    latency_ms = (time.perf_counter() - t0) * 1000.0
    settings = get_settings()
    url = str(settings.database_url).lower()
    pooler = ":6543" in url or "pooler" in url
    log.info("db.ready", latency_ms=round(latency_ms, 2), pooler=pooler)
    if not pooler:
        log.warning(
            "db.pooler_recommended",
            hint="Use Supabase transaction pooler (port 6543) in DATABASE_URL for lower API latency",
        )
    if latency_ms > 500.0:
        log.warning(
            "db.high_latency",
            latency_ms=round(latency_ms, 2),
            hint="Deploy the API in the same region as your Supabase project",
        )
    return ok

