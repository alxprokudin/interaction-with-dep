"""Завершение заявки поставщика — загрузка договора и протокола."""
from __future__ import annotations

import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.keyboards.main import get_main_menu_keyboard
from bot.keyboards.product_registration import get_cancel_keyboard
from bot.config import SUPERADMIN_IDS
from bot.services.database import get_user_company_info
from bot.services.google_sheets import google_sheets_service
from bot.services.google_drive import upload_file_to_drive, get_file_link
from bot.services.email_service import (
    create_email_contract_completed,
    send_email,
)


# Состояния диалога
(
    SC_SELECT,      # Выбор поставщика из списка
    SC_CONTRACT,    # Загрузка договора
    SC_PROTOCOL,    # Загрузка протокола (опционально)
) = range(3)


def _extract_folder_id_from_link(link: str) -> Optional[str]:
    """Извлечь ID папки из ссылки Google Drive."""
    # https://drive.google.com/drive/folders/FOLDER_ID
    # https://drive.google.com/drive/folders/FOLDER_ID?usp=sharing
    if not link:
        return None
    
    match = re.search(r'/folders/([a-zA-Z0-9_-]+)', link)
    if match:
        return match.group(1)
    return None


async def start_supplier_complete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало завершения заявки поставщика."""
    telegram_id = update.effective_user.id
    logger.info(f"start_supplier_complete called: user_id={telegram_id}")
    
    # Получаем информацию о компании пользователя
    company_info = await get_user_company_info(telegram_id)
    
    if not company_info:
        is_superadmin = telegram_id in SUPERADMIN_IDS
        await update.message.reply_text(
            "⚠️ Вы не состоите в компании.\n"
            "Для завершения заявок необходимо быть участником компании.",
            reply_markup=get_main_menu_keyboard(is_superadmin),
        )
        return ConversationHandler.END
    
    sheet_id = company_info.sheet_id
    if not sheet_id or not company_info.sheet_verified:
        is_superadmin = telegram_id in SUPERADMIN_IDS
        await update.message.reply_text(
            f"⚠️ Для компании «{company_info.company_name}» не настроена Google Таблица.\n"
            "Обратитесь к администратору.",
            reply_markup=get_main_menu_keyboard(is_superadmin),
        )
        return ConversationHandler.END
    
    # Сохраняем информацию о компании как словарь
    context.user_data["complete_company_info"] = {
        "company_id": company_info.company_id,
        "company_name": company_info.company_name,
        "sheet_id": company_info.sheet_id,
        "drive_folder_id": company_info.drive_folder_id,
    }
    
    # Получаем незавершённые заявки
    await update.message.reply_text("🔍 Загружаю список незавершённых заявок...")
    
    incomplete_suppliers = await google_sheets_service.get_incomplete_suppliers(sheet_id)
    
    if not incomplete_suppliers:
        is_superadmin = telegram_id in SUPERADMIN_IDS
        await update.message.reply_text(
            "✅ Все заявки завершены!\n\n"
            "Незавершённых заявок (без договора) не найдено.",
            reply_markup=get_main_menu_keyboard(is_superadmin),
        )
        return ConversationHandler.END
    
    # Формируем клавиатуру с поставщиками
    keyboard = []
    for supplier in incomplete_suppliers[:20]:  # Ограничение на 20 для UI
        name = supplier.get("name", "Без названия")[:30]
        inn = supplier.get("inn", "")
        row_num = supplier.get("row_number", 0)
        
        button_text = f"{name} ({inn})"
        callback_data = f"sc_select:{row_num}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="sc_cancel")])
    
    # Сохраняем список для последующего использования
    context.user_data["incomplete_suppliers"] = {s["row_number"]: s for s in incomplete_suppliers}
    
    await update.message.reply_text(
        f"📋 *Незавершённые заявки ({len(incomplete_suppliers)})*\n\n"
        "Выберите поставщика для завершения заявки:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    
    return SC_SELECT


async def supplier_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора поставщика."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    logger.info(f"supplier_selected: data={data}")
    
    if data == "sc_cancel":
        return await cancel_handler(update, context)
    
    # Парсим row_number
    try:
        row_number = int(data.replace("sc_select:", ""))
    except ValueError:
        await query.edit_message_text("❌ Ошибка выбора. Попробуйте снова.")
        return ConversationHandler.END
    
    # Получаем данные поставщика
    suppliers = context.user_data.get("incomplete_suppliers", {})
    supplier = suppliers.get(row_number)
    
    if not supplier:
        await query.edit_message_text("❌ Поставщик не найден. Попробуйте снова.")
        return ConversationHandler.END
    
    # Извлекаем folder_id из ссылки
    folder_link = supplier.get("folder_link", "")
    folder_id = _extract_folder_id_from_link(folder_link)
    
    if not folder_id:
        await query.edit_message_text(
            f"❌ Не удалось определить папку поставщика.\n"
            f"Ссылка на папку: {folder_link}\n\n"
            "Обратитесь к администратору."
        )
        return ConversationHandler.END
    
    # Сохраняем выбранного поставщика
    context.user_data["complete_supplier_info"] = {
        "row_number": row_number,
        "name": supplier.get("name", ""),
        "inn": supplier.get("inn", ""),
        "folder_id": folder_id,
        "folder_link": folder_link,
        "tracking_code": supplier.get("tracking_code", ""),
    }
    
    await query.edit_message_text(
        f"✅ Выбран поставщик: *{supplier.get('name', '')}*\n"
        f"ИНН: {supplier.get('inn', '')}\n\n"
        "📎 Теперь загрузите *договор* (PDF, Word).\n\n"
        "Файл будет сохранён в папку поставщика и отправлен бухгалтеру.",
        parse_mode="Markdown",
    )
    
    return SC_CONTRACT


async def contract_uploaded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка загрузки договора."""
    logger.info("contract_uploaded called")
    
    # Определяем тип файла
    if update.message.document:
        file = await update.message.document.get_file()
        filename = update.message.document.file_name
        mime_type = update.message.document.mime_type or "application/octet-stream"
    else:
        await update.message.reply_text(
            "❌ Пожалуйста, отправьте файл договора (PDF или Word).",
            reply_markup=get_cancel_keyboard(),
        )
        return SC_CONTRACT
    
    # Проверяем расширение
    allowed_extensions = {".pdf", ".doc", ".docx"}
    file_ext = Path(filename).suffix.lower()
    if file_ext not in allowed_extensions:
        await update.message.reply_text(
            f"❌ Неподдерживаемый формат файла: {file_ext}\n"
            "Допустимые форматы: PDF, DOC, DOCX",
            reply_markup=get_cancel_keyboard(),
        )
        return SC_CONTRACT
    
    # Скачиваем файл
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
        await file.download_to_drive(tmp.name)
        tmp_path = Path(tmp.name)
    
    logger.debug(f"Договор скачан: {tmp_path}, size={tmp_path.stat().st_size}")
    
    # Сохраняем путь и имя файла
    context.user_data["complete_contract_path"] = tmp_path
    context.user_data["complete_contract_name"] = filename
    
    await update.message.reply_text(
        f"✅ Договор *{filename}* получен!\n\n"
        "📎 Теперь загрузите *протокол разногласий* (PDF, Word).\n\n"
        "Если протокола нет — нажмите кнопку *Пропустить*.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭ Пропустить протокол", callback_data="sc_skip_protocol")],
            [InlineKeyboardButton("❌ Отмена", callback_data="sc_cancel")],
        ]),
    )
    
    return SC_PROTOCOL


