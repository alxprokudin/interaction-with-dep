"""Поиск и добавление поставщиков."""
from __future__ import annotations

from loguru import logger

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.config import SUPERADMIN_IDS
from bot.keyboards.main import get_main_menu_keyboard
from bot.services.dadata import get_company_by_inn
from bot.services.database import get_user_company_info
from bot.services.google_sheets import google_sheets_service


# Состояния диалога
(
    SEARCH_INPUT,      # Ввод поискового запроса
    SEARCH_RESULTS,    # Показ результатов поиска
    ADD_SCENARIO,      # Выбор сценария добавления
    INPUT_INN,         # Ввод ИНН
    CONFIRM_COMPANY,   # Подтверждение данных из DaData
    INPUT_EMAIL,       # Ввод email
    INPUT_PHONE,       # Ввод телефона
    INPUT_CONTACT,     # Ввод ФИО менеджера
    INPUT_SUBJECT,     # Ввод предмета
    INPUT_LOCATIONS,   # Ввод точек
    CONFIRM_SAVE,      # Подтверждение сохранения
) = range(11)


def _get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура с кнопкой отмены."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("❌ Отмена")]],
        resize_keyboard=True,
    )


def _get_supplier_draft(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Получить черновик поставщика."""
    return context.user_data.get("supplier_draft", {})


def _save_supplier_draft(context: ContextTypes.DEFAULT_TYPE, data: dict) -> None:
    """Сохранить данные в черновик поставщика."""
    draft = _get_supplier_draft(context)
    draft.update(data)
    context.user_data["supplier_draft"] = draft
    logger.debug(f"Черновик поставщика сохранён: keys={list(draft.keys())}")


def _clear_supplier_draft(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Очистить черновик."""
    context.user_data.pop("supplier_draft", None)


async def start_supplier_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало поиска поставщика."""
    telegram_id = update.effective_user.id
    logger.info(f"start_supplier_search: user_id={telegram_id}")
    
    # Очищаем предыдущий черновик
    _clear_supplier_draft(context)
    
    # Получаем информацию о компании пользователя
    company_info = await get_user_company_info(telegram_id)
    
    if not company_info:
        await update.message.reply_text(
            "⚠️ Вы не состоите в компании.\n"
            "Присоединитесь к компании, чтобы использовать эту функцию.",
            reply_markup=get_main_menu_keyboard(telegram_id in SUPERADMIN_IDS),
        )
        return ConversationHandler.END
    
    sheet_id = company_info.sheet_id
    
    if not sheet_id:
        await update.message.reply_text(
            f"⚠️ Для компании «{company_info.company_name}» не настроена Google Таблица.\n\n"
            "Обратитесь к администратору для настройки интеграции:\n"
            "Админ-панель → Интеграции → Указать Sheet ID",
            reply_markup=get_main_menu_keyboard(telegram_id in SUPERADMIN_IDS),
        )
        return ConversationHandler.END
    
    if not company_info.sheet_verified:
        await update.message.reply_text(
            f"⚠️ Доступ к Google Таблице компании «{company_info.company_name}» не подтверждён.\n\n"
            "Обратитесь к администратору для проверки доступа.",
            reply_markup=get_main_menu_keyboard(telegram_id in SUPERADMIN_IDS),
        )
        return ConversationHandler.END
    
    _save_supplier_draft(context, {
        "sheet_id": sheet_id,
        "company_id": company_info.company_id,
        "company_name": company_info.company_name,
        "drive_folder_id": company_info.drive_folder_id,
    })
    
    await update.message.reply_text(
        f"🔍 *Поиск поставщика*\n"
        f"Компания: {company_info.company_name}\n\n"
        "Введите часть названия поставщика для поиска:",
        parse_mode="Markdown",
        reply_markup=_get_cancel_keyboard(),
    )
    return SEARCH_INPUT


async def process_search_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка поискового запроса."""
    query = update.message.text.strip()
    logger.info(f"process_search_query: query='{query}'")
    
    if len(query) < 2:
        await update.message.reply_text(
            "Введите минимум 2 символа для поиска:",
            reply_markup=_get_cancel_keyboard(),
        )
        return SEARCH_INPUT
    
    draft = _get_supplier_draft(context)
    sheet_id = draft.get("sheet_id")
    
    # Показываем индикатор загрузки
    loading_msg = await update.message.reply_text("🔄 Поиск...")
    
    # Ищем поставщиков
    suppliers = await google_sheets_service.search_suppliers(sheet_id, query)
    
    await loading_msg.delete()
    
    if suppliers:
        # Формируем кнопки с результатами
        buttons = []
        for supplier in suppliers[:8]:  # Максимум 8 результатов
            name = supplier.get("name", "Без названия")[:40]
            inn = supplier.get("inn", "")
            label = f"{name}" + (f" (ИНН: {inn})" if inn else "")
            buttons.append([InlineKeyboardButton(
                label[:60],
                callback_data=f"sup_sel:{supplier['row_number']}"
            )])
        
        buttons.append([InlineKeyboardButton("➕ Добавить нового поставщика", callback_data="sup_add_new")])
        buttons.append([InlineKeyboardButton("🔍 Искать снова", callback_data="sup_search_again")])
        
        await update.message.reply_text(
            f"📋 Найдено поставщиков: {len(suppliers)}\n\n"
            "Выберите из списка или добавьте нового:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        
        # Сохраняем результаты для возможного выбора
        _save_supplier_draft(context, {"search_results": suppliers, "search_query": query})
        return SEARCH_RESULTS
    else:
        # Ничего не найдено
        buttons = [
            [InlineKeyboardButton("➕ Добавить нового поставщика", callback_data="sup_add_new")],
            [InlineKeyboardButton("🔍 Искать снова", callback_data="sup_search_again")],
        ]
        await update.message.reply_text(
            f"🔍 По запросу «{query}» ничего не найдено.\n\n"
            "Хотите добавить нового поставщика?",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        _save_supplier_draft(context, {"search_query": query})
        return SEARCH_RESULTS


async def handle_search_result_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора из результатов поиска."""
    query = update.callback_query
    await query.answer()
    data = query.data
    logger.debug(f"handle_search_result_selection: data={data}")
    
    if data == "sup_search_again":
        await query.edit_message_text(
            "🔍 *Поиск поставщика*\n\n"
            "Введите часть названия поставщика для поиска:",
            parse_mode="Markdown",
        )
        return SEARCH_INPUT
    
    if data == "sup_add_new":
        # Переход к выбору сценария добавления
        buttons = [
            [InlineKeyboardButton("📝 Добавить для проработки", callback_data="add_for_work")],
            [InlineKeyboardButton("📧 Добавить и отправить на заведение", callback_data="add_and_send")],
            [InlineKeyboardButton("◀️ Назад к поиску", callback_data="sup_search_again")],
        ]
        await query.edit_message_text(
            "➕ *Добавление нового поставщика*\n\n"
            "Выберите сценарий:\n\n"
            "• *Добавить для проработки* — сохранить поставщика в реестр\n"
            "• *Добавить и отправить на заведение* — сохранить + отправить письмо менеджеру",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return ADD_SCENARIO
    
    if data.startswith("sup_sel:"):
        # Выбран существующий поставщик
        row_number = int(data.split(":")[1])
        draft = _get_supplier_draft(context)
        search_results = draft.get("search_results", [])
        
        selected = None
        for s in search_results:
            if s.get("row_number") == row_number:
                selected = s
                break
        
        if selected:
            _save_supplier_draft(context, {"selected_supplier": selected})
            
            # Сохраняем выбранного поставщика глобально для заведения продукта
            context.user_data["selected_supplier_for_product"] = {
                "name": selected.get("name", ""),
                "inn": selected.get("inn", ""),
                "kpp": selected.get("kpp", ""),
                "email": selected.get("email", ""),
                "phone": selected.get("phone", ""),
                "contact_name": selected.get("contact_name", ""),
            }
            
            # Показываем информацию о выбранном поставщике
            info = (
                f"✅ *Выбран поставщик:*\n\n"
                f"📌 {selected.get('name', '—')}\n"
                f"ИНН: {selected.get('inn', '—')}\n"
                f"КПП: {selected.get('kpp', '—')}\n"
                f"Email: {selected.get('email', '—')}\n"
                f"Телефон: {selected.get('phone', '—')}\n"
                f"Контакт: {selected.get('contact_name', '—')}\n"
            )
            
            is_superadmin = update.effective_user.id in SUPERADMIN_IDS
            await query.edit_message_text(info, parse_mode="Markdown")
            await query.message.reply_text(
                "Поставщик выбран. Теперь нажмите «📦 Заведение продукта на проработку».",
                reply_markup=get_main_menu_keyboard(is_superadmin),
            )
            return ConversationHandler.END
    
    return SEARCH_RESULTS


async def handle_add_scenario(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора сценария добавления."""
    query = update.callback_query
    await query.answer()
    data = query.data
    logger.debug(f"handle_add_scenario: data={data}")
    
    if data == "sup_search_again":
        await query.edit_message_text(
            "🔍 *Поиск поставщика*\n\n"
            "Введите часть названия поставщика для поиска:",
            parse_mode="Markdown",
        )
        return SEARCH_INPUT
    
    scenario = "work" if data == "add_for_work" else "send"
    _save_supplier_draft(context, {"scenario": scenario})
    
    await query.edit_message_text(
        "📝 *Шаг 1/6: ИНН*\n\n"
        "Введите ИНН организации (10 или 12 цифр):\n\n"
        "_Данные о компании будут получены автоматически из DaData._",
        parse_mode="Markdown",
    )
    return INPUT_INN


async def process_inn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода ИНН."""
    inn_raw = update.message.text.strip()
    inn = "".join(c for c in inn_raw if c.isdigit())
    logger.info(f"process_inn: inn={inn}")
    
    if len(inn) not in (10, 12):
        await update.message.reply_text(
            "❌ ИНН должен содержать 10 или 12 цифр.\n"
            "Попробуйте ещё раз:",
            reply_markup=_get_cancel_keyboard(),
        )
        return INPUT_INN
    
    # Показываем индикатор загрузки
    loading_msg = await update.message.reply_text("🔄 Проверяем ИНН в DaData...")
    
    # Запрашиваем данные из DaData
    company_info = await get_company_by_inn(inn)
    
    await loading_msg.delete()
    
    if company_info:
        # Сохраняем данные
        _save_supplier_draft(context, {
            "inn": company_info.inn,
            "kpp": company_info.kpp,
            "name": company_info.short_name or company_info.name,
            "full_name": company_info.name,
            "ogrn": company_info.ogrn,
            "address": company_info.address,
            "dadata_found": True,
        })
        
        # Показываем найденные данные
        status_emoji = "🟢" if company_info.status == "ACTIVE" else "🟡"
        info = (
            f"✅ *Компания найдена в DaData:*\n\n"
            f"📌 {company_info.short_name or company_info.name}\n"
            f"ИНН: `{company_info.inn}`\n"
            f"КПП: `{company_info.kpp}`\n"
        )
        if company_info.ogrn:
            info += f"ОГРН: `{company_info.ogrn}`\n"
        if company_info.address:
            info += f"Адрес: {company_info.address[:100]}...\n" if len(company_info.address) > 100 else f"Адрес: {company_info.address}\n"
        info += f"\nСтатус: {status_emoji} {company_info.status or 'неизвестен'}"
        
        buttons = [
            [InlineKeyboardButton("✅ Подтвердить и продолжить", callback_data="confirm_company")],
            [InlineKeyboardButton("✏️ Ввести ИНН заново", callback_data="retry_inn")],
        ]
        await update.message.reply_text(
            info,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return CONFIRM_COMPANY
    else:
        # Компания не найдена — ручной ввод
        _save_supplier_draft(context, {
            "inn": inn,
            "kpp": "-",
            "dadata_found": False,
        })
        
        await update.message.reply_text(
            f"⚠️ Компания с ИНН `{inn}` не найдена в DaData.\n\n"
            "Введите наименование организации вручную:",
            parse_mode="Markdown",
            reply_markup=_get_cancel_keyboard(),
        )
        return INPUT_EMAIL  # Пропускаем подтверждение, сразу к вводу названия
        # TODO: добавить шаг ввода названия


async def handle_company_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Подтверждение данных компании."""
    query = update.callback_query
    await query.answer()
    data = query.data
    logger.debug(f"handle_company_confirm: data={data}")
    
    if data == "retry_inn":
        await query.edit_message_text(
            "📝 *Шаг 1/6: ИНН*\n\n"
            "Введите ИНН организации (10 или 12 цифр):",
            parse_mode="Markdown",
        )
        return INPUT_INN
    
    if data == "confirm_company":
        await query.edit_message_text(
            "📝 *Шаг 2/6: Email*\n\n"
            "Введите email менеджера поставщика:",
            parse_mode="Markdown",
        )
        return INPUT_EMAIL
    
    return CONFIRM_COMPANY


async def process_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода email."""
    email = update.message.text.strip()
    logger.debug(f"process_email: email={email}")
    
    # Простая валидация email
    if "@" not in email or "." not in email:
        await update.message.reply_text(
            "❌ Неверный формат email. Попробуйте ещё раз:",
            reply_markup=_get_cancel_keyboard(),
        )
        return INPUT_EMAIL
    
    _save_supplier_draft(context, {"email": email})
    
    await update.message.reply_text(
        "📝 *Шаг 3/6: Телефон*\n\n"
        "Введите телефон менеджера:",
        parse_mode="Markdown",
        reply_markup=_get_cancel_keyboard(),
    )
    return INPUT_PHONE


async def process_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода телефона."""
    phone = update.message.text.strip()
    logger.debug(f"process_phone: phone={phone}")
    
    _save_supplier_draft(context, {"phone": phone})
    
    await update.message.reply_text(
        "📝 *Шаг 4/6: ФИО менеджера*\n\n"
        "Введите ФИО контактного лица:",
        parse_mode="Markdown",
        reply_markup=_get_cancel_keyboard(),
    )
    return INPUT_CONTACT


async def process_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода ФИО контакта."""
    contact = update.message.text.strip()
    logger.debug(f"process_contact: contact={contact}")
    
    _save_supplier_draft(context, {"contact_name": contact})
    
    await update.message.reply_text(
        "📝 *Шаг 5/6: Предмет*\n\n"
        "Введите предмет поставки (категория товаров):",
        parse_mode="Markdown",
        reply_markup=_get_cancel_keyboard(),
    )
    return INPUT_SUBJECT


async def process_subject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода предмета."""
    subject = update.message.text.strip()
    logger.debug(f"process_subject: subject={subject}")
    
    _save_supplier_draft(context, {"subject": subject})
    
    await update.message.reply_text(
        "📝 *Шаг 6/6: Точки*\n\n"
        "Введите точки (города/регионы поставки):",
        parse_mode="Markdown",
        reply_markup=_get_cancel_keyboard(),
    )
    return INPUT_LOCATIONS


async def process_locations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода точек и сохранение поставщика."""
    locations = update.message.text.strip()
    logger.debug(f"process_locations: locations={locations}")
    
    draft = _get_supplier_draft(context)
    _save_supplier_draft(context, {"locations": locations})
    draft = _get_supplier_draft(context)  # Обновлённый
    
    # Добавляем ответственного (текущий пользователь)
    username = update.effective_user.username or update.effective_user.full_name or str(update.effective_user.id)
    draft["responsible"] = f"@{username}" if update.effective_user.username else username
    
    # Показываем итоговую информацию
    scenario = draft.get("scenario", "work")
    scenario_text = "📝 Добавить для проработки" if scenario == "work" else "📧 Добавить и отправить на заведение"
    
    summary = (
        f"✅ *Проверьте данные поставщика:*\n\n"
        f"📌 {draft.get('name', '—')}\n"
        f"ИНН: `{draft.get('inn', '—')}`\n"
        f"КПП: `{draft.get('kpp', '—')}`\n"
        f"Email: {draft.get('email', '—')}\n"
        f"Телефон: {draft.get('phone', '—')}\n"
        f"Контакт: {draft.get('contact_name', '—')}\n"
        f"Предмет: {draft.get('subject', '—')}\n"
        f"Точки: {draft.get('locations', '—')}\n"
        f"Ответственный: {draft.get('responsible', '—')}\n\n"
        f"Сценарий: {scenario_text}"
    )
    
    buttons = [
        [InlineKeyboardButton("✅ Сохранить", callback_data="save_supplier")],
        [InlineKeyboardButton("❌ Отменить", callback_data="cancel_supplier")],
    ]
    
    await update.message.reply_text(
        summary,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return CONFIRM_SAVE


async def handle_save_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Подтверждение сохранения поставщика."""
    query = update.callback_query
    await query.answer()
    data = query.data
    logger.debug(f"handle_save_confirm: data={data}")
    
    is_superadmin = update.effective_user.id in SUPERADMIN_IDS
    
    if data == "cancel_supplier":
        _clear_supplier_draft(context)
        await query.edit_message_text("❌ Добавление поставщика отменено.")
        await query.message.reply_text(
            "Выберите действие:",
            reply_markup=get_main_menu_keyboard(is_superadmin),
        )
        return ConversationHandler.END
    
    if data == "save_supplier":
        draft = _get_supplier_draft(context)
        sheet_id = draft.get("sheet_id")
        scenario = draft.get("scenario", "work")
        
        # Сохраняем в Google Sheets
        loading_msg = await query.message.reply_text("🔄 Сохраняем поставщика...")
        
        success = await google_sheets_service.add_supplier(sheet_id, draft)
        
        await loading_msg.delete()
        
        if success:
            if scenario == "send":
                # TODO: отправить письмо через Gmail API
                await query.edit_message_text(
                    "✅ *Поставщик сохранён!*\n\n"
                    "📧 Письмо на заведение будет отправлено менеджеру.\n"
                    "_(Функция отправки email будет добавлена позже)_",
                    parse_mode="Markdown",
                )
            else:
                await query.edit_message_text(
                    "✅ *Поставщик успешно сохранён в реестр!*",
                    parse_mode="Markdown",
                )
        else:
            await query.edit_message_text(
                "❌ Ошибка сохранения поставщика. Попробуйте позже.",
            )
        
        _clear_supplier_draft(context)
        await query.message.reply_text(
            "Выберите действие:",
            reply_markup=get_main_menu_keyboard(is_superadmin),
        )
        return ConversationHandler.END
    
    return CONFIRM_SAVE


async def cancel_supplier_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена поиска/добавления поставщика."""
    logger.info(f"cancel_supplier_search: user_id={update.effective_user.id}")
    _clear_supplier_draft(context)
    
    is_superadmin = update.effective_user.id in SUPERADMIN_IDS
    await update.message.reply_text(
        "❌ Поиск поставщика отменён.",
        reply_markup=get_main_menu_keyboard(is_superadmin),
    )
    return ConversationHandler.END


def get_supplier_search_handler() -> ConversationHandler:
    """Собрать ConversationHandler для поиска поставщиков."""
    return ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^🔍 Поиск поставщика$"),
                start_supplier_search,
            ),
        ],
        states={
            SEARCH_INPUT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    process_search_query,
                ),
            ],
            SEARCH_RESULTS: [
                CallbackQueryHandler(handle_search_result_selection, pattern="^sup_"),
            ],
            ADD_SCENARIO: [
                CallbackQueryHandler(handle_add_scenario, pattern="^(add_for_work|add_and_send|sup_search_again)$"),
            ],
            INPUT_INN: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    process_inn,
                ),
            ],
            CONFIRM_COMPANY: [
                CallbackQueryHandler(handle_company_confirm, pattern="^(confirm_company|retry_inn)$"),
            ],
            INPUT_EMAIL: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    process_email,
                ),
            ],
            INPUT_PHONE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    process_phone,
                ),
            ],
            INPUT_CONTACT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    process_contact,
                ),
            ],
            INPUT_SUBJECT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    process_subject,
                ),
            ],
            INPUT_LOCATIONS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    process_locations,
                ),
            ],
            CONFIRM_SAVE: [
                CallbackQueryHandler(handle_save_confirm, pattern="^(save_supplier|cancel_supplier)$"),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^❌ Отмена$"), cancel_supplier_search),
            MessageHandler(filters.Regex("^/cancel$"), cancel_supplier_search),
        ],
        name="supplier_search",
    )
