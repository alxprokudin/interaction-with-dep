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
from bot.services.google_drive import get_spreadsheet_link
from bot.services.iiko_service import iiko_service, search_products
from bot.services.act_generator import generate_act_for_request
from bot.services.database import get_user_company_info


# States для ConversationHandler
(
    DEV_MENU,
    DEV_SELECT_REQUEST,
    DEV_SEARCH_PRODUCT,
    DEV_SELECT_PRODUCT,
    DEV_CONFIRM,
) = range(5)


async def show_development_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показать меню процесса проработки."""
    user_id = update.effective_user.id
    logger.info(f"show_development_menu: user_id={user_id}")
    
    keyboard = [
        [InlineKeyboardButton("📝 Выбрать заявку", callback_data="dev:create_act")],
        [InlineKeyboardButton("📋 Мои заявки в работе", callback_data="dev:my_requests")],
        [InlineKeyboardButton("❌ Закрыть", callback_data="dev:close")],
    ]
    
    await update.message.reply_text(
        "🔄 <b>Процесс проработки</b>\n\n"
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
        
        act_file_id = await asyncio.to_thread(
            generate_act_for_request,
            selected_request.get("request_id", "REQ-?????"),
            selected_request.get("nomenclature", ""),
            selected_request.get("supplier_name", ""),
            selected_product.get("name", ""),
            folder_id,
        )
        
        if not act_file_id:
            logger.error("Не удалось создать акт")
            await query.edit_message_text("❌ Ошибка создания акта проработки.")
            return ConversationHandler.END
        
        act_link = get_spreadsheet_link(act_file_id)
        logger.info(f"Акт создан: {act_link}")
        
        # 4. Обновляем реестр
        responsible = f"@{user.username}" if user.username else str(user.id)
        
        success = await google_sheets_service.update_development_request_for_work(
            sheet_id=company_info.get("sheet_id", ""),
            row_number=selected_request.get("row_number", 0),
            responsible=responsible,
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
    """Показать заявки пользователя в работе (заглушка)."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "📋 <b>Мои заявки в работе</b>\n\n"
        "Этот раздел будет реализован в Этапе 2.",
        parse_mode="HTML",
    )
    
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
                filters.Regex("^🔄 Процесс проработки$"),
                show_development_menu,
            ),
        ],
        states={
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
        },
        fallbacks=[
            CallbackQueryHandler(cancel_handler, pattern=r"^dev:cancel$"),
            MessageHandler(filters.Regex("^/cancel$"), cancel_handler),
        ],
        name="development_process",
        persistent=False,
        allow_reentry=True,
    )
