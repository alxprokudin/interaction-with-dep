"""Процесс проработки — создание акта."""
from __future__ import annotations

import re
from typing import Any

from loguru import logger

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.services.google_sheets import google_sheets_service
from bot.services.google_drive import get_spreadsheet_link, upload_file_to_drive, get_file_link
from bot.services.iiko_service import iiko_service, search_products
from bot.services.act_generator import (
    generate_act_for_request,
    get_act_cell_value,
    add_photos_to_act,
    export_act_to_pdf,
)
from bot.services.database import get_user_company_info
from bot.keyboards.main import get_main_menu_keyboard
from bot.config import SUPERADMIN_IDS


# States для ConversationHandler
(
    DEV_MENU,
    DEV_SELECT_REQUEST,
    DEV_SEARCH_PRODUCT,
    DEV_SELECT_PRODUCT,
    DEV_CONFIRM,
    # Новые состояния для завершения проработки
    COMPLETE_SELECT_REQUEST,  # Выбор заявки для завершения
    COMPLETE_UPLOAD_PHOTOS,   # Загрузка фото
    COMPLETE_RESULT,          # Подходит / Не подходит
    COMPLETE_COMMENT,         # Комментарий
    COMPLETE_MASS_PRORABOTKA, # Массовая проработка
    COMPLETE_CONFIRM,         # Подтверждение
) = range(11)


