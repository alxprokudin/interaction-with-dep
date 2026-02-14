#!/usr/bin/env python3
"""Скрипт создания компании и назначения админа.

Использование:
    python scripts/create_company.py "Название компании" TELEGRAM_ID

Пример:
    python scripts/create_company.py "Моя компания" 123456789
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from bot.models import Company, User, UserRole
from bot.models.base import async_session_factory, init_db


async def create_company(name: str, admin_telegram_id: int) -> None:
    """Создать компанию и назначить админа."""
    logger.info(f"Создание компании: name={name}, admin_telegram_id={admin_telegram_id}")

    # Инициализируем БД (создаём таблицы если их нет)
    await init_db()

    async with async_session_factory() as session:
        # Создаём компанию
        company = Company(name=name)
        session.add(company)
        await session.flush()  # Получаем ID и invite_code

        logger.info(f"Компания создана: id={company.id}, invite_code={company.invite_code}")

        # Создаём админа
        admin = User(
            telegram_id=admin_telegram_id,
            company_id=company.id,
            role=UserRole.ADMIN,
            full_name="Администратор",
        )
        session.add(admin)
        await session.commit()

        logger.info(f"Админ назначен: telegram_id={admin_telegram_id}")

        print("\n" + "=" * 50)
        print("✅ КОМПАНИЯ УСПЕШНО СОЗДАНА")
        print("=" * 50)
        print(f"📛 Название: {company.name}")
        print(f"🔐 Код приглашения: {company.invite_code}")
        print(f"👤 Админ (Telegram ID): {admin_telegram_id}")
        print("=" * 50)
        print("\nДайте этот код сотрудникам для присоединения к компании.")
        print("=" * 50 + "\n")


def main() -> None:
    """Точка входа."""
    if len(sys.argv) < 3:
        print("Использование: python scripts/create_company.py \"Название компании\" TELEGRAM_ID")
        print("\nПример:")
        print('  python scripts/create_company.py "ООО Рога и Копыта" 123456789')
        print("\nКак узнать свой Telegram ID:")
        print("  1. Напишите боту @userinfobot в Telegram")
        print("  2. Он ответит вашим ID")
        sys.exit(1)

    company_name = sys.argv[1]
    try:
        admin_telegram_id = int(sys.argv[2])
    except ValueError:
        print(f"❌ Ошибка: '{sys.argv[2]}' не является числом (Telegram ID)")
        sys.exit(1)

    asyncio.run(create_company(company_name, admin_telegram_id))


if __name__ == "__main__":
    main()
