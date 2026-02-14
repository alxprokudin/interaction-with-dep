"""Добавление поставщика без привязки к заявке на проработку."""
from __future__ import annotations

import tempfile
from pathlib import Path

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
from bot.keyboards.product_registration import get_upload_keyboard, get_cancel_keyboard
from bot.config import SUPERADMIN_IDS
from bot.services.database import get_user_company_info
from bot.services.google_sheets import google_sheets_service
from bot.services.dadata import get_company_by_inn
from bot.services.google_drive import (
    create_supplier_folder,
    upload_supplier_card,
    get_file_link,
)
from bot.services.email_service import (
    SupplierData,
    send_supplier_registration_emails,
)


# Состояния диалога
(
    SA_SCENARIO,        # Выбор сценария (проработка/заведение)
    SA_INN,             # Ввод ИНН
    SA_CONFIRM,         # Подтверждение данных DaData
    SA_EMAIL,           # Ввод email
    SA_PHONE,           # Ввод телефона
    SA_CONTACT,         # Ввод ФИО
    SA_SUBJECT,         # Ввод предмета
    SA_LOCATIONS,       # Ввод точек
    SA_CARD,            # Загрузка карточки (для заведения)
) = range(9)


async def start_supplier_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало добавления поставщика."""
    telegram_id = update.effective_user.id
    logger.info(f"start_supplier_add called: user_id={telegram_id}")
    
    # Получаем информацию о компании пользователя
    company_info = await get_user_company_info(telegram_id)
    
    if not company_info:
        is_superadmin = telegram_id in SUPERADMIN_IDS
        await update.message.reply_text(
            "⚠️ Вы не состоите в компании.\n"
            "Присоединитесь к компании, чтобы использовать эту функцию.",
            reply_markup=get_main_menu_keyboard(is_superadmin),
        )
        return ConversationHandler.END
    
    sheet_id = company_info.sheet_id
    
    if not sheet_id or not company_info.sheet_verified:
        is_superadmin = telegram_id in SUPERADMIN_IDS
        await update.message.reply_text(
            f"⚠️ Для компании «{company_info.company_name}» не настроена Google Таблица.\n\n"
            "Обратитесь к администратору.",
            reply_markup=get_main_menu_keyboard(is_superadmin),
        )
        return ConversationHandler.END
    
    # Сохраняем данные компании в контекст
    context.user_data["supplier_add_company_info"] = {
        "company_id": company_info.company_id,
        "company_name": company_info.company_name,
        "sheet_id": company_info.sheet_id,
        "drive_folder_id": company_info.drive_folder_id,
    }
    
    # Показываем выбор сценария
    keyboard = [
        [InlineKeyboardButton("📝 Добавить для проработки", callback_data="sa_scenario:prorabotka")],
        [InlineKeyboardButton("📧 Добавить и отправить на заведение", callback_data="sa_scenario:zavedenie")],
        [InlineKeyboardButton("❌ Отмена", callback_data="sa_scenario:cancel")],
    ]
    await update.message.reply_text(
        "➕ *Добавление нового поставщика*\n\n"
        "Выберите сценарий:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SA_SCENARIO


async def scenario_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора сценария."""
    query = update.callback_query
    await query.answer()
    data = query.data
    logger.debug(f"scenario_selected: data={data}")
    
    if data == "sa_scenario:cancel":
        is_superadmin = update.effective_user.id in SUPERADMIN_IDS
        await query.edit_message_text("❌ Добавление поставщика отменено.")
        await query.message.reply_text(
            "Главное меню:",
            reply_markup=get_main_menu_keyboard(is_superadmin),
        )
        return ConversationHandler.END
    
    scenario = "prorabotka" if data == "sa_scenario:prorabotka" else "zavedenie"
    context.user_data["supplier_add_scenario"] = scenario
    
    scenario_text = "для проработки" if scenario == "prorabotka" else "и отправить на заведение"
    
    await query.edit_message_text(
        f"➕ *Добавление поставщика* ({scenario_text})",
        parse_mode="Markdown",
    )
    
    await query.message.reply_text(
        "Введите ИНН организации (10 или 12 цифр):",
        reply_markup=get_cancel_keyboard(),
    )
    return SA_INN


