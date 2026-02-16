"""Сервис для работы с iiko API."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import AsyncIterator, Optional

import httpx
from loguru import logger

from bot.config import get_env


# Конфигурация iiko API
IIKO_BASE_URL = get_env("IIKO_BASE_URL", "https://mnogo-lososya-centralniy-of-co.iiko.it:443/resto/api")
IIKO_LOGIN = get_env("IIKO_LOGIN", "api_reader")
IIKO_PASSWORD = get_env("IIKO_PASSWORD", "")


@dataclass
class IikoDepartment:
    """Департамент/подразделение из iiko."""
    id: str
    parent_id: str
    code: str  # SAP ID / Department.Code
    name: str
    type: str


@dataclass
class IikoProduct:
    """Продукт из iiko."""
    id: str
    num: str  # Артикул
    name: str
    product_type: str  # GOODS, DISH, etc.
    cooking_place_type: str
    main_unit: str
    product_category: str


@dataclass
class IikoProductPrice:
    """Цена продукта из iiko."""
    product_id: str
    product_name: str
    avg_price: float
    period_start: datetime
    period_end: datetime


class IikoService:
    """Сервис для работы с iiko API.
    
    ВАЖНО: Всегда использовать контекстный менеджер session() для работы с API.
    Это гарантирует правильный login/logout.
    """
    
    def __init__(self):
        self._base_url = IIKO_BASE_URL.rstrip("/")
        self._login = IIKO_LOGIN
        self._password = IIKO_PASSWORD
        self._client = httpx.AsyncClient(timeout=60.0, verify=False)
    
    async def _login_api(self) -> str:
        """Авторизация в iiko API. Возвращает токен."""
        logger.debug(f"_login_api: connecting to {self._base_url}")
        
        url = f"{self._base_url}/auth"
        params = {"login": self._login, "pass": self._password}
        
        response = await self._client.get(url, params=params)
        response.raise_for_status()
        
        token = response.text.strip()
        logger.info(f"iiko login successful, token length: {len(token)}")
        return token
    
    async def _logout_api(self, token: str) -> None:
        """Выход из iiko API. ОБЯЗАТЕЛЬНО вызывать после работы!"""
        logger.debug("_logout_api: logging out")
        
        url = f"{self._base_url}/logout"
        params = {"key": token}
        
        try:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            logger.info("iiko logout successful")
        except Exception as e:
            logger.error(f"iiko logout failed: {e}")
    
    @asynccontextmanager
    async def session(self) -> AsyncIterator[str]:
        """Контекстный менеджер для работы с iiko API.
        
        Гарантирует login при входе и logout при выходе.
        
        Usage:
            async with iiko_service.session() as token:
                products = await iiko_service.get_products(token)
        """
        token = await self._login_api()
        try:
            yield token
        finally:
            await self._logout_api(token)
    
    async def get_departments(self, token: str) -> list[IikoDepartment]:
        """Получить список всех департаментов из iiko.
        
        Args:
            token: Токен авторизации
            
        Returns:
            Список департаментов
        """
        logger.debug("get_departments: fetching departments from iiko")
        
        url = f"{self._base_url}/corporation/departments"
        params = {"key": token}
        
        response = await self._client.get(url, params=params)
        response.raise_for_status()
        
        xml_content = response.text
        departments = self._parse_departments_xml(xml_content)
        
        logger.info(f"get_departments: fetched {len(departments)} departments")
        return departments
    
    def _parse_departments_xml(self, xml_content: str) -> list[IikoDepartment]:
        """Парсинг XML с департаментами."""
        departments = []
        
        try:
            root = ET.fromstring(xml_content)
            
            for dept_dto in root.findall("corporateItemDto"):
                try:
                    dept = IikoDepartment(
                        id=self._get_xml_text(dept_dto, "id", ""),
                        parent_id=self._get_xml_text(dept_dto, "parentId", ""),
                        code=self._get_xml_text(dept_dto, "code", ""),
                        name=self._get_xml_text(dept_dto, "name", ""),
                        type=self._get_xml_text(dept_dto, "type", ""),
                    )
                    departments.append(dept)
                except Exception as e:
                    logger.warning(f"Failed to parse department: {e}")
                    
        except ET.ParseError as e:
            logger.error(f"Failed to parse departments XML: {e}")
            
        return departments
    
    async def get_active_ml_msk_departments(self, token: str) -> list[IikoDepartment]:
        """Получить активные департаменты МЛ МСК.
        
        Фильтрует департаменты по условиям:
        - Название содержит "МЛ МСК"
        - Название НЕ содержит "(закрыто)" или "(закрыта)"
        
        Args:
            token: Токен авторизации
            
        Returns:
            Список активных департаментов МЛ МСК
        """
        all_departments = await self.get_departments(token)
        
        active_departments = [
            dept for dept in all_departments
            if "МЛ МСК" in dept.name
            and "(закрыто)" not in dept.name.lower()
            and "(закрыта)" not in dept.name.lower()
        ]
        
        logger.info(f"get_active_ml_msk_departments: {len(active_departments)} active out of {len(all_departments)} total")
        return active_departments
    
    async def get_active_department_codes(self, token: str) -> list[str]:
        """Получить коды (SAP ID) активных департаментов МЛ МСК.
        
        Используется для фильтрации в OLAP отчётах.
        """
        departments = await self.get_active_ml_msk_departments(token)
        codes = [dept.code for dept in departments if dept.code]
        logger.debug(f"Active department codes: {codes}")
        return codes

    async def get_products(self, token: str) -> list[IikoProduct]:
        """Получить список всех продуктов из iiko.
        
        Args:
            token: Токен авторизации (получить через session())
            
        Returns:
            Список продуктов
        """
        logger.debug("get_products: fetching products from iiko")
        
        url = f"{self._base_url}/products"
        params = {"key": token}
        
        response = await self._client.get(url, params=params)
        response.raise_for_status()
        
        xml_content = response.text
        products = self._parse_products_xml(xml_content)
        
        logger.info(f"get_products: fetched {len(products)} products")
        return products
    
    def _parse_products_xml(self, xml_content: str) -> list[IikoProduct]:
        """Парсинг XML с продуктами."""
        products = []
        
        try:
            root = ET.fromstring(xml_content)
            
            for product_dto in root.findall("productDto"):
                try:
                    product = IikoProduct(
                        id=self._get_xml_text(product_dto, "id", ""),
                        num=self._get_xml_text(product_dto, "num", ""),
                        name=self._get_xml_text(product_dto, "name", ""),
                        product_type=self._get_xml_text(product_dto, "productType", ""),
                        cooking_place_type=self._get_xml_text(product_dto, "cookingPlaceType", ""),
                        main_unit=self._get_xml_text(product_dto, "mainUnit", ""),
                        product_category=self._get_xml_text(product_dto, "productCategory", ""),
                    )
                    products.append(product)
                except Exception as e:
                    logger.warning(f"Failed to parse product: {e}")
                    
        except ET.ParseError as e:
            logger.error(f"Failed to parse XML: {e}")
            
        return products
    
    @staticmethod
    def _get_xml_text(element: ET.Element, tag: str, default: str = "") -> str:
        """Безопасное получение текста из XML элемента."""
        child = element.find(tag)
        return child.text if child is not None and child.text else default
    
    async def get_product_price(
        self, 
        token: str,
        product_name: str,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        department_codes: Optional[list[str]] = None,
    ) -> Optional[IikoProductPrice]:
        """Получить средневзвешенную цену продукта за период через OLAP отчёт.
        
        Args:
            token: Токен авторизации
            product_name: Название продукта для поиска
            date_from: Начало периода (по умолчанию 7 дней назад)
            date_to: Конец периода (по умолчанию сегодня)
            department_codes: Коды подразделений (опционально)
            
        Returns:
            Цена продукта или None если не найден
        """
        if date_to is None:
            date_to = datetime.now()
        if date_from is None:
            date_from = date_to - timedelta(days=7)
            
        logger.debug(f"get_product_price: product={product_name}, period={date_from} - {date_to}")
        
        # Формируем запрос OLAP
        report_data = await self._fetch_olap_report(
            token=token,
            product_names=[product_name],
            date_from=date_from,
            date_to=date_to,
            department_codes=department_codes or [],
        )
        
        if not report_data:
            logger.warning(f"No price data found for product: {product_name}")
            return None
        
        # Считаем средневзвешенную цену: Sum / Amount
        total_amount = 0.0
        total_sum = 0.0
        
        for row in report_data:
            amount = row.get("Contr-Amount", 0) or 0
            price_sum = row.get("Sum.ResignedSum", 0) or 0
            total_amount += amount
            total_sum += price_sum
        
        if total_amount == 0:
            logger.warning(f"Zero amount for product: {product_name}")
            return None
            
        avg_price = total_sum / total_amount
        
        return IikoProductPrice(
            product_id="",  # Будет заполнено из данных
            product_name=product_name,
            avg_price=round(avg_price, 2),
            period_start=date_from,
            period_end=date_to,
        )
    
    async def _fetch_olap_report(
        self,
        token: str,
        product_names: list[str],
        date_from: datetime,
        date_to: datetime,
        department_codes: list[str],
    ) -> list[dict]:
        """Получить данные OLAP отчёта по транзакциям.
        
        Использует endpoint /v2/reports/olap
        """
        url = f"{self._base_url}/v2/reports/olap"
        
        # Формируем фильтры
        filters = {
            "DateTime.DateTyped": {
                "filterType": "DateRange",
                "periodType": "CUSTOM",
                "from": date_from.strftime("%Y-%m-%d"),
                "to": date_to.strftime("%Y-%m-%d"),
                "includeLow": True,
                "includeHigh": True,
            },
            "Account.Name": {
                "filterType": "IncludeValues",
                "values": ["Задолженность перед поставщиками"],
            },
            "Account.CounteragentType": {
                "filterType": "IncludeValues",
                "values": ["SUPPLIER", "INTERNAL_SUPPLIER"],
            },
            "TransactionType": {
                "filterType": "IncludeValues",
                "values": ["INVOICE"],
            },
            "Contr-Product.Type": {
                "filterType": "IncludeValues",
                "values": ["GOODS"],
            },
            "Contr-Product.Name": {
                "filterType": "IncludeValues",
                "values": product_names,
            },
        }
        
        # Добавляем фильтр по подразделениям если указаны
        if department_codes:
            filters["Department.Code"] = {
                "filterType": "IncludeValues",
                "values": department_codes,
            }
        
        params = {
            "reportType": "TRANSACTIONS",
            "buildSummary": False,
            "groupByColFields": [
                "Product.Id",
                "Contr-Product.Name",
                "Department.Code",
                "Contr-Product.Num",
                "Counteragent.Name",
                "Contr-Product.MeasureUnit",
                "Contr-Product.TopParent",
                "Contr-Product.SecondParent",
                "Department",
                "DateTime.Month",
                "DateTime.Year",
                "DateTime.DateTyped",
            ],
            "aggregateFields": ["Contr-Amount", "Sum.ResignedSum"],
            "filters": filters,
        }
        
        logger.debug(f"OLAP request to {url}")
        logger.debug(f"OLAP params: {params}")
        
        response = await self._client.post(
            url=url,
            params={"key": token},
            json=params,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        response.raise_for_status()
        
        data = response.json()
        logger.debug(f"OLAP response: {len(data) if isinstance(data, list) else 'dict'} items")
        
        return data if isinstance(data, list) else data.get("data", [])
    
    async def get_product_price_auto(
        self,
        token: str,
        product_name: str,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> Optional[IikoProductPrice]:
        """Получить цену продукта с автоматической фильтрацией по активным департаментам МЛ МСК.
        
        Удобная обёртка над get_product_price, которая:
        1. Автоматически получает коды активных департаментов
        2. Использует период 7 дней по умолчанию
        
        Args:
            token: Токен авторизации
            product_name: Название продукта
            date_from: Начало периода (по умолчанию 7 дней назад)
            date_to: Конец периода (по умолчанию сегодня)
        """
        # Получаем коды активных департаментов
        department_codes = await self.get_active_department_codes(token)
        
        if not department_codes:
            logger.warning("No active ML MSK departments found!")
            return None
        
        return await self.get_product_price(
            token=token,
            product_name=product_name,
            date_from=date_from,
            date_to=date_to,
            department_codes=department_codes,
        )
    
    async def close(self) -> None:
        """Закрыть HTTP клиент."""
        await self._client.aclose()


# Singleton instance
iiko_service = IikoService()


async def sync_products_to_db() -> int:
    """Синхронизировать продукты из iiko в локальную БД.
    
    Вызывается по расписанию (раз в сутки).
    
    Returns:
        Количество синхронизированных продуктов
    """
    from bot.models.iiko_product import IikoProductCache
    from bot.models.base import async_session_factory
    from sqlalchemy import delete
    
    logger.info("sync_products_to_db: starting sync")
    
    async with iiko_service.session() as token:
        products = await iiko_service.get_products(token)
    
    # Сохраняем в БД
    async with async_session_factory() as session:
        # Очищаем старые данные
        await session.execute(delete(IikoProductCache))
        
        # Добавляем новые
        for product in products:
            cache_item = IikoProductCache(
                iiko_id=product.id,
                num=product.num,
                name=product.name,
                name_lower=product.name.lower(),  # Для регистронезависимого поиска
                product_type=product.product_type,
                cooking_place_type=product.cooking_place_type,
                main_unit=product.main_unit,
                product_category=product.product_category,
            )
            session.add(cache_item)
        
        await session.commit()
    
    logger.info(f"sync_products_to_db: synced {len(products)} products")
    return len(products)


async def search_products(query: str, limit: int = 10) -> list[dict]:
    """Поиск продуктов в локальном кеше.
    
    Args:
        query: Поисковый запрос (часть названия)
        limit: Максимальное количество результатов
        
    Returns:
        Список найденных продуктов
    """
    from bot.models.iiko_product import IikoProductCache
    from bot.models.base import async_session_factory
    from sqlalchemy import select
    
    logger.debug(f"search_products: query={query}, limit={limit}")
    
    # Нормализуем запрос для регистронезависимого поиска (Python lower() для кириллицы)
    query_lower = query.lower().strip()
    
    async with async_session_factory() as session:
        # Поиск по name_lower (предварительно нормализованному полю)
        stmt = (
            select(IikoProductCache)
            .where(IikoProductCache.name_lower.contains(query_lower))
            .limit(limit)
        )
        result = await session.execute(stmt)
        products = result.scalars().all()
    
    return [
        {
            "id": p.iiko_id,
            "num": p.num,
            "name": p.name,
            "product_type": p.product_type,
            "main_unit": p.main_unit,
            "product_category": p.product_category,
        }
        for p in products
    ]
