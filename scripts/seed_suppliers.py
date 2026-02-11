#!/usr/bin/env python3
"""Добавить демо-поставщиков для тестирования."""
import asyncio

from loguru import logger

from bot.config import DATABASE_URL
from bot.models.base import init_db
from bot.models.company import Company
from bot.models.supplier import Supplier
from bot.services.database import get_or_create_default_company


async def seed() -> None:
    """Добавить демо-данные."""
    logger.info("seed_suppliers started")
    await init_db()
    company = await get_or_create_default_company()

    from sqlalchemy import select
    from bot.models.base import async_session_factory

    async with async_session_factory() as session:
        result = await session.execute(
            select(Supplier).where(Supplier.company_id == company.id)
        )
        existing = result.scalars().all()
        if existing:
            logger.info(f"Поставщики уже есть: {len(existing)} шт")
            return

        suppliers = [
            Supplier(company_id=company.id, name="ООО Рога и Копыта"),
            Supplier(company_id=company.id, name="ИП Иванов"),
            Supplier(company_id=company.id, name="АО Продукты России"),
        ]
        for s in suppliers:
            session.add(s)
        await session.commit()
        logger.info(f"Добавлено поставщиков: {len(suppliers)}")


if __name__ == "__main__":
    asyncio.run(seed())