async def inn_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен ИНН — ищем данные в DaData."""
    inn = update.message.text.strip()
    logger.info(f"inn_received: inn={inn}")
    
    if not inn.isdigit() or len(inn) not in (10, 12):
        await update.message.reply_text(
            "❌ ИНН должен содержать 10 или 12 цифр.\nПопробуйте ещё раз:",
            reply_markup=get_cancel_keyboard(),
        )
        return SA_INN
    
    await update.message.reply_text(
        "🔍 Ищу данные по ИНН...",
        reply_markup=get_cancel_keyboard(),
    )
    
    company_data = await get_company_by_inn(inn)
    
    if not company_data:
        await update.message.reply_text(
            f"❌ Организация с ИНН {inn} не найдена в реестре.\n"
            "Проверьте ИНН и попробуйте ещё раз:",
            reply_markup=get_cancel_keyboard(),
        )
        return SA_INN
    
    context.user_data["new_supplier_dadata"] = company_data
    
    name = company_data.short_name or company_data.name or "—"
    kpp = company_data.kpp or "—"
    address = company_data.address or "—"
    
    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data="sa_confirm:yes")],
        [InlineKeyboardButton("🔄 Ввести другой ИНН", callback_data="sa_confirm:retry")],
        [InlineKeyboardButton("❌ Отмена", callback_data="sa_confirm:cancel")],
    ]
    
    await update.message.reply_text(
        f"✅ *Данные подтверждены*\n\n"
        f"*Название:* {name}\n"
        f"*ИНН:* {inn}\n"
        f"*КПП:* {kpp}\n"
        f"*Адрес:* {address}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SA_CONFIRM


async def confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Подтверждение данных DaData."""
    query = update.callback_query
    await query.answer()
    data = query.data
    logger.debug(f"confirm_handler: data={data}")
    
    if data == "sa_confirm:cancel":
        is_superadmin = update.effective_user.id in SUPERADMIN_IDS
        await query.edit_message_text("❌ Добавление поставщика отменено.")
        await query.message.reply_text(
            "Главное меню:",
            reply_markup=get_main_menu_keyboard(is_superadmin),
        )
        return ConversationHandler.END
    
    if data == "sa_confirm:retry":
        await query.edit_message_text(
            "➕ *Добавление поставщика*\n\n"
            "Введите ИНН организации (10 или 12 цифр):",
            parse_mode="Markdown",
        )
        return SA_INN
    
    # Подтверждено
    await query.edit_message_text("✅ Данные подтверждены.\n\nВведите Email поставщика:")
    return SA_EMAIL


async def email_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен email."""
    email = update.message.text.strip()
    logger.debug(f"email_received: email={email}")
    
    if "@" not in email or "." not in email:
        await update.message.reply_text(
            "❌ Неверный формат email. Попробуйте ещё раз:",
            reply_markup=get_cancel_keyboard(),
        )
        return SA_EMAIL
    
    context.user_data["new_supplier_email"] = email
    await update.message.reply_text(
        "Введите телефон поставщика:",
        reply_markup=get_cancel_keyboard(),
    )
    return SA_PHONE


async def phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен телефон."""
    phone = update.message.text.strip()
    logger.debug(f"phone_received: phone={phone}")
    context.user_data["new_supplier_phone"] = phone
    await update.message.reply_text(
        "Введите ФИО контактного лица:",
        reply_markup=get_cancel_keyboard(),
    )
    return SA_CONTACT


async def contact_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получено ФИО."""
    contact = update.message.text.strip()
    logger.debug(f"contact_received: contact={contact}")
    context.user_data["new_supplier_contact"] = contact
    await update.message.reply_text(
        "Введите предмет (категорию товаров):",
        reply_markup=get_cancel_keyboard(),
    )
    return SA_SUBJECT


async def subject_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен предмет."""
    subject = update.message.text.strip()
    logger.debug(f"subject_received: subject={subject}")
    context.user_data["new_supplier_subject"] = subject
    await update.message.reply_text(
        "Введите точки (локации поставки):",
        reply_markup=get_cancel_keyboard(),
    )
    return SA_LOCATIONS


