"""Работа с БД."""
from typing import Optional

from loguru import logger

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Company, Supplier, User
from bot.models.base import async_session_factory


async def get_or_create_default_company() -> Company:
    """Получить или создать компанию по умолчанию (для демо)."""
    logger.debug("get_or_create_default_company called")
    async with async_session_factory() as session:
        result = await session.execute(select(Company).limit(1))
        company = result.scalar_one_or_none()
        if company:
            logger.debug(f"Найдена компания: id={company.id}, name={company.name}")
            return company
        company = Company(name="Демо-компания")
        session.add(company)
        await session.commit()
        await session.refresh(company)
        logger.info(f"Создана компания по умолчанию: id={company.id}")
        return company


async def get_suppliers_for_company(company_id: int) -> list[tuple[int, str]]:
    """Получить список поставщиков компании (id, name)."""
    logger.debug(f"get_suppliers_for_company called with: company_id={company_id}")
    async with async_session_factory() as session:
        result = await session.execute(
            select(Supplier.id, Supplier.name).where(Supplier.company_id == company_id).order_by(Supplier.name)
        )
        suppliers = result.all()
        logger.debug(f"Найдено поставщиков: {len(suppliers)}")
        return [(row[0], row[1]) for row in suppliers]


async def get_supplier_by_id(supplier_id: int) -> Optional[Supplier]:
    """Получить поставщика по ID."""
    logger.debug(f"get_supplier_by_id called with: supplier_id={supplier_id}")
    async with async_session_factory() as session:
        result = await session.execute(select(Supplier).where(Supplier.id == supplier_id))
        return result.scalar_one_or_none()


async def add_supplier(company_id: int, name: str) -> Supplier:
    """Добавить поставщика."""
    logger.info(f"add_supplier called with: company_id={company_id}, name={name}")
    async with async_session_factory() as session:
        supplier = Supplier(company_id=company_id, name=name)
        session.add(supplier)
        await session.commit()
        await session.refresh(supplier)
        logger.debug(f"Поставщик создан: id={supplier.id}")
        return supplier