async def show_development_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показать меню процесса проработки."""
    user_id = update.effective_user.id
    logger.info(f"show_development_menu: user_id={user_id}")
    
    keyboard = [
        [InlineKeyboardButton("📝 Выбрать заявку", callback_data="dev:create_act")],
        [InlineKeyboardButton("❌ Закрыть", callback_data="dev:close")],
    ]
    
    await update.message.reply_text(
        "🔄 <b>Проработки (Заявки)</b>\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    
    return DEV_MENU


async def create_act_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начать создание акта — показать список новых заявок."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    logger.info(f"create_act_start: user_id={user_id}")
    
    # Получаем информацию о компании пользователя
    company_info = await get_user_company_info(user_id)
    if not company_info:
        await query.edit_message_text(
            "❌ Вы не привязаны к компании. Используйте /start для регистрации."
        )
        return ConversationHandler.END
    
    context.user_data["company_info"] = {
        "sheet_id": company_info.sheet_id,
        "drive_folder_id": company_info.drive_folder_id,
        "company_name": company_info.company_name,
    }
    
    # Получаем новые заявки
    requests = await google_sheets_service.get_new_development_requests(
        sheet_id=company_info.sheet_id,
    )
    
    if not requests:
        await query.edit_message_text(
            "📭 Нет новых заявок на проработку.\n\n"
            "Все заявки уже взяты в работу или завершены."
        )
        return ConversationHandler.END
    
    # Сохраняем заявки в контексте
    context.user_data["dev_requests"] = requests
    
    # Формируем клавиатуру с заявками
    keyboard = []
    for req in requests[:10]:  # Максимум 10 заявок
        label = f"{req['request_id']} | {req['supplier_name'][:15]} | {req['nomenclature'][:20]}"
        keyboard.append([
            InlineKeyboardButton(label, callback_data=f"dev:req:{req['row_number']}")
        ])
    
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="dev:cancel")])
    
    await query.edit_message_text(
        f"📋 <b>Новые заявки на проработку</b>\n\n"
        f"Найдено заявок: {len(requests)}\n"
        f"Выберите заявку для создания акта:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    
    return DEV_SELECT_REQUEST


async def request_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора заявки — предложить продукты из iiko."""
    query = update.callback_query
    await query.answer()
    
    # Извлекаем номер строки из callback_data
    match = re.match(r"dev:req:(\d+)", query.data)
    if not match:
        await query.edit_message_text("❌ Ошибка: неверный формат данных.")
        return ConversationHandler.END
    
    row_number = int(match.group(1))
    logger.info(f"request_selected: row_number={row_number}")
    
    # Находим заявку в сохранённом списке
    requests = context.user_data.get("dev_requests", [])
    selected_request = None
    for req in requests:
        if req["row_number"] == row_number:
            selected_request = req
            break
    
    if not selected_request:
        await query.edit_message_text("❌ Заявка не найдена.")
        return ConversationHandler.END
    
    context.user_data["selected_request"] = selected_request
    
    # Показываем информацию о заявке
    await query.edit_message_text(
        f"📦 <b>Выбрана заявка</b>\n\n"
        f"ID: {selected_request['request_id']}\n"
        f"Поставщик: {selected_request['supplier_name']}\n"
        f"Товар: {selected_request['nomenclature']}\n"
        f"Цена поставщика: {selected_request['price']} руб.\n\n"
        f"⏳ Ищу похожие продукты в iiko...",
        parse_mode="HTML",
    )
    
    # Ищем похожие продукты в кеше iiko
    nomenclature = selected_request["nomenclature"]
    
    # Пробуем найти по первым словам названия
    search_terms = nomenclature.split()[:2]  # Первые 2 слова
    search_query = " ".join(search_terms) if search_terms else nomenclature[:20]
    
    products = await search_products(search_query, limit=5)
    
    if products:
        context.user_data["found_products"] = products
        
        keyboard = []
        for i, prod in enumerate(products):
            label = f"{prod['name'][:40]}"
            keyboard.append([
                InlineKeyboardButton(label, callback_data=f"dev:prod:{i}")
            ])
        
        keyboard.append([InlineKeyboardButton("🔍 Искать вручную", callback_data="dev:manual_search")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="dev:cancel")])
        
        await query.edit_message_text(
            f"📦 <b>Заявка {selected_request['request_id']}</b>\n"
            f"Товар поставщика: {nomenclature}\n\n"
            f"🔍 Найдены похожие продукты в iiko:\n"
            f"Выберите продукт для сопоставления:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        
        return DEV_SELECT_PRODUCT
    else:
        # Не нашли — предлагаем ручной поиск
        await query.edit_message_text(
            f"📦 <b>Заявка {selected_request['request_id']}</b>\n"
            f"Товар поставщика: {nomenclature}\n\n"
            f"❌ Похожие продукты не найдены в кеше iiko.\n\n"
            f"Введите название для поиска:",
            parse_mode="HTML",
        )
        
        return DEV_SEARCH_PRODUCT


async def manual_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начать ручной поиск продукта."""
    query = update.callback_query
    await query.answer()
    
    selected_request = context.user_data.get("selected_request", {})
    
    await query.edit_message_text(
        f"📦 <b>Заявка {selected_request.get('request_id', '?')}</b>\n\n"
        f"🔍 Введите название продукта для поиска в iiko:",
        parse_mode="HTML",
    )
    
    return DEV_SEARCH_PRODUCT


async def search_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ручного поиска продукта."""
    search_query = update.message.text.strip()
    logger.info(f"search_product: query={search_query}")
    
    if len(search_query) < 2:
        await update.message.reply_text(
            "⚠️ Введите минимум 2 символа для поиска."
        )
        return DEV_SEARCH_PRODUCT
    
    # Ищем в кеше
    products = await search_products(search_query, limit=10)
    
    if not products:
        await update.message.reply_text(
            f"❌ По запросу '{search_query}' ничего не найдено.\n\n"
            f"Попробуйте другой запрос или проверьте синхронизацию кеша iiko."
        )
        return DEV_SEARCH_PRODUCT
    
    context.user_data["found_products"] = products
    
    keyboard = []
    for i, prod in enumerate(products):
        label = f"{prod['name'][:40]}"
        keyboard.append([
            InlineKeyboardButton(label, callback_data=f"dev:prod:{i}")
        ])
    
    keyboard.append([InlineKeyboardButton("🔍 Искать ещё", callback_data="dev:manual_search")])
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="dev:cancel")])
    
    await update.message.reply_text(
        f"🔍 Найдено {len(products)} продуктов:\n\n"
        f"Выберите продукт для сопоставления:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    
    return DEV_SELECT_PRODUCT


async def product_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора продукта — получить цену и создать акт."""
    query = update.callback_query
    await query.answer()
    
    # Извлекаем индекс продукта
    match = re.match(r"dev:prod:(\d+)", query.data)
    if not match:
        await query.edit_message_text("❌ Ошибка: неверный формат данных.")
        return ConversationHandler.END
    
    product_idx = int(match.group(1))
    products = context.user_data.get("found_products", [])
    
    if product_idx >= len(products):
        await query.edit_message_text("❌ Продукт не найден.")
        return ConversationHandler.END
    
    selected_product = products[product_idx]
    context.user_data["selected_product"] = selected_product
    
    logger.info(f"product_selected: {selected_product['name']}")
    
    # Показываем статус
    await query.edit_message_text(
        f"⏳ Получаю цену из iiko...\n\n"
        f"Продукт: {selected_product['name']}"
    )
    
    # Получаем цену из iiko
    try:
        async with iiko_service.session() as token:
            price_data = await iiko_service.get_product_price_auto(
                token=token,
                product_name=selected_product["name"],
            )
        
        if price_data:
            iiko_price = price_data.avg_price
            logger.info(f"Получена цена: {iiko_price} руб.")
        else:
            iiko_price = 0.0
            logger.warning(f"Цена не найдена для: {selected_product['name']}")
            
    except Exception as e:
        logger.error(f"Ошибка получения цены: {e}", exc_info=True)
        iiko_price = 0.0
    
    context.user_data["iiko_price"] = iiko_price
    
    # Показываем итоги и просим подтверждение
    selected_request = context.user_data.get("selected_request", {})
    supplier_price = selected_request.get("price", "?")
    
    # Сравнение цен
    price_diff = ""
    if iiko_price > 0:
        try:
            supplier_price_float = float(str(supplier_price).replace(",", ".").replace(" ", ""))
            diff = ((supplier_price_float - iiko_price) / iiko_price) * 100
            if diff > 0:
                price_diff = f"📈 Дороже на {diff:.1f}%"
            elif diff < 0:
                price_diff = f"📉 Дешевле на {abs(diff):.1f}%"
            else:
                price_diff = "➡️ Цена равна"
        except (ValueError, ZeroDivisionError):
            price_diff = ""
    
    keyboard = [
        [InlineKeyboardButton("✅ Создать акт", callback_data="dev:confirm_create")],
        [InlineKeyboardButton("🔄 Выбрать другой продукт", callback_data="dev:manual_search")],
        [InlineKeyboardButton("❌ Отмена", callback_data="dev:cancel")],
    ]
    
    await query.edit_message_text(
        f"📋 <b>Сводка для создания акта</b>\n\n"
        f"<b>Заявка:</b> {selected_request.get('request_id', '?')}\n"
        f"<b>Поставщик:</b> {selected_request.get('supplier_name', '?')}\n"
        f"<b>Товар поставщика:</b> {selected_request.get('nomenclature', '?')}\n"
        f"<b>Цена поставщика:</b> {supplier_price} руб.\n\n"
        f"<b>Продукт iiko:</b> {selected_product['name']}\n"
        f"<b>Цена iiko:</b> {iiko_price:.2f} руб. {price_diff}\n\n"
        f"Создать акт проработки?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    
    return DEV_CONFIRM


async def confirm_create_act(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Подтверждение и создание акта."""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    logger.info(f"confirm_create_act: user={user.username or user.id}")
    
    await query.edit_message_text("⏳ Создаю акт проработки...")
    
    selected_request = context.user_data.get("selected_request", {})
    selected_product = context.user_data.get("selected_product", {})
    iiko_price = context.user_data.get("iiko_price", 0.0)
    company_info = context.user_data.get("company_info", {})
    
    try:
        # 1. Извлекаем folder_id из ссылки на папку заявки
        folder_link = selected_request.get("folder_link", "")
        folder_id = _extract_folder_id(folder_link)
        
        if not folder_id:
            logger.error(f"Не удалось извлечь folder_id из: {folder_link}")
            await query.edit_message_text(
                "❌ Ошибка: не найдена папка заявки на Google Drive."
            )
            return ConversationHandler.END
        
        # 2. Генерируем акт (копирование шаблона Google Sheets + заполнение)
        import asyncio
        
        # Подготовка данных для акта
        user_name = f"@{user.username}" if user.username else user.full_name or str(user.id)
        price_from_partner = selected_request.get("price", 0.0)
        certificate_link = selected_request.get("certificate_link", "")
        ocr_link = selected_request.get("ocr_link", "")
        
        # Период расчёта цены (7 дней по умолчанию)
        from datetime import datetime, timedelta
        date_to = datetime.now()
        date_from = date_to - timedelta(days=7)
        period_from_iiko = f"{date_from.strftime('%d.%m.%Y')} - {date_to.strftime('%d.%m.%Y')}"
        
        act_file_id = await asyncio.to_thread(
            generate_act_for_request,
            selected_request.get("request_id", "REQ-?????"),
            selected_request.get("nomenclature", ""),
            selected_request.get("supplier_name", ""),
            selected_product.get("name", ""),
            folder_id,
            user_name=user_name,
            certificate_link=certificate_link,
            ocr_link=ocr_link,
            price_from_partner=price_from_partner,
            price_from_iiko=iiko_price,
            period_from_iiko=period_from_iiko,
        )
        
        if not act_file_id:
            logger.error("Не удалось создать акт")
            await query.edit_message_text("❌ Ошибка создания акта проработки.")
            return ConversationHandler.END
        
        act_link = get_spreadsheet_link(act_file_id)
        logger.info(f"Акт создан: {act_link}")
        
        # 4. Обновляем реестр
        taken_by = f"@{user.username}" if user.username else str(user.id)
        
        success = await google_sheets_service.update_development_request_for_work(
            sheet_id=company_info.get("sheet_id", ""),
            row_number=selected_request.get("row_number", 0),
            taken_by=taken_by,
            iiko_name=selected_product.get("name", ""),
            iiko_price=iiko_price,
            act_link=act_link or "",
        )
        
        if not success:
            logger.error("Не удалось обновить реестр")
        
        # 5. Отправляем результат пользователю
        await query.edit_message_text(
            f"✅ <b>Акт проработки создан!</b>\n\n"
            f"📋 Заявка: {selected_request.get('request_id')}\n"
            f"📦 Товар: {selected_request.get('nomenclature')}\n"
            f"🔗 Продукт iiko: {selected_product.get('name')}\n"
            f"💰 Цена iiko: {iiko_price:.2f} руб.\n\n"
            f"📎 <a href='{act_link}'>Открыть акт</a>\n\n"
            f"<i>Во время проработки загрузите фото и укажите результат "
            f"через меню «Мои заявки в работе».</i>",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        
        # Очищаем данные
        _cleanup_context(context)
        
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Ошибка создания акта: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ Ошибка при создании акта:\n{str(e)[:200]}"
        )
        return ConversationHandler.END


async def my_requests_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показать заявки пользователя в работе (из меню проработки)."""
    query = update.callback_query
    await query.answer()
    
    # Используем общую логику
    return await _show_user_requests(update, context, is_callback=True)


async def start_my_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Точка входа для кнопки '📋 Заявки в работе' из главного меню."""
    logger.info(f"start_my_requests: user_id={update.effective_user.id}")
    return await _show_user_requests(update, context, is_callback=False)


async def _show_user_requests(
    update: Update, 
    context: ContextTypes.DEFAULT_TYPE, 
    is_callback: bool = False
) -> int:
    """Общая логика показа заявок пользователя в работе."""
    user = update.effective_user
    user_id = user.id
    
    # Определяем username
    username = f"@{user.username}" if user.username else user.full_name or str(user_id)
    
    # Получаем информацию о компании
    company_info = await get_user_company_info(user_id)
    if not company_info:
        msg = "❌ Вы не привязаны к компании. Используйте /start для регистрации."
        if is_callback:
            await update.callback_query.edit_message_text(msg)
        else:
            is_superadmin = user_id in SUPERADMIN_IDS
            await update.message.reply_text(msg, reply_markup=get_main_menu_keyboard(is_superadmin))
        return ConversationHandler.END
    
    context.user_data["complete_company_info"] = {
        "sheet_id": company_info.sheet_id,
        "drive_folder_id": company_info.drive_folder_id,
        "company_name": company_info.company_name,
    }
    
    # Получаем заявки пользователя
    requests = await google_sheets_service.get_user_in_progress_requests(
        sheet_id=company_info.sheet_id,
        username=username,
    )
    
    if not requests:
        msg = (
            "📭 <b>Нет активных заявок</b>\n\n"
            "У вас нет заявок в работе.\n"
            "Сначала выберите заявку через меню «Проработки (Заявки)» → «Выбрать заявку»."
        )
        if is_callback:
            await update.callback_query.edit_message_text(msg, parse_mode="HTML")
        else:
            is_superadmin = user_id in SUPERADMIN_IDS
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_main_menu_keyboard(is_superadmin))
        return ConversationHandler.END
    
    # Сохраняем заявки
    context.user_data["complete_requests"] = requests
    
    # Формируем клавиатуру
    keyboard = []
    for req in requests[:10]:
        label = f"{req['request_id']} | {req['supplier_name'][:15]} | {req['nomenclature'][:15]}"
        keyboard.append([
            InlineKeyboardButton(label, callback_data=f"compl:req:{req['row_number']}")
        ])
    
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="compl:cancel")])
    
    msg = (
        f"📋 <b>Заявки в работе</b> ({len(requests)} шт)\n\n"
        "Выберите заявку для завершения:"
    )
    
    if is_callback:
        await update.callback_query.edit_message_text(
            msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    return COMPLETE_SELECT_REQUEST


async def complete_request_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пользователь выбрал заявку для завершения."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    logger.debug(f"complete_request_selected: data={data}")
    
    row_number = int(data.split(":")[2])
    requests = context.user_data.get("complete_requests", [])
    
    selected = None
    for req in requests:
        if req["row_number"] == row_number:
            selected = req
            break
    
    if not selected:
        await query.edit_message_text("❌ Заявка не найдена.")
        return ConversationHandler.END
    
    context.user_data["complete_selected"] = selected
    context.user_data["complete_photos"] = []  # Список загруженных фото
    
    # Извлекаем act_id из ссылки на акт
    act_link = selected.get("act_link", "")
    act_id = _extract_file_id_from_act_link(act_link)
    context.user_data["complete_act_id"] = act_id
    
    # Извлекаем folder_id из ссылки на папку
    folder_link = selected.get("folder_link", "")
    folder_id = _extract_folder_id(folder_link)
    context.user_data["complete_folder_id"] = folder_id
    
    keyboard = [[InlineKeyboardButton("✅ Завершить загрузку фото", callback_data="compl:photos_done")]]
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="compl:cancel")])
    
    await query.edit_message_text(
        f"📸 <b>Загрузка фото проработки</b>\n\n"
        f"📦 Заявка: {selected['request_id']}\n"
        f"📋 Товар: {selected['nomenclature']}\n\n"
        "Отправьте фотографии проработки (можно несколько).\n"
        "Когда закончите — нажмите «Завершить загрузку фото».",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    
    return COMPLETE_UPLOAD_PHOTOS


async def complete_photo_uploaded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пользователь загрузил фото."""
    import asyncio
    import tempfile
    from pathlib import Path
    
    photo = update.message.photo[-1] if update.message.photo else None
    document = update.message.document if update.message.document else None
    
    if not photo and not document:
        await update.message.reply_text("Пожалуйста, отправьте фото или документ.")
        return COMPLETE_UPLOAD_PHOTOS
    
    folder_id = context.user_data.get("complete_folder_id")
    if not folder_id:
        await update.message.reply_text("❌ Ошибка: не найдена папка для загрузки.")
        return COMPLETE_UPLOAD_PHOTOS
    
    # Скачиваем файл
    if photo:
        file = await photo.get_file()  # photo — это PhotoSize, не tuple
        filename = f"photo_{len(context.user_data.get('complete_photos', [])) + 1}.jpg"
    else:
        file = await document.get_file()
        filename = document.file_name or f"file_{len(context.user_data.get('complete_photos', [])) + 1}"
    
    # Сохраняем во временный файл
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix) as tmp:
        await file.download_to_drive(tmp.name)
        tmp_path = tmp.name
    
    try:
        # Загружаем в Google Drive
        # Сигнатура: upload_file_to_drive(file_path, folder_id, filename)
        file_id = await asyncio.to_thread(
            upload_file_to_drive,
            tmp_path,
            folder_id,
            filename,
        )
        
        if file_id:
            link = get_file_link(file_id)
            context.user_data.setdefault("complete_photos", []).append((filename, link))
            logger.info(f"Фото загружено: {filename} -> {link}")
        else:
            await update.message.reply_text(f"⚠️ Не удалось загрузить {filename}")
    finally:
        # Удаляем временный файл
        Path(tmp_path).unlink(missing_ok=True)
    
    # Показываем текущее количество фото
    photos_count = len(context.user_data.get("complete_photos", []))
    keyboard = [[InlineKeyboardButton(f"✅ Завершить загрузку ({photos_count} фото)", callback_data="compl:photos_done")]]
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="compl:cancel")])
    
    await update.message.reply_text(
        f"📸 Загружено фото: {photos_count}\n\n"
        "Отправьте ещё фото или нажмите «Завершить загрузку».",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    
    return COMPLETE_UPLOAD_PHOTOS


async def complete_photos_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пользователь завершил загрузку фото."""
    import asyncio
    
    query = update.callback_query
    await query.answer()
    
    photos = context.user_data.get("complete_photos", [])
    act_id = context.user_data.get("complete_act_id")
    
    # Добавляем фото в акт
    if photos and act_id:
        await asyncio.to_thread(add_photos_to_act, act_id, photos)
        logger.info(f"Добавлено {len(photos)} фото в акт {act_id}")
    
    # Спрашиваем результат
    keyboard = [
        [InlineKeyboardButton("✅ Подходит", callback_data="compl:result:yes")],
        [InlineKeyboardButton("❌ Не подходит", callback_data="compl:result:no")],
        [InlineKeyboardButton("❌ Отмена", callback_data="compl:cancel")],
    ]
    
    await query.edit_message_text(
        "📊 <b>Результат проработки</b>\n\n"
        f"Загружено фото: {len(photos)}\n\n"
        "Продукт подходит для закупки?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    
    return COMPLETE_RESULT


async def complete_result_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пользователь выбрал результат (подходит/не подходит)."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    result = "Подходит" if data == "compl:result:yes" else "Не подходит"
    context.user_data["complete_result"] = result
    
    keyboard = [
        [InlineKeyboardButton("➡️ Пропустить", callback_data="compl:comment:skip")],
        [InlineKeyboardButton("❌ Отмена", callback_data="compl:cancel")],
    ]
    
    await query.edit_message_text(
        f"📝 <b>Комментарий</b>\n\n"
        f"Результат: {result}\n\n"
        "Введите комментарий (или нажмите «Пропустить»):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    
    return COMPLETE_COMMENT


async def complete_comment_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пользователь ввёл комментарий."""
    comment = update.message.text.strip()
    context.user_data["complete_comment"] = comment
    
    return await _ask_mass_prorabotka_or_finish(update, context, is_message=True)


async def complete_comment_skipped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пользователь пропустил комментарий."""
    query = update.callback_query
    await query.answer()
    
    context.user_data["complete_comment"] = ""
    
    return await _ask_mass_prorabotka_or_finish(update, context, is_message=False)


async def _ask_mass_prorabotka_or_finish(
    update: Update, 
    context: ContextTypes.DEFAULT_TYPE,
    is_message: bool = False
) -> int:
    """Спросить про массовую проработку (если подходит) или перейти к завершению."""
    result = context.user_data.get("complete_result", "")
    
    if result == "Подходит":
        keyboard = [
            [InlineKeyboardButton("✅ Да", callback_data="compl:mass:yes")],
            [InlineKeyboardButton("❌ Нет", callback_data="compl:mass:no")],
            [InlineKeyboardButton("❌ Отмена", callback_data="compl:cancel")],
        ]
        
        msg = (
            "🔄 <b>Массовая проработка</b>\n\n"
            "Нужна ли массовая проработка этого продукта?"
        )
        
        if is_message:
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.callback_query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        
        return COMPLETE_MASS_PRORABOTKA
    else:
        # Если не подходит — сразу к завершению
        context.user_data["complete_mass_prorabotka"] = ""
        return await _show_complete_confirmation(update, context, is_message)


async def complete_mass_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пользователь выбрал массовую проработку."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    mass = "Да" if data == "compl:mass:yes" else "Нет"
    context.user_data["complete_mass_prorabotka"] = mass
    
    return await _show_complete_confirmation(update, context, is_message=False)


async def _show_complete_confirmation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    is_message: bool = False
) -> int:
    """Показать подтверждение завершения."""
    import asyncio
    
    selected = context.user_data.get("complete_selected", {})
    result = context.user_data.get("complete_result", "")
    comment = context.user_data.get("complete_comment", "")
    mass = context.user_data.get("complete_mass_prorabotka", "")
    photos_count = len(context.user_data.get("complete_photos", []))
    act_id = context.user_data.get("complete_act_id")
    
    # Читаем вес из ячейки C24 акта
    weight = ""
    if act_id:
        weight = await asyncio.to_thread(get_act_cell_value, act_id, "C24")
    context.user_data["complete_weight"] = weight
    
    full_result = f"{result}: {comment}" if comment else result
    
    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить и завершить", callback_data="compl:finish")],
        [InlineKeyboardButton("❌ Отмена", callback_data="compl:cancel")],
    ]
    
    msg = (
        f"📋 <b>Подтверждение завершения</b>\n\n"
        f"📦 Заявка: {selected.get('request_id', '')}\n"
        f"📋 Товар: {selected.get('nomenclature', '')}\n"
        f"📸 Фото: {photos_count}\n"
        f"📊 Результат: {full_result}\n"
    )
    
    if mass:
        msg += f"🔄 Массовая проработка: {mass}\n"
    
    if weight:
        msg += f"⚖️ Вес с этикетки: {weight}\n"
    
    msg += "\nНажмите «Подтвердить» для завершения."
    
    if is_message:
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.callback_query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    
    return COMPLETE_CONFIRM


async def complete_finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершить проработку."""
    import asyncio
    
    query = update.callback_query
    await query.answer("Завершаем...")
    
    user_id = update.effective_user.id
    is_superadmin = user_id in SUPERADMIN_IDS
    
    selected = context.user_data.get("complete_selected", {})
    company_info = context.user_data.get("complete_company_info", {})
    result = context.user_data.get("complete_result", "")
    comment = context.user_data.get("complete_comment", "")
    mass = context.user_data.get("complete_mass_prorabotka", "")
    weight = context.user_data.get("complete_weight", "")
    act_id = context.user_data.get("complete_act_id")
    
    full_result = f"{result}: {comment}" if comment else result
    sheet_id = company_info.get("sheet_id", "")
    row_number = selected.get("row_number", 0)
    
    # 1. Обновляем реестр
    success = await google_sheets_service.complete_development_request(
        sheet_id=sheet_id,
        row_number=row_number,
        result=full_result,
        mass_prorabotka=mass,
        weight_from_label=weight,
    )
    
    if not success:
        await query.edit_message_text("❌ Ошибка обновления реестра. Попробуйте позже.")
        _cleanup_complete_context(context)
        return ConversationHandler.END
    
    # 2. Экспортируем PDF
    pdf_bytes = None
    if act_id:
        pdf_bytes = await asyncio.to_thread(export_act_to_pdf, act_id)
    
    # 3. Отправляем email поставщику
    supplier_inn = selected.get("supplier_inn", "")
    supplier_email = await google_sheets_service.get_supplier_email_by_inn(sheet_id, supplier_inn)
    
    email_sent = False
    if supplier_email and pdf_bytes:
        email_sent = await _send_completion_email(
            to_email=supplier_email,
            selected=selected,
            result=full_result,
            mass=mass,
            pdf_bytes=pdf_bytes,
        )
    
    # Формируем итоговое сообщение
    msg = (
        f"✅ <b>Проработка завершена!</b>\n\n"
        f"📦 Заявка: {selected.get('request_id', '')}\n"
        f"📋 Товар: {selected.get('nomenclature', '')}\n"
        f"📊 Результат: {full_result}\n"
    )
    
    if mass:
        msg += f"🔄 Массовая проработка: {mass}\n"
    
    if email_sent:
        msg += f"\n📧 Email отправлен на {supplier_email}"
    elif supplier_email:
        msg += f"\n⚠️ Не удалось отправить email на {supplier_email}"
    else:
        msg += "\n⚠️ Email поставщика не найден"
    
    await query.edit_message_text(msg, parse_mode="HTML")
    
    # Показываем главное меню
    await query.message.reply_text(
        "Используйте меню для продолжения работы.",
        reply_markup=get_main_menu_keyboard(is_superadmin),
    )
    
    _cleanup_complete_context(context)
    return ConversationHandler.END


async def _send_completion_email(
    to_email: str,
    selected: dict,
    result: str,
    mass: str,
    pdf_bytes: bytes,
) -> bool:
    """Отправить email о завершении проработки."""
    from bot.services.email_service import send_email, EmailMessage, DEFAULT_CC
    
    subject = f"Результат проработки: {selected.get('nomenclature', 'Товар')}"
    
    body = f"""Результат проработки продукта

Заявка: {selected.get('request_id', '')}
Товар: {selected.get('nomenclature', '')}
Поставщик: {selected.get('supplier_name', '')}
Результат: {result}
"""
    
    if mass:
        body += f"Массовая проработка: {mass}\n"
    
    body += """
Акт проработки прикреплён к письму.

С уважением,
WorkFlow Hub
"""
    
    # Формируем вложение
    attachments = [
        {
            "filename": f"Акт_проработки_{selected.get('request_id', 'XXX')}.pdf",
            "content": pdf_bytes,
            "content_type": "application/pdf",
        }
    ]
    
    email = EmailMessage(
        to=[to_email],
        cc=DEFAULT_CC,
        subject=subject,
        body=body,
        attachments=attachments,
    )
    
    return await send_email(email)


def _extract_file_id_from_act_link(link: str) -> str | None:
    """Извлечь ID файла из ссылки на Google Sheets акт."""
    if not link:
        return None
    
    # https://docs.google.com/spreadsheets/d/FILE_ID/edit
    patterns = [
        r"/d/([a-zA-Z0-9_-]+)",
        r"id=([a-zA-Z0-9_-]+)",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, link)
        if match:
            return match.group(1)
    
    return None


def _cleanup_complete_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Очистить данные контекста завершения."""
    keys = [
        "complete_company_info",
        "complete_requests",
        "complete_selected",
        "complete_photos",
        "complete_act_id",
        "complete_folder_id",
        "complete_result",
        "complete_comment",
        "complete_mass_prorabotka",
        "complete_weight",
    ]
    for key in keys:
        context.user_data.pop(key, None)


async def complete_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена процесса завершения."""
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("❌ Завершение отменено.")
    else:
        await update.message.reply_text("❌ Завершение отменено.")
    
    user_id = update.effective_user.id
    is_superadmin = user_id in SUPERADMIN_IDS
    
    if update.effective_message:
        await update.effective_message.reply_text(
            "Используйте меню для продолжения.",
            reply_markup=get_main_menu_keyboard(is_superadmin),
        )
    
    _cleanup_complete_context(context)
    return ConversationHandler.END


async def close_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Закрыть меню."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text("Меню закрыто.")
    _cleanup_context(context)
    
    return ConversationHandler.END


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена операции."""
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("❌ Операция отменена.")
    else:
        await update.message.reply_text("❌ Операция отменена.")
    
    _cleanup_context(context)
    return ConversationHandler.END


def _cleanup_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Очистить данные контекста."""
    keys_to_remove = [
        "company_info",
        "dev_requests",
        "selected_request",
        "found_products",
        "selected_product",
        "iiko_price",
    ]
    for key in keys_to_remove:
        context.user_data.pop(key, None)


def _extract_folder_id(folder_link: str) -> str | None:
    """Извлечь folder_id из ссылки Google Drive."""
    if not folder_link:
        return None
    
    # Паттерны ссылок:
    # https://drive.google.com/drive/folders/FOLDER_ID
    # https://drive.google.com/drive/u/0/folders/FOLDER_ID
    patterns = [
        r"folders/([a-zA-Z0-9_-]+)",
        r"id=([a-zA-Z0-9_-]+)",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, folder_link)
        if match:
            return match.group(1)
    
    return None


def get_development_handler() -> ConversationHandler:
    """Создать ConversationHandler для процесса проработки."""
    return ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^🔄 Проработки \\(Заявки\\)$"),
                show_development_menu,
            ),
            MessageHandler(
                filters.Regex("^📋 Заявки в работе$"),
                start_my_requests,
            ),
        ],
        states={
            # === Этап 1: Создание акта ===
            DEV_MENU: [
                CallbackQueryHandler(create_act_start, pattern=r"^dev:create_act$"),
                CallbackQueryHandler(my_requests_handler, pattern=r"^dev:my_requests$"),
                CallbackQueryHandler(close_menu, pattern=r"^dev:close$"),
            ],
            DEV_SELECT_REQUEST: [
                CallbackQueryHandler(request_selected, pattern=r"^dev:req:\d+$"),
                CallbackQueryHandler(cancel_handler, pattern=r"^dev:cancel$"),
            ],
            DEV_SEARCH_PRODUCT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_product),
                CallbackQueryHandler(cancel_handler, pattern=r"^dev:cancel$"),
            ],
            DEV_SELECT_PRODUCT: [
                CallbackQueryHandler(product_selected, pattern=r"^dev:prod:\d+$"),
                CallbackQueryHandler(manual_search_start, pattern=r"^dev:manual_search$"),
                CallbackQueryHandler(cancel_handler, pattern=r"^dev:cancel$"),
            ],
            DEV_CONFIRM: [
                CallbackQueryHandler(confirm_create_act, pattern=r"^dev:confirm_create$"),
                CallbackQueryHandler(manual_search_start, pattern=r"^dev:manual_search$"),
                CallbackQueryHandler(cancel_handler, pattern=r"^dev:cancel$"),
            ],
            # === Этап 2: Завершение проработки ===
            COMPLETE_SELECT_REQUEST: [
                CallbackQueryHandler(complete_request_selected, pattern=r"^compl:req:\d+$"),
                CallbackQueryHandler(complete_cancel, pattern=r"^compl:cancel$"),
            ],
            COMPLETE_UPLOAD_PHOTOS: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, complete_photo_uploaded),
                CallbackQueryHandler(complete_photos_done, pattern=r"^compl:photos_done$"),
                CallbackQueryHandler(complete_cancel, pattern=r"^compl:cancel$"),
            ],
            COMPLETE_RESULT: [
                CallbackQueryHandler(complete_result_selected, pattern=r"^compl:result:(yes|no)$"),
                CallbackQueryHandler(complete_cancel, pattern=r"^compl:cancel$"),
            ],
            COMPLETE_COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, complete_comment_received),
                CallbackQueryHandler(complete_comment_skipped, pattern=r"^compl:comment:skip$"),
                CallbackQueryHandler(complete_cancel, pattern=r"^compl:cancel$"),
            ],
            COMPLETE_MASS_PRORABOTKA: [
                CallbackQueryHandler(complete_mass_selected, pattern=r"^compl:mass:(yes|no)$"),
                CallbackQueryHandler(complete_cancel, pattern=r"^compl:cancel$"),
            ],
            COMPLETE_CONFIRM: [
                CallbackQueryHandler(complete_finish, pattern=r"^compl:finish$"),
                CallbackQueryHandler(complete_cancel, pattern=r"^compl:cancel$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_handler, pattern=r"^dev:cancel$"),
            CallbackQueryHandler(complete_cancel, pattern=r"^compl:cancel$"),
            MessageHandler(filters.Regex("^/cancel$"), cancel_handler),
        ],
        name="development_process",
        persistent=False,
        allow_reentry=True,
    )