async def locations_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получены точки — сохраняем поставщика."""
    text = update.message.text.strip()
    logger.info(f"locations_received: locations={text}")
    
    # Собираем данные поставщика
    dadata = context.user_data.get("new_supplier_dadata")
    company_info = context.user_data.get("supplier_add_company_info", {})
    
    supplier_data = {
        "inn": dadata.inn if dadata else "",
        "kpp": dadata.kpp if dadata else "-",
        "name": (dadata.short_name or dadata.name) if dadata else "",
        "email": context.user_data.get("new_supplier_email", ""),
        "phone": context.user_data.get("new_supplier_phone", ""),
        "contact_name": context.user_data.get("new_supplier_contact", ""),
        "subject": context.user_data.get("new_supplier_subject", ""),
        "locations": text,
        "responsible": update.effective_user.username or str(update.effective_user.id),
        "telegram_user_id": update.effective_user.id,  # Для отслеживания ответов
        "folder_link": "",  # Будет заполнено после создания папки
        "card_link": "",    # Будет заполнено после загрузки карточки
    }
    
    context.user_data["current_supplier_data"] = supplier_data
    
    # Примечание: НЕ сохраняем в Google Sheets здесь для сценария "заведение",
    # т.к. нужно дождаться создания папки и загрузки карточки
    scenario = context.user_data.get("supplier_add_scenario", "prorabotka")
    
    # Сохраняем в Google Sheets только для "проработки" (без папки/карточки)
    sheet_id = company_info.get("sheet_id")
    if sheet_id and scenario == "prorabotka":
        success = await google_sheets_service.add_supplier(sheet_id, supplier_data)
        if success:
            logger.info(f"Поставщик добавлен в таблицу: {supplier_data['name']}")
        else:
            logger.error("Ошибка добавления поставщика в таблицу")
    
    # Проверяем сценарий
    scenario = context.user_data.get("supplier_add_scenario", "prorabotka")
    
    if scenario == "zavedenie":
        # Переходим к загрузке карточки поставщика
        await update.message.reply_text(
            f"✅ Данные поставщика *{supplier_data['name']}* сохранены!\n\n"
            "📎 Теперь загрузите *карточку поставщика* (PDF, Word, Excel или изображение).\n"
            "Это необходимо для отправки на заведение.",
            parse_mode="Markdown",
            reply_markup=get_cancel_keyboard(),
        )
        return SA_CARD
    
    # Сценарий "для проработки" — завершаем
    is_superadmin = update.effective_user.id in SUPERADMIN_IDS
    await update.message.reply_text(
        f"✅ Поставщик *{supplier_data['name']}* успешно добавлен!",
        parse_mode="Markdown",
        reply_markup=get_main_menu_keyboard(is_superadmin),
    )
    _cleanup_context(context)
    return ConversationHandler.END


async def card_uploaded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка загрузки карточки поставщика."""
    logger.info("card_uploaded called")
    
    company_info = context.user_data.get("supplier_add_company_info", {})
    supplier_data = context.user_data.get("current_supplier_data", {})
    drive_folder_id = company_info.get("drive_folder_id")
    
    # Определяем тип загрузки
    if update.message.document:
        file = await update.message.document.get_file()
        filename = update.message.document.file_name
        mime_type = update.message.document.mime_type or "application/octet-stream"
    elif update.message.photo:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        filename = f"card_{supplier_data.get('inn', 'unknown')}.jpg"
        mime_type = "image/jpeg"
    else:
        await update.message.reply_text(
            "❌ Пожалуйста, отправьте файл (PDF, Word, Excel) или фото карточки поставщика.",
            reply_markup=get_cancel_keyboard(),
        )
        return SA_CARD
    
    # Скачиваем файл
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix) as tmp:
        await file.download_to_drive(tmp.name)
        tmp_path = Path(tmp.name)
    
    logger.debug(f"Карточка скачана: {tmp_path}, size={tmp_path.stat().st_size}")
    
    # Загружаем в Google Drive
    folder_link = ""
    card_link = ""
    
    if drive_folder_id:
        supplier_name = supplier_data.get("name", "Неизвестный")
        supplier_folder_id = create_supplier_folder(supplier_name, drive_folder_id)
        
        if supplier_folder_id:
            # Сохраняем ссылку на папку
            from bot.services.google_drive import get_folder_link
            folder_link = get_folder_link(supplier_folder_id)
            supplier_data["folder_link"] = folder_link
            context.user_data["supplier_folder_link"] = folder_link
            logger.info(f"Создана папка поставщика: {folder_link}")
            
            # Загружаем карточку
            card_file_id = upload_supplier_card(tmp_path, supplier_folder_id, filename, mime_type)
            if card_file_id:
                card_link = get_file_link(card_file_id)
                supplier_data["card_link"] = card_link
                context.user_data["supplier_card_link"] = card_link
                logger.info(f"Карточка загружена в Drive: {card_link}")
    
    # Обновляем supplier_data в контексте
    context.user_data["current_supplier_data"] = supplier_data
    
    # Сохраняем в Google Sheets с ссылками
    sheet_id = company_info.get("sheet_id")
    if sheet_id:
        success = await google_sheets_service.add_supplier(sheet_id, supplier_data)
        if success:
            logger.info(f"Поставщик добавлен в таблицу с ссылками: {supplier_data['name']}")
        else:
            logger.error("Ошибка добавления поставщика в таблицу")
    
    await update.message.reply_text(
        "📎 Карточка поставщика получена!\n\n"
        "Отправляю письма на заведение...",
        parse_mode="Markdown",
    )
    
    # Отправляем 4 письма
    await _send_registration_emails(update, context, tmp_path if tmp_path.exists() else None)
    
    # Завершаем
    is_superadmin = update.effective_user.id in SUPERADMIN_IDS
    await update.message.reply_text(
        f"✅ Поставщик *{supplier_data.get('name', '')}* добавлен и отправлен на заведение!",
        parse_mode="Markdown",
        reply_markup=get_main_menu_keyboard(is_superadmin),
    )
    _cleanup_context(context)
    return ConversationHandler.END


