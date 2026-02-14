#!/usr/bin/env python3
"""Скрипт управления должностями компании.

Использование:
    python scripts/manage_positions.py COMPANY_ID add "Должность 1" "Должность 2" ...
    python scripts/manage_positions.py COMPANY_ID list
    python scripts/manage_positions.py COMPANY_ID delete POSITION_ID

Пример:
    python scripts/manage_positions.py 1 add "Технолог" "Закупщик" "Операционный директор" "ТСУ"
    python scripts/manage_positions.py 1 list
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger
from sqlalchemy import select

from bot.models import Company, Position
from bot.models.base import async_session_factory, init_db


async def add_positions(company_id: int, position_names: list[str]) -> None:
    """Добавить должности в компанию."""
    await init_db()

    async with async_session_factory() as session:
        # Проверяем существование компании
        result = await session.execute(select(Company).where(Company.id == company_id))
        company = result.scalar_one_or_none()
        if not company:
            print(f"❌ Компания с ID {company_id} не найдена")
            return

        # Получаем текущий максимальный sort_order
        result = await session.execute(
            select(Position.sort_order)
            .where(Position.company_id == company_id)
            .order_by(Position.sort_order.desc())
            .limit(1)
        )
        max_order = result.scalar() or 0

        print(f"\n📋 Добавление должностей в компанию «{company.name}»:\n")

        for i, name in enumerate(position_names, start=1):
            position = Position(
                company_id=company_id,
                name=name,
                sort_order=max_order + i,
            )
            session.add(position)
            print(f"  ✅ {name}")

        await session.commit()
        print(f"\n✅ Добавлено {len(position_names)} должностей\n")


async def list_positions(company_id: int) -> None:
    """Показать список должностей компании."""
    await init_db()

    async with async_session_factory() as session:
        result = await session.execute(select(Company).where(Company.id == company_id))
        company = result.scalar_one_or_none()
        if not company:
            print(f"❌ Компания с ID {company_id} не найдена")
            return

        result = await session.execute(
            select(Position)
            .where(Position.company_id == company_id, Position.is_active == True)
            .order_by(Position.sort_order)
        )
        positions = result.scalars().all()

        print(f"\n📋 Должности компании «{company.name}»:\n")
        if not positions:
            print("  (нет должностей)")
        else:
            for pos in positions:
                print(f"  [{pos.id}] {pos.name}")
        print()


async def delete_position(company_id: int, position_id: int) -> None:
    """Деактивировать должность."""
    await init_db()

    async with async_session_factory() as session:
        result = await session.execute(
            select(Position).where(
                Position.id == position_id,
                Position.company_id == company_id,
            )
        )
        position = result.scalar_one_or_none()
        if not position:
            print(f"❌ Должность не найдена")
            return

        position.is_active = False
        await session.commit()
        print(f"✅ Должность «{position.name}» деактивирована")


def main() -> None:
    """Точка входа."""
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    try:
        company_id = int(sys.argv[1])
    except ValueError:
        print(f"❌ Ошибка: '{sys.argv[1]}' не является числом (Company ID)")
        sys.exit(1)

    action = sys.argv[2].lower()

    if action == "add":
        if len(sys.argv) < 4:
            print("❌ Укажите названия должностей")
            print('Пример: python scripts/manage_positions.py 1 add "Технолог" "Закупщик"')
            sys.exit(1)
        position_names = sys.argv[3:]
        asyncio.run(add_positions(company_id, position_names))

    elif action == "list":
        asyncio.run(list_positions(company_id))

    elif action == "delete":
        if len(sys.argv) < 4:
            print("❌ Укажите ID должности для удаления")
            sys.exit(1)
        position_id = int(sys.argv[3])
        asyncio.run(delete_position(company_id, position_id))

    else:
        print(f"❌ Неизвестное действие: {action}")
        print("Доступные действия: add, list, delete")
        sys.exit(1)


if __name__ == "__main__":
    main()