async def protocol_uploaded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка загрузки протокола."""
    logger.info("protocol_uploaded called")
    
    # Определяем тип файла
    if update.message.document:
        file = await update.message.document.get_file()
        filename = update.message.document.file_name
        mime_type = update.message.document.mime_type or "application/octet-stream"
    else:
        await update.message.reply_text(
            "❌ Пожалуйста, отправьте файл протокола (PDF или Word) "
            "или нажмите кнопку Пропустить.",
        )
        return SC_PROTOCOL
    
    # Проверяем расширение
    allowed_extensions = {".pdf", ".doc", ".docx"}
    file_ext = Path(filename).suffix.lower()
    if file_ext not in allowed_extensions:
        await update.message.reply_text(
            f"❌ Неподдерживаемый формат файла: {file_ext}\n"
            "Допустимые форматы: PDF, DOC, DOCX",
        )
        return SC_PROTOCOL
    
    # Скачиваем файл
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
        await file.download_to_drive(tmp.name)
        tmp_path = Path(tmp.name)
    
    logger.debug(f"Протокол скачан: {tmp_path}, size={tmp_path.stat().st_size}")
    
    # Сохраняем путь и имя файла
    context.user_data["complete_protocol_path"] = tmp_path
    context.user_data["complete_protocol_name"] = filename
    
    # Завершаем процесс
    return await _finalize_completion(update, context)


async def skip_protocol(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пропустить загрузку протокола."""
    query = update.callback_query
    await query.answer()
    
    logger.info("skip_protocol called")
    
    if query.data == "sc_cancel":
        return await cancel_handler(update, context)
    
    # Протокол не загружен
    context.user_data["complete_protocol_path"] = None
    context.user_data["complete_protocol_name"] = None
    
    await query.edit_message_text("⏳ Завершаю оформление заявки...")
    
    # Завершаем процесс
    return await _finalize_completion(update, context, from_callback=True)


