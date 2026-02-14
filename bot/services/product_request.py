"""Сервис для сохранения заявки на проработку продукта."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from loguru import logger


def _calculate_deadline(sla_days: int) -> str:
    """
    Рассчитать дедлайн с учётом рабочих дней.
    
    Простой алгоритм: пропускаем субботы и воскресенья.
    """
    today = datetime.now()
    deadline = today
    days_added = 0
    
    while days_added < sla_days:
        deadline += timedelta(days=1)
        # 0 = понедельник, 6 = воскресенье
        if deadline.weekday() < 5:  # Рабочий день
            days_added += 1
    
    return deadline.strftime("%d.%m.%Y")

from bot.services.google_sheets import google_sheets_service
from bot.services.google_drive import (
    _get_drive_service,
    _get_or_create_folder,
    upload_file_to_drive,
)


def generate_request_id() -> str:
    """Генерировать уникальный ID заявки."""
    # Формат: REQ-XXXXX (5 символов, буквы+цифры)
    suffix = secrets.token_hex(3).upper()[:5]
    request_id = f"REQ-{suffix}"
    logger.debug(f"Сгенерирован ID заявки: {request_id}")
    return request_id


async def save_product_request(
    company_info: dict,
    draft: dict,
    telegram_username: str,
) -> Optional[dict]:
    """
    Сохранить заявку на проработку продукта.
    
    1. Генерирует уникальный ID
    2. Создаёт папки в Google Drive
    3. Загружает файлы
    4. Сохраняет строку в Google Sheets
    
    Args:
        company_info: Информация о компании (sheet_id, drive_folder_id)
        draft: Черновик продукта
        telegram_username: Username пользователя
        
    Returns:
        Словарь с результатом или None при ошибке
    """
    logger.info(f"save_product_request: supplier={draft.get('supplier_name')}")
    logger.debug(f"save_product_request draft keys: {list(draft.keys())}")
    logger.debug(f"save_product_request photos_product: {len(draft.get('photos_product', []))}")
    logger.debug(f"save_product_request photos_label: {len(draft.get('photos_label', []))}")
    logger.debug(f"save_product_request certs: {len(draft.get('certs', []))}")
    
    request_id = generate_request_id()
    sheet_id = company_info.get("sheet_id")
    drive_folder_id = company_info.get("drive_folder_id")
    
    if not sheet_id:
        logger.error("sheet_id не указан")
        return None
    
    supplier_name = draft.get("supplier_name", "Без_поставщика")
    nomenclature = draft.get("supplier_nomenclature", "Без_названия")
    
    # Создаём папки в Google Drive
    folder_link = None
    product_folder_id = None
    
    if drive_folder_id:
        try:
            product_folder_id, folder_link = await _create_product_folders(
                drive_folder_id,
                supplier_name,
                nomenclature,
                request_id,
            )
        except Exception as e:
            logger.error(f"Ошибка создания папок: {e}", exc_info=True)
    
    # Загружаем файлы
    certs_count = 0
    certs_links = []
    photos_product_count = 0
    photos_product_links = []
    photos_label_count = 0
    photos_label_links = []
    ocr_text_link = None
    
    if product_folder_id:
        certs_count, certs_links = await _upload_files_to_folder(
            product_folder_id, "Сертификаты", draft.get("certs", [])
        )
        photos_product_count, photos_product_links = await _upload_files_to_folder(
            product_folder_id, "Фото_продукта", draft.get("photos_product", [])
        )
        photos_label_count, photos_label_links = await _upload_files_to_folder(
            product_folder_id, "Фото_этикетки", draft.get("photos_label", [])
        )
        
        # Сохраняем OCR текст в txt файл
        ocr_text = draft.get("ocr_text", "")
        if ocr_text:
            ocr_text_link = await _save_ocr_text_to_drive(
                product_folder_id,
                ocr_text,
                f"ocr_{request_id}.txt",
            )
    
    # Формируем строки со ссылками (через запятую если много)
    certs_links_str = ", ".join(certs_links) if certs_links else ""
    photos_product_links_str = ", ".join(photos_product_links) if photos_product_links else ""
    photos_label_links_str = ", ".join(photos_label_links) if photos_label_links else ""
    
    # Тип заявки и SLA
    request_type = draft.get("request_type", "regular")
    sla_days = draft.get("sla_days", 14)
    request_type_label = "Срочная" if request_type == "urgent" else "Регулярная"
    deadline = _calculate_deadline(sla_days)
    
    # Сохраняем в Google Sheets
    row_data = [
        datetime.now().strftime("%d.%m.%Y %H:%M:%S"),  # A - Дата
        request_id,                                      # B - ID заявки
        request_type_label,                              # C - Тип заявки
        str(sla_days),                                   # D - SLA (дни)
        deadline,                                        # E - Дедлайн
        supplier_name,                                   # F - Поставщик
        draft.get("supplier_inn", ""),                   # G - ИНН
        nomenclature,                                    # H - Номенклатура
        draft.get("unit", ""),                           # I - Ед. изм.
        str(draft.get("price", "")),                     # J - Цена без НДС
        folder_link or "",                               # K - Ссылка на папку
        certs_links_str,                                 # L - Ссылки на сертификаты
        photos_product_links_str,                        # M - Ссылки на фото продукта
        photos_label_links_str,                          # N - Ссылки на фото этикетки
        ocr_text_link or "",                             # O - Ссылка на OCR текст
        telegram_username,                               # P - Ответственный
        "Новая",                                         # Q - Статус
    ]
    
    success = await google_sheets_service.append_row(
        sheet_id,
        "Реестр_Проработки",
        row_data,
    )
    
    if success:
        logger.info(f"Заявка {request_id} сохранена в таблицу")
    else:
        logger.error(f"Ошибка сохранения заявки {request_id}")
    
    return {
        "request_id": request_id,
        "folder_link": folder_link,
        "certs_count": certs_count,
        "photos_product_count": photos_product_count,
        "photos_label_count": photos_label_count,
        "success": success,
    }


async def _create_product_folders(
    root_folder_id: str,
    supplier_name: str,
    nomenclature: str,
    request_id: str,
) -> tuple[Optional[str], Optional[str]]:
    """
    Создать структуру папок для продукта.
    
    Структура:
    Проработки_Товары_Акты/{Поставщик}/{Номенклатура} [REQ-XXXXX]/
    
    Returns:
        (folder_id, folder_link)
    """
    import asyncio
    
    logger.debug(f"_create_product_folders: supplier={supplier_name}, nomenclature={nomenclature}")
    
    service = _get_drive_service()
    if not service:
        return None, None
    
    try:
        def _create_folders():
            # 1. Проработки_Товары_Акты
            prorabotki_id = _get_or_create_folder(service, root_folder_id, "Проработки_Товары_Акты")
            
            # 2. Папка поставщика
            supplier_folder_id = _get_or_create_folder(service, prorabotki_id, supplier_name)
            
            # 3. Папка продукта с ID заявки
            product_folder_name = f"{nomenclature} [{request_id}]"
            product_folder_id = _get_or_create_folder(service, supplier_folder_id, product_folder_name)
            
            # Создаём подпапки
            _get_or_create_folder(service, product_folder_id, "Сертификаты")
            _get_or_create_folder(service, product_folder_id, "Фото_продукта")
            _get_or_create_folder(service, product_folder_id, "Фото_этикетки")
            
            # Формируем ссылку на папку
            folder_link = f"https://drive.google.com/drive/folders/{product_folder_id}"
            
            return product_folder_id, folder_link
        
        folder_id, folder_link = await asyncio.to_thread(_create_folders)
        logger.info(f"Папка создана: {folder_link}")
        return folder_id, folder_link
        
    except Exception as e:
        logger.error(f"Ошибка создания папок: {e}", exc_info=True)
        return None, None


async def _upload_files_to_folder(
    parent_folder_id: str,
    subfolder_name: str,
    files: list[dict],
) -> tuple[int, list[str]]:
    """
    Загрузить файлы в подпапку.
    
    Args:
        parent_folder_id: ID родительской папки продукта
        subfolder_name: Название подпапки (Сертификаты, Фото_продукта, Фото_этикетки)
        files: Список файлов [{"name": ..., "local_path": ...}, ...]
        
    Returns:
        (количество загруженных, список ссылок на файлы)
    """
    import asyncio
    
    if not files:
        return 0, []
    
    logger.debug(f"_upload_files_to_folder: {subfolder_name}, files={len(files)}")
    
    service = _get_drive_service()
    if not service:
        return 0, []
    
    uploaded = 0
    file_links = []
    
    try:
        def _get_subfolder():
            return _get_or_create_folder(service, parent_folder_id, subfolder_name)
        
        subfolder_id = await asyncio.to_thread(_get_subfolder)
        
        for file_info in files:
            local_path = file_info.get("local_path")
            filename = file_info.get("name", "file")
            
            if local_path and Path(local_path).exists():
                # Определяем mime type
                suffix = Path(local_path).suffix.lower()
                mime_types = {
                    ".pdf": "application/pdf",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".doc": "application/msword",
                    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                }
                mime_type = mime_types.get(suffix, "application/octet-stream")
                
                file_id = upload_file_to_drive(
                    Path(local_path),
                    subfolder_id,
                    filename,
                    mime_type,
                )
                
                if file_id:
                    uploaded += 1
                    file_link = f"https://drive.google.com/file/d/{file_id}/view"
                    file_links.append(file_link)
                    logger.debug(f"Файл загружен: {filename} -> {file_link}")
                    
                    # Удаляем временный файл
                    try:
                        Path(local_path).unlink()
                    except Exception:
                        pass
    
    except Exception as e:
        logger.error(f"Ошибка загрузки файлов в {subfolder_name}: {e}", exc_info=True)
    
    logger.info(f"Загружено в {subfolder_name}: {uploaded}/{len(files)}")
    return uploaded, file_links


async def _save_ocr_text_to_drive(
    parent_folder_id: str,
    ocr_text: str,
    filename: str = "ocr_text.txt",
) -> Optional[str]:
    """
    Сохранить распознанный OCR текст в txt файл на Google Drive.
    
    Returns:
        Ссылка на файл или None
    """
    import asyncio
    import tempfile
    
    if not ocr_text:
        return None
    
    logger.debug(f"_save_ocr_text_to_drive: text_length={len(ocr_text)}")
    
    service = _get_drive_service()
    if not service:
        return None
    
    try:
        # Создаём временный txt файл
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', encoding='utf-8') as tmp:
            tmp.write(ocr_text)
            tmp_path = tmp.name
        
        # Загружаем в Drive
        file_id = upload_file_to_drive(
            Path(tmp_path),
            parent_folder_id,
            filename,
            "text/plain",
        )
        
        # Удаляем временный файл
        try:
            Path(tmp_path).unlink()
        except Exception:
            pass
        
        if file_id:
            file_link = f"https://drive.google.com/file/d/{file_id}/view"
            logger.info(f"OCR текст сохранён: {file_link}")
            return file_link
        
        return None
        
    except Exception as e:
        logger.error(f"Ошибка сохранения OCR текста: {e}", exc_info=True)
        return None