async def _send_registration_emails(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    card_path: Path = None,
) -> None:
    """Отправить 4 письма для заведения поставщика."""
    supplier_data = context.user_data.get("current_supplier_data", {})
    company_info = context.user_data.get("supplier_add_company_info", {})
    
    supplier = SupplierData(
        name=supplier_data.get("name", ""),
        inn=supplier_data.get("inn", ""),
        kpp=supplier_data.get("kpp", "-"),
        contact_name=supplier_data.get("contact_name", ""),
        contact_phone=supplier_data.get("phone", ""),
        contact_email=supplier_data.get("email", ""),
        delivery_points=supplier_data.get("locations", ""),
    )
    
    logger.info(f"Отправка писем для поставщика: {supplier.name}")
    
    results = await send_supplier_registration_emails(
        supplier=supplier,
        card_path=card_path,
        telegram_user_id=update.effective_user.id,
        company_id=company_info.get("company_id"),
        sheet_id=company_info.get("sheet_id"),
    )
    
    # Формируем отчёт
    sent_count = sum(1 for v in results.values() if v)
    total = len(results)
    
    status_lines = []
    status_lines.append(f"1️⃣ СБ (Ol.Pak): {'✅' if results.get('email_1_sb') else '❌'}")
    status_lines.append(f"2️⃣ DocsInBox: {'✅' if results.get('email_2_docsinbox') else '❌'}")
    status_lines.append(f"3️⃣ Роуминг (Контур): {'✅' if results.get('email_3_roaming') else '❌'}")
    status_lines.append(f"4️⃣ Документы поставщику: {'✅' if results.get('email_4_documents') else '❌'}")
    
    status_text = "\n".join(status_lines)
    
    await update.message.reply_text(
        f"📧 *Отправка на заведение: {sent_count}/{total}*\n\n"
        f"{status_text}",
        parse_mode="Markdown",
    )


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена добавления поставщика."""
    logger.info(f"cancel_handler: user_id={update.effective_user.id}")
    _cleanup_context(context)
    is_superadmin = update.effective_user.id in SUPERADMIN_IDS
    await update.message.reply_text(
        "❌ Добавление поставщика отменено.",
        reply_markup=get_main_menu_keyboard(is_superadmin),
    )
    return ConversationHandler.END


def _cleanup_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Очистить данные из контекста."""
    keys_to_remove = [
        "supplier_add_company_info",
        "supplier_add_scenario",
        "new_supplier_dadata",
        "new_supplier_email",
        "new_supplier_phone",
        "new_supplier_contact",
        "new_supplier_subject",
        "current_supplier_data",
        "supplier_card_link",
    ]
    for key in keys_to_remove:
        context.user_data.pop(key, None)


def get_supplier_add_handler() -> ConversationHandler:
    """Собрать ConversationHandler для добавления поставщика."""
    return ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^➕ Добавить поставщика$"),
                start_supplier_add,
            ),
        ],
        states={
            SA_SCENARIO: [
                CallbackQueryHandler(scenario_selected, pattern="^sa_scenario:"),
            ],
            SA_INN: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    inn_received,
                ),
            ],
            SA_CONFIRM: [
                CallbackQueryHandler(confirm_handler, pattern="^sa_confirm:"),
            ],
            SA_EMAIL: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    email_received,
                ),
            ],
            SA_PHONE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    phone_received,
                ),
            ],
            SA_CONTACT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    contact_received,
                ),
            ],
            SA_SUBJECT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    subject_received,
                ),
            ],
            SA_LOCATIONS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    locations_received,
                ),
            ],
            SA_CARD: [
                MessageHandler(
                    (filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND,
                    card_uploaded,
                ),
                MessageHandler(
                    filters.Regex("^❌ Отмена$"),
                    cancel_handler,
                ),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^/cancel$"), cancel_handler),
            MessageHandler(filters.Regex("^❌ Отмена$"), cancel_handler),
        ],
        name="supplier_add",
    )