async def _finalize_completion(
    update: Update, 
    context: ContextTypes.DEFAULT_TYPE,
    from_callback: bool = False,
) -> int:
    """Финализация: загрузка в Drive, отправка email, обновление таблицы."""
    import asyncio
    
    supplier_info = context.user_data.get("complete_supplier_info", {})
    company_info = context.user_data.get("complete_company_info", {})
    
    folder_id = supplier_info.get("folder_id")
    supplier_name = supplier_info.get("name", "")
    supplier_inn = supplier_info.get("inn", "")
    row_number = supplier_info.get("row_number")
    sheet_id = company_info.get("sheet_id")
    
    contract_path = context.user_data.get("complete_contract_path")
    contract_name = context.user_data.get("complete_contract_name", "Договор")
    protocol_path = context.user_data.get("complete_protocol_path")
    protocol_name = context.user_data.get("complete_protocol_name")
    
    # Функция отправки сообщения
    async def send_message(text: str):
        if from_callback:
            await update.callback_query.message.reply_text(text, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, parse_mode="Markdown")
    
    try:
        # 1. Загружаем договор в Google Drive
        logger.info(f"Загружаем договор в папку {folder_id}")
        contract_file_id = await asyncio.to_thread(
            upload_file_to_drive, contract_path, folder_id, contract_name
        )
        
        if not contract_file_id:
            await send_message("❌ Ошибка загрузки договора в Google Drive.")
            return await _cleanup_and_end(update, context, from_callback)
        
        contract_link = get_file_link(contract_file_id)
        logger.info(f"Договор загружен: {contract_link}")
        
        # 2. Загружаем протокол (если есть)
        protocol_link = ""
        if protocol_path and protocol_path.exists():
            logger.info(f"Загружаем протокол в папку {folder_id}")
            protocol_file_id = await asyncio.to_thread(
                upload_file_to_drive, protocol_path, folder_id, protocol_name
            )
            
            if protocol_file_id:
                protocol_link = get_file_link(protocol_file_id)
                logger.info(f"Протокол загружен: {protocol_link}")
        
        # 3. Формируем вложения для письма
        attachments = [(contract_name, contract_path)]
        if protocol_path and protocol_path.exists():
            attachments.append((protocol_name, protocol_path))
        
        # 4. Отправляем email бухгалтеру
        email_message = create_email_contract_completed(
            supplier_name=supplier_name,
            supplier_inn=supplier_inn,
            attachments=attachments,
        )
        
        email_sent = await send_email(email_message)
        email_status = "✅" if email_sent else "❌"
        
        # 5. Обновляем колонку T в Google Sheets
        contract_info_parts = [f"Договор: {contract_link}"]
        if protocol_link:
            contract_info_parts.append(f"Протокол: {protocol_link}")
        contract_info_parts.append(datetime.now().strftime("%d.%m.%Y"))
        
        contract_info = " | ".join(contract_info_parts)
        
        sheet_updated = await google_sheets_service.update_contract_info(
            sheet_id=sheet_id,
            row_number=row_number,
            contract_info=contract_info,
        )
        sheet_status = "✅" if sheet_updated else "❌"
        
        # 6. Отчёт пользователю
        report = (
            f"📋 *Заявка завершена!*\n\n"
            f"*Поставщик:* {supplier_name}\n"
            f"*ИНН:* {supplier_inn}\n\n"
            f"*Результаты:*\n"
            f"📁 Договор загружен в Drive: ✅\n"
            f"📁 Протокол загружен: {'✅' if protocol_link else 'Пропущен'}\n"
            f"📧 Email бухгалтеру: {email_status}\n"
            f"📊 Таблица обновлена: {sheet_status}\n"
        )
        
        await send_message(report)
        
    except Exception as e:
        logger.error(f"Ошибка завершения заявки: {e}", exc_info=True)
        await send_message(f"❌ Произошла ошибка: {str(e)[:100]}")
    
    return await _cleanup_and_end(update, context, from_callback)


