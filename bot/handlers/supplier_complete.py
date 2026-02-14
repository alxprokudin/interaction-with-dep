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
    SC_DOCUMENTS,   # Загрузка документов (договор + протокол)
) = range(2)


def _extract_folder_id_from_link(link: str) -> Optional[str]:
    """Извлечь ID папки из ссылки Google Drive."""
    if not link:
        return None
    
    match = re.search(r'/folders/([a-zA-Z0-9_-]+)', link)
    if match:
        return match.group(1)
    return None


def _get_documents_keyboard(has_files: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура для состояния загрузки документов."""
    buttons = []
    if has_files:
        buttons.append([InlineKeyboardButton("✅ Завершить", callback_data="sc_finish")])
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="sc_cancel")])
    return InlineKeyboardMarkup(buttons)


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
        f"📋 Незавершённые заявки ({len(incomplete_suppliers)})\n\n"
        "Выберите поставщика для завершения заявки:",
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
    
    # Инициализируем список загруженных файлов
    context.user_data["complete_files"] = []
    
    await query.edit_message_text(
        f"✅ Выбран поставщик: {supplier.get('name', '')}\n"
        f"ИНН: {supplier.get('inn', '')}\n\n"
        "📎 Загрузите договор и протокол (PDF, Word).\n"
        "Можно отправить несколько файлов.\n\n"
        "После загрузки нажмите Завершить.",
        reply_markup=_get_documents_keyboard(True),  # Кнопка "Завершить" сразу видна
    )
    
    return SC_DOCUMENTS


async def document_uploaded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка загрузки документа (договора или протокола)."""
    logger.info("document_uploaded called")
    
    # Определяем тип файла
    if not update.message.document:
        files = context.user_data.get("complete_files", [])
        await update.message.reply_text(
            "❌ Пожалуйста, отправьте файл (PDF или Word).",
            reply_markup=_get_documents_keyboard(len(files) > 0),
        )
        return SC_DOCUMENTS
    
    file = await update.message.document.get_file()
    filename = update.message.document.file_name
    
    # Проверяем расширение
    allowed_extensions = {".pdf", ".doc", ".docx"}
    file_ext = Path(filename).suffix.lower()
    if file_ext not in allowed_extensions:
        files = context.user_data.get("complete_files", [])
        await update.message.reply_text(
            f"❌ Неподдерживаемый формат: {file_ext}\n"
            "Допустимые форматы: PDF, DOC, DOCX",
            reply_markup=_get_documents_keyboard(len(files) > 0),
        )
        return SC_DOCUMENTS
    
    # Скачиваем файл
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
        await file.download_to_drive(tmp.name)
        tmp_path = Path(tmp.name)
    
    logger.debug(f"Файл скачан: {tmp_path}, name={filename}, size={tmp_path.stat().st_size}")
    
    # Добавляем в список файлов (молча, без сообщения)
    files = context.user_data.get("complete_files", [])
    files.append({"name": filename, "path": tmp_path})
    context.user_data["complete_files"] = files
    
    logger.info(f"Файл добавлен: {filename}, всего файлов: {len(files)}")
    
    # Не отправляем сообщение — кнопка "Завершить" уже есть в начальном сообщении
    return SC_DOCUMENTS


async def finish_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершить загрузку и обработать файлы."""
    query = update.callback_query
    await query.answer()
    
    logger.info("finish_upload called")
    
    files = context.user_data.get("complete_files", [])
    
    if not files:
        await query.edit_message_text(
            "❌ Необходимо загрузить хотя бы один файл (договор).",
            reply_markup=_get_documents_keyboard(0),
        )
        return SC_DOCUMENTS
    
    await query.edit_message_text("⏳ Завершаю оформление заявки...")
    
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
    files = context.user_data.get("complete_files", [])
    
    folder_id = supplier_info.get("folder_id")
    supplier_name = supplier_info.get("name", "")
    supplier_inn = supplier_info.get("inn", "")
    row_number = supplier_info.get("row_number")
    sheet_id = company_info.get("sheet_id")
    
    # Функция отправки сообщения
    async def send_message(text: str):
        if from_callback:
            await update.callback_query.message.reply_text(text)
        else:
            await update.message.reply_text(text)
    
    try:
        uploaded_links = []
        email_attachments = []
        
        # 1. Загружаем все файлы в Google Drive
        for file_info in files:
            file_name = file_info["name"]
            file_path = file_info["path"]
            
            logger.info(f"Загружаем файл {file_name} в папку {folder_id}")
            file_id = await asyncio.to_thread(
                upload_file_to_drive, file_path, folder_id, file_name
            )
            
            if file_id:
                link = get_file_link(file_id)
                uploaded_links.append(f"{file_name}: {link}")
                email_attachments.append((file_name, file_path))
                logger.info(f"Файл загружен: {link}")
            else:
                logger.warning(f"Не удалось загрузить файл: {file_name}")
        
        if not uploaded_links:
            await send_message("❌ Не удалось загрузить файлы в Google Drive.")
            return await _cleanup_and_end(update, context, from_callback)
        
        # 2. Отправляем email бухгалтеру
        email_message = create_email_contract_completed(
            supplier_name=supplier_name,
            supplier_inn=supplier_inn,
            attachments=email_attachments,
        )
        
        email_sent = await send_email(email_message)
        email_status = "✅" if email_sent else "❌"
        
        # 3. Обновляем колонку T в Google Sheets
        contract_info = " | ".join(uploaded_links) + f" | {datetime.now().strftime('%d.%m.%Y')}"
        
        sheet_updated = await google_sheets_service.update_contract_info(
            sheet_id=sheet_id,
            row_number=row_number,
            contract_info=contract_info,
        )
        sheet_status = "✅" if sheet_updated else "❌"
        
        # 4. Отчёт пользователю
        files_report = "\n".join([f"📁 {f['name']}: ✅" for f in files])
        report = (
            f"📋 Заявка завершена!\n\n"
            f"Поставщик: {supplier_name}\n"
            f"ИНН: {supplier_inn}\n\n"
            f"Загруженные файлы:\n{files_report}\n\n"
            f"📧 Email бухгалтеру: {email_status}\n"
            f"📊 Таблица обновлена: {sheet_status}"
        )
        
        if from_callback:
            await update.callback_query.message.reply_text(report)
        else:
            await update.message.reply_text(report)
        
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
    files = context.user_data.get("complete_files", [])
    for file_info in files:
        path = file_info.get("path")
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
        "complete_files",
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
            SC_DOCUMENTS: [
                MessageHandler(
                    filters.Document.ALL & ~filters.COMMAND,
                    document_uploaded,
                ),
                CallbackQueryHandler(finish_upload, pattern=r"^sc_finish$"),
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
