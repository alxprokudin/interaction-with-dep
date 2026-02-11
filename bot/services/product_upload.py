"""Загрузка продукта: БД + Google Drive."""
import asyncio
from pathlib import Path
from typing import Optional

from loguru import logger

from bot.models.base import async_session_factory
from bot.models.product import Product
from bot.models.user import User
from bot.services.google_drive import (
    create_product_folder,
    create_subfolder,
    upload_file_to_drive,
)


async def save_product_with_files(
    company_id: int,
    telegram_user_id: int,
    draft: dict,
) -> Optional[Product]:
    """
    Сохранить продукт в БД и загрузить файлы в Google Drive.
    draft: supplier_id, supplier_name, supplier_nomenclature, unit, price, vat_rate,
           certs, photos_product, photos_label
    """
    logger.info(
        "save_product_with_files called",
        company_id=company_id,
        telegram_user_id=telegram_user_id,
        draft_keys=list(draft.keys()),
    )

    supplier_name = draft.get("supplier_name") or "Без названия"
    nomenclature = draft.get("supplier_nomenclature") or "Продукт"

    # Создаём папку на Drive (синхронный вызов в executor)
    product_folder_id = await asyncio.to_thread(
        create_product_folder,
        product_name=nomenclature,
        supplier_name=supplier_name,
    )

    certs_folder_id = None
    product_photos_folder_id = None
    label_photos_folder_id = None

    if product_folder_id:
        certs_folder_id = await asyncio.to_thread(
            create_subfolder, product_folder_id, "Сертификаты и декларации"
        )
        product_photos_folder_id = await asyncio.to_thread(
            create_subfolder, product_folder_id, "Фото продукта"
        )
        label_photos_folder_id = await asyncio.to_thread(
            create_subfolder, product_folder_id, "Фото этикетки"
        )

    # Загружаем файлы
    def _upload_files(folder_id: Optional[str], items: list) -> None:
        if not folder_id or not items:
            return
        for item in items:
            path = Path(item.get("local_path", ""))
            if path.exists():
                upload_file_to_drive(path, folder_id, item.get("name", path.name))

    certs = draft.get("certs", [])
    photos_product = draft.get("photos_product", [])
    photos_label = draft.get("photos_label", [])

    if certs_folder_id:
        await asyncio.to_thread(_upload_files, certs_folder_id, certs)
    if product_photos_folder_id:
        await asyncio.to_thread(_upload_files, product_photos_folder_id, photos_product)
    if label_photos_folder_id:
        await asyncio.to_thread(_upload_files, label_photos_folder_id, photos_label)

    # Сохраняем в БД
    async with async_session_factory() as session:
        # Получаем или создаём user
        from sqlalchemy import select

        result = await session.execute(
            select(User).where(
                User.telegram_id == telegram_user_id,
                User.company_id == company_id,
            )
        )
        user = result.scalar_one_or_none()
        if not user:
            user = User(
                telegram_id=telegram_user_id,
                company_id=company_id,
                full_name=None,
            )
            session.add(user)
            await session.flush()

        supplier_id = draft.get("supplier_id")
        if not supplier_id:
            from bot.models.supplier import Supplier
            from bot.services.database import add_supplier

            supplier = await add_supplier(company_id, supplier_name)
            supplier_id = supplier.id

        product = Product(
            company_id=company_id,
            created_by_user_id=user.id,
            supplier_id=supplier_id,
            supplier_nomenclature=nomenclature,
            unit=draft.get("unit", "шт"),
            price=draft.get("price", 0),
            vat_rate=draft.get("vat_rate", "20%"),
            certs_folder_id=certs_folder_id,
            product_photos_folder_id=product_photos_folder_id,
            label_photos_folder_id=label_photos_folder_id,
        )
        session.add(product)
        await session.commit()
        await session.refresh(product)
        logger.info(f"Продукт сохранён: id={product.id}")
        return product