async def _cleanup_and_end(
    update: Update, 
    context: ContextTypes.DEFAULT_TYPE,
    from_callback: bool = False,
) -> int:
    """Очистка контекста и завершение."""
    # Удаляем временные файлы
    for key in ["complete_contract_path", "complete_protocol_path"]:
        path = context.user_data.get(key)
        if path and isinstance(path, Path) and path.exists():
            try:
                path.unlink()
                logger.debug(f"Временный файл удалён: {path}")
            except Exception:
                pass
    
    # Очищаем контекст
    keys_to_remove = [
        "complete_company_info",
        "complete_supplier_info", 
        "incomplete_suppliers",
        "complete_contract_path",
        "complete_contract_name",
        "complete_protocol_path",
        "complete_protocol_name",
    ]
    for key in keys_to_remove:
        context.user_data.pop(key, None)
    
    # Показываем главное меню
    is_superadmin = update.effective_user.id in SUPERADMIN_IDS
    
    if from_callback:
        await update.callback_query.message.reply_text(
            "Выберите действие:",
            reply_markup=get_main_menu_keyboard(is_superadmin),
        )
    else:
        await update.message.reply_text(
            "Выберите действие:",
            reply_markup=get_main_menu_keyboard(is_superadmin),
        )
    
    return ConversationHandler.END


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена завершения заявки."""
    logger.info("cancel_handler called")
    
    # Определяем источник (callback или message)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Завершение заявки отменено.")
        return await _cleanup_and_end(update, context, from_callback=True)
    else:
        await update.message.reply_text("❌ Завершение заявки отменено.")
        return await _cleanup_and_end(update, context, from_callback=False)


def get_supplier_complete_handler() -> ConversationHandler:
    """Создать ConversationHandler для завершения заявки."""
    return ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^✅ Завершить заявку$"),
                start_supplier_complete,
            ),
        ],
        states={
            SC_SELECT: [
                CallbackQueryHandler(supplier_selected, pattern=r"^sc_select:\d+$"),
                CallbackQueryHandler(cancel_handler, pattern=r"^sc_cancel$"),
            ],
            SC_CONTRACT: [
                MessageHandler(
                    filters.Document.ALL & ~filters.COMMAND,
                    contract_uploaded,
                ),
                MessageHandler(
                    filters.Regex("^❌ Отмена$"),
                    cancel_handler,
                ),
            ],
            SC_PROTOCOL: [
                MessageHandler(
                    filters.Document.ALL & ~filters.COMMAND,
                    protocol_uploaded,
                ),
                CallbackQueryHandler(skip_protocol, pattern=r"^sc_skip_protocol$"),
                CallbackQueryHandler(cancel_handler, pattern=r"^sc_cancel$"),
                MessageHandler(
                    filters.Regex("^❌ Отмена$"),
                    cancel_handler,
                ),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^/cancel$"), cancel_handler),
            MessageHandler(filters.Regex("^❌ Отмена$"), cancel_handler),
            CallbackQueryHandler(cancel_handler, pattern=r"^sc_cancel$"),
        ],
        name="supplier_complete",
        persistent=False,
    )
