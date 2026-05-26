from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.settings import Settings

engine: AsyncEngine | None = None


def init_engine(settings: Settings) -> AsyncEngine:
    global engine
    # Supabase Supavisor (transaction pool / PgBouncer) does not support server-side prepared
    # statement caching — disable asyncpg statement cache to avoid intermittent failures.
    engine = create_async_engine(
        str(settings.database_url),
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=int(settings.database_pool_recycle_seconds),
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
        },
    )
    return engine


def get_engine() -> AsyncEngine:
    if engine is None:
        raise RuntimeError("Database engine is not initialized")
    return engine

