"""Сервис для работы с DaData API (данные по ИНН)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import httpx
from loguru import logger

from bot.config import get_env


DADATA_API_KEY = get_env("DADATA_API_KEY", "")
DADATA_SECRET_KEY = get_env("DADATA_SECRET_KEY", "")

DADATA_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"


@dataclass
class CompanyInfo:
    """Информация о компании из DaData."""
    inn: str
    kpp: str
    name: str  # Полное наименование
    short_name: str  # Краткое наименование
    ogrn: Optional[str] = None
    address: Optional[str] = None
    management_name: Optional[str] = None  # ФИО руководителя
    status: Optional[str] = None  # ACTIVE, LIQUIDATING, LIQUIDATED, etc.


async def get_company_by_inn(inn: str) -> Optional[CompanyInfo]:
    """
    Получить информацию о компании по ИНН.
    
    Args:
        inn: ИНН организации (10 или 12 цифр)
        
    Returns:
        CompanyInfo или None при ошибке
    """
    logger.debug(f"get_company_by_inn called with: inn={inn}")
    
    if not DADATA_API_KEY:
        logger.warning("DADATA_API_KEY не задан")
        return None
    
    # Очистка ИНН
    inn_clean = "".join(c for c in inn if c.isdigit())
    
    if len(inn_clean) not in (10, 12):
        logger.warning(f"Неверная длина ИНН: {len(inn_clean)}")
        return None
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Token {DADATA_API_KEY}",
    }
    
    payload = {"query": inn_clean}
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                DADATA_URL,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        
        suggestions = data.get("suggestions", [])
        if not suggestions:
            logger.info(f"Компания по ИНН {inn_clean} не найдена")
            return None
        
        # Берём первый результат
        suggestion = suggestions[0]
        company_data = suggestion.get("data", {})
        
        # Извлекаем данные
        name_data = company_data.get("name", {})
        address_data = company_data.get("address", {})
        management = company_data.get("management", {})
        
        info = CompanyInfo(
            inn=company_data.get("inn", inn_clean),
            kpp=company_data.get("kpp") or "-",
            name=name_data.get("full_with_opf") or suggestion.get("value", ""),
            short_name=name_data.get("short_with_opf") or name_data.get("short") or "",
            ogrn=company_data.get("ogrn"),
            address=address_data.get("value") if address_data else None,
            management_name=management.get("name") if management else None,
            status=company_data.get("state", {}).get("status"),
        )
        
        logger.info(f"Компания найдена: {info.short_name} (ИНН: {info.inn}, КПП: {info.kpp})")
        return info
        
    except httpx.TimeoutException:
        logger.error("Таймаут запроса к DaData")
        return None
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP ошибка DaData: {e.response.status_code}")
        return None
    except Exception as e:
        logger.error(f"Ошибка запроса к DaData: {e}", exc_info=True)
        return None
