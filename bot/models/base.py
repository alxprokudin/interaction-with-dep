"""Базовая конфигурация БД."""
from loguru import logger

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from bot.config import DATABASE_URL


class Base(DeclarativeBase):
    """Базовый класс для моделей."""

    pass


engine = create_async_engine(
    DATABASE_URL,
    echo=False,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_async_session() -> AsyncSession:
    """Получить сессию БД."""
    async with async_session_factory() as session:
        yield session


async def init_db() -> None:
    """Инициализация БД (создание таблиц)."""
    import bot.models  # noqa: F401 — регистрируем все модели

    logger.info("Инициализация базы данных", database_url=DATABASE_URL[:50] + "...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.debug("Таблицы созданы успешно")
