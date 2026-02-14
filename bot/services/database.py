"""Работа с БД."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.models import Company, Supplier, User
from bot.models.base import async_session_factory
from bot.models.integrations import CompanyIntegrations


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


@dataclass
class UserCompanyInfo:
    """Информация о пользователе и его компании."""
    user_id: int
    company_id: int
    company_name: str
    sheet_id: Optional[str] = None
    drive_folder_id: Optional[str] = None
    sheet_verified: bool = False
    drive_verified: bool = False


async def get_user_company_info(telegram_id: int) -> Optional[UserCompanyInfo]:
    """
    Получить информацию о компании пользователя и её интеграциях.
    
    Args:
        telegram_id: Telegram ID пользователя
        
    Returns:
        UserCompanyInfo или None если пользователь не найден
    """
    logger.debug(f"get_user_company_info called with: telegram_id={telegram_id}")
    
    async with async_session_factory() as session:
        # Получаем пользователя с компанией и её интеграциями
        result = await session.execute(
            select(User)
            .where(User.telegram_id == telegram_id)
            .options(
                selectinload(User.company).selectinload(Company.integrations)
            )
        )
        user = result.scalar_one_or_none()
        
        if not user or not user.company:
            logger.debug(f"Пользователь или компания не найдены: telegram_id={telegram_id}")
            return None
        
        company = user.company
        integrations = company.integrations
        
        info = UserCompanyInfo(
            user_id=user.id,
            company_id=company.id,
            company_name=company.name,
        )
        
        if integrations:
            info.sheet_id = integrations.google_sheet_id
            info.drive_folder_id = integrations.google_drive_folder_id
            info.sheet_verified = integrations.google_sheet_verified
            info.drive_verified = integrations.google_drive_verified
        
        logger.debug(f"Получена информация: company={company.name}, sheet_id={info.sheet_id}")
        return info


async def get_company_integrations(company_id: int) -> Optional[CompanyIntegrations]:
    """Получить настройки интеграций компании."""
    logger.debug(f"get_company_integrations called with: company_id={company_id}")
    
    async with async_session_factory() as session:
        result = await session.execute(
            select(CompanyIntegrations).where(CompanyIntegrations.company_id == company_id)
        )
        return result.scalar_one_or_none()
