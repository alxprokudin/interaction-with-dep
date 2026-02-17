"""Процесс заведения продукта на проработку."""
import tempfile
from pathlib import Path

from loguru import logger

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.keyboards.product_registration import (
    get_unit_keyboard,
    get_upload_keyboard,
    get_upload_keyboard_minimal,
    get_finish_upload_inline_keyboard,
    get_cancel_keyboard,
)
from bot.keyboards.main import get_main_menu_keyboard
from bot.config import SUPERADMIN_IDS
from bot.services.database import get_user_company_info
from bot.services.google_sheets import google_sheets_service
from bot.services.dadata import get_company_by_inn
from bot.services.google_drive import (
    create_supplier_folder,
    upload_supplier_card,
    get_file_link,
    get_folder_link,
)
from bot.services.email_service import (
    SupplierData,
    send_supplier_registration_emails,
)

# Состояния диалога
(
    SUPPLIER,           # Выбор поставщика из списка
    SUPPLIER_ADD_SCENARIO,  # Выбор сценария добавления
    SUPPLIER_INN,       # Ввод ИНН
    SUPPLIER_CONFIRM,   # Подтверждение данных DaData
    SUPPLIER_EMAIL,     # Ввод email
    SUPPLIER_PHONE,     # Ввод телефона
    SUPPLIER_CONTACT,   # Ввод ФИО
    SUPPLIER_SUBJECT,   # Ввод предмета
    SUPPLIER_LOCATIONS, # Ввод точек
    SUPPLIER_CARD,      # Загрузка карточки поставщика (для сценария zavedenie)
    UNIT,               # Выбор единицы измерения
    PRICE,              # Ввод цены без НДС
    CERTS,              # Загрузка сертификатов
    PHOTOS_PRODUCT,     # Загрузка фото продукта
    PHOTOS_LABEL,       # Загрузка фото этикетки
    NOMENCLATURE_CONFIRM,  # Подтверждение названия из Vision/GPT
    NOMENCLATURE_MANUAL,   # Ручной ввод названия
    REQUEST_TYPE,       # Выбор типа заявки (срочная/регулярная)
) = range(18)


def _get_draft_key(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Ключ для хранения черновика в user_data."""
    # company_id может быть в product_company_info или напрямую
    company_info = context.user_data.get("product_company_info", {})
    company_id = company_info.get("company_id") or context.user_data.get("company_id", 0)
    return f"product_draft_{company_id}"


def _get_draft(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Получить черновик продукта."""
    return context.user_data.get(_get_draft_key(context), {})


def _save_draft(context: ContextTypes.DEFAULT_TYPE, data: dict) -> None:
    """Сохранить черновик."""
    key = _get_draft_key(context)
    context.user_data[key] = {**_get_draft(context), **data}
    logger.debug(f"Черновик сохранён: keys={list(context.user_data[key].keys())}")


# Константы пагинации
SUPPLIERS_PER_PAGE = 10


def _build_suppliers_keyboard(
    suppliers: list,
    page: int,
    company_name: str,
) -> tuple[list, str]:
    """
    Построить клавиатуру поставщиков с пагинацией.
    
    Args:
        suppliers: Полный список поставщиков
        page: Номер страницы (0-based)
        company_name: Название компании для заголовка
        
    Returns:
        (keyboard, text) — клавиатура и текст сообщения
    """
    total = len(suppliers)
    total_pages = (total + SUPPLIERS_PER_PAGE - 1) // SUPPLIERS_PER_PAGE if total > 0 else 1
    
    # Ограничиваем страницу
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * SUPPLIERS_PER_PAGE
    end_idx = min(start_idx + SUPPLIERS_PER_PAGE, total)
    page_suppliers = suppliers[start_idx:end_idx]
    
    keyboard = []
    
    # Кнопки поставщиков
    for idx, row in enumerate(page_suppliers):
        if len(row) > 3 and row[3]:  # Колонка D — Наименование
            name = row[3][:40]
            # Глобальный индекс в списке
            global_idx = start_idx + idx
            keyboard.append([InlineKeyboardButton(name, callback_data=f"sup_sel:{global_idx}")])
    
    # Кнопки пагинации
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"sup_page:{page - 1}"))
        nav_buttons.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="sup_page:noop"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("➡️ Вперёд", callback_data=f"sup_page:{page + 1}"))
        keyboard.append(nav_buttons)
    
    # Кнопка добавления нового
    keyboard.append([InlineKeyboardButton("➕ Добавить нового поставщика", callback_data="sup_add_new")])
    
    # Текст сообщения
    if total == 0:
        text = (
            f"📦 *Заведение продукта на проработку*\n"
            f"Компания: {company_name}\n\n"
            "📋 Список поставщиков пуст.\n"
            "Добавьте нового поставщика:"
        )
    else:
        text = (
            f"📦 *Заведение продукта на проработку*\n"
            f"Компания: {company_name}\n\n"
            f"Выберите поставщика из списка ({total} шт):"
        )
    
    return keyboard, text


async def start_product_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало заведения продукта — показ списка поставщиков из Google Sheets."""
    telegram_id = update.effective_user.id
    logger.info(f"start_product_registration called: user_id={telegram_id}")
    
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
    
    # Сохраняем данные компании в контекст (ДО очистки черновика!)
    context.user_data["product_company_info"] = {
        "company_id": company_info.company_id,
        "company_name": company_info.company_name,
        "sheet_id": sheet_id,
        "drive_folder_id": company_info.drive_folder_id,
    }
    
    # Очищаем черновик (теперь company_id доступен для формирования ключа)
    _save_draft(context, {})
    
    # Загружаем поставщиков из Google Sheets
    suppliers = await google_sheets_service.get_all_rows(
        sheet_id, 
        worksheet_name="Реестр_Поставщики",
        skip_header=True,
    )
    
    # Сохраняем ВЕСЬ список для пагинации
    context.user_data["suppliers_list"] = suppliers
    context.user_data["suppliers_page"] = 0
    
    # Формируем клавиатуру с пагинацией
    keyboard, text = _build_suppliers_keyboard(suppliers, 0, company_info.company_name)
    
    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SUPPLIER


async def supplier_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора поставщика из списка."""
    query = update.callback_query
    await query.answer()
    data = query.data
    logger.debug(f"supplier_selected: data={data}")
    
    # Пагинация
    if data.startswith("sup_page:"):
        page_str = data.split(":")[1]
        if page_str == "noop":
            # Нажали на номер страницы — ничего не делаем
            return SUPPLIER
        
        page = int(page_str)
        suppliers = context.user_data.get("suppliers_list", [])
        company_info = context.user_data.get("product_company_info", {})
        company_name = company_info.get("company_name", "")
        
        context.user_data["suppliers_page"] = page
        keyboard, text = _build_suppliers_keyboard(suppliers, page, company_name)
        
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return SUPPLIER
    
    if data == "sup_add_new":
        # Переход к добавлению нового поставщика
        keyboard = [
            [InlineKeyboardButton("📝 Добавить для проработки", callback_data="sup_scenario:prorabotka")],
            [InlineKeyboardButton("📧 Добавить и отправить на заведение", callback_data="sup_scenario:zavedenie")],
            [InlineKeyboardButton("❌ Отмена", callback_data="sup_scenario:cancel")],
        ]
        await query.edit_message_text(
            "➕ *Добавление нового поставщика*\n\n"
            "Выберите сценарий:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return SUPPLIER_ADD_SCENARIO
    
    # Выбор существующего поставщика
    if data.startswith("sup_sel:"):
        idx = int(data.split(":")[1])
        suppliers_list = context.user_data.get("suppliers_list", [])
        
        if idx < len(suppliers_list):
            row = suppliers_list[idx]
            # Структура: Дата, ИНН, КПП, Наименование, Email, Телефон, ФИО, Предмет, Точки, Ответственный
            supplier_data = {
                "supplier_name": row[3] if len(row) > 3 else "",
                "supplier_inn": row[1] if len(row) > 1 else "",
                "supplier_kpp": row[2] if len(row) > 2 else "",
                "supplier_email": row[4] if len(row) > 4 else "",
                "supplier_phone": row[5] if len(row) > 5 else "",
                "supplier_contact": row[6] if len(row) > 6 else "",
            }
            _save_draft(context, supplier_data)
            
            await query.edit_message_text(
                f"✅ Поставщик выбран: *{supplier_data['supplier_name']}*",
                parse_mode="Markdown",
            )
            await query.message.reply_text(
                "Выберите единицу измерения:",
                reply_markup=get_unit_keyboard(),
            )
            return UNIT
    
    return SUPPLIER


async def supplier_add_scenario(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора сценария добавления поставщика."""
    query = update.callback_query
    await query.answer()
    data = query.data
    logger.debug(f"supplier_add_scenario: data={data}")
    
    if data == "sup_scenario:cancel":
        is_superadmin = update.effective_user.id in SUPERADMIN_IDS
        await query.edit_message_text("❌ Заведение продукта отменено.")
        await query.message.reply_text(
            "Вы вернулись в главное меню.",
            reply_markup=get_main_menu_keyboard(is_superadmin),
        )
        return ConversationHandler.END
    
    scenario = "prorabotka" if data == "sup_scenario:prorabotka" else "zavedenie"
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
    return SUPPLIER_INN


async def supplier_inn_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен ИНН — запрашиваем данные из DaData."""
    inn = update.message.text.strip()
    logger.info(f"supplier_inn_received: inn={inn}")
    
    # Очищаем ИНН
    inn_clean = "".join(c for c in inn if c.isdigit())
    
    if len(inn_clean) not in (10, 12):
        await update.message.reply_text(
            "⚠️ ИНН должен содержать 10 или 12 цифр.\n"
            "Попробуйте снова:",
            reply_markup=get_cancel_keyboard(),
        )
        return SUPPLIER_INN
    
    # Запрос в DaData
    await update.message.reply_text(
        "🔍 Ищу данные по ИНН...",
        reply_markup=get_cancel_keyboard(),
    )
    
    company_info = await get_company_by_inn(inn_clean)
    
    if company_info:
        context.user_data["new_supplier_dadata"] = {
            "inn": company_info.inn,
            "kpp": company_info.kpp,
            "name": company_info.name,
            "short_name": company_info.short_name,
        }
        
        keyboard = [
            [InlineKeyboardButton("✅ Подтвердить", callback_data="sup_confirm:yes")],
            [InlineKeyboardButton("✏️ Ввести вручную", callback_data="sup_confirm:manual")],
            [InlineKeyboardButton("❌ Отмена", callback_data="sup_confirm:cancel")],
        ]
        
        await update.message.reply_text(
            f"📋 *Найдена организация:*\n\n"
            f"ИНН: `{company_info.inn}`\n"
            f"КПП: `{company_info.kpp}`\n"
            f"Название: {company_info.short_name}\n\n"
            "Это правильная организация?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return SUPPLIER_CONFIRM
    else:
        # DaData не нашла — вводим вручную
        context.user_data["new_supplier_dadata"] = {
            "inn": inn_clean,
            "kpp": "-",
            "name": "",
            "short_name": "",
        }
        await update.message.reply_text(
            f"⚠️ Организация по ИНН `{inn_clean}` не найдена в базе.\n\n"
            "Введите название организации вручную:",
            parse_mode="Markdown",
            reply_markup=get_cancel_keyboard(),
        )
        # Пропускаем подтверждение — сразу запрашиваем название
        context.user_data["manual_supplier_name"] = True
        return SUPPLIER_EMAIL  # Переиспользуем как ввод названия


async def supplier_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Подтверждение данных из DaData."""
    query = update.callback_query
    await query.answer()
    data = query.data
    logger.debug(f"supplier_confirm: data={data}")
    
    if data == "sup_confirm:cancel":
        is_superadmin = update.effective_user.id in SUPERADMIN_IDS
        await query.edit_message_text("❌ Добавление поставщика отменено.")
        await query.message.reply_text(
            "Вы вернулись в главное меню.",
            reply_markup=get_main_menu_keyboard(is_superadmin),
        )
        return ConversationHandler.END
    
    if data == "sup_confirm:manual":
        await query.edit_message_text("Введите данные вручную.")
        await query.message.reply_text(
            "Введите название организации:",
            reply_markup=get_cancel_keyboard(),
        )
        context.user_data["manual_supplier_name"] = True
        return SUPPLIER_EMAIL
    
    # Подтверждено — продолжаем
    await query.edit_message_text("✅ Данные подтверждены.")
    await query.message.reply_text(
        "Введите Email поставщика:",
        reply_markup=get_cancel_keyboard(),
    )
    context.user_data["manual_supplier_name"] = False
    return SUPPLIER_EMAIL


async def supplier_email_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен email (или название при ручном вводе)."""
    text = update.message.text.strip()
    
    # Если это ввод названия вручную
    if context.user_data.get("manual_supplier_name"):
        logger.info(f"supplier_name_manual: name={text}")
        dadata = context.user_data.get("new_supplier_dadata", {})
        dadata["name"] = text
        dadata["short_name"] = text
        context.user_data["new_supplier_dadata"] = dadata
        context.user_data["manual_supplier_name"] = False
        
        await update.message.reply_text(
            "Введите Email поставщика:",
            reply_markup=get_cancel_keyboard(),
        )
        return SUPPLIER_EMAIL
    
    logger.info(f"supplier_email_received: email={text}")
    context.user_data["new_supplier_email"] = text
    
    await update.message.reply_text(
        "Введите телефон поставщика:",
        reply_markup=get_cancel_keyboard(),
    )
    return SUPPLIER_PHONE


async def supplier_phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен телефон."""
    text = update.message.text.strip()
    logger.info(f"supplier_phone_received: phone={text}")
    context.user_data["new_supplier_phone"] = text
    
    await update.message.reply_text(
        "Введите ФИО контактного лица:",
        reply_markup=get_cancel_keyboard(),
    )
    return SUPPLIER_CONTACT


async def supplier_contact_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получено ФИО контакта."""
    text = update.message.text.strip()
    logger.info(f"supplier_contact_received: contact={text}")
    context.user_data["new_supplier_contact"] = text
    
    await update.message.reply_text(
        "Введите предмет (категорию товаров):",
        reply_markup=get_cancel_keyboard(),
    )
    return SUPPLIER_SUBJECT


async def supplier_subject_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен предмет."""
    text = update.message.text.strip()
    logger.info(f"supplier_subject_received: subject={text}")
    context.user_data["new_supplier_subject"] = text
    
    await update.message.reply_text(
        "Введите точки (локации поставки):",
        reply_markup=get_cancel_keyboard(),
    )
    return SUPPLIER_LOCATIONS


async def supplier_locations_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получены точки — сохраняем поставщика и переходим далее."""
    text = update.message.text.strip()
    logger.info(f"supplier_locations_received: locations={text}")
    
    # Собираем данные поставщика
    dadata = context.user_data.get("new_supplier_dadata")
    company_info = context.user_data.get("product_company_info", {})
    
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
    }
    
    # Сохраняем данные поставщика в контекст для последующего использования
    context.user_data["current_supplier_data"] = supplier_data
    
    # Сохраняем в Google Sheets
    sheet_id = company_info.get("sheet_id")
    if sheet_id:
        success = await google_sheets_service.add_supplier(sheet_id, supplier_data)
        if success:
            logger.info(f"Поставщик добавлен в таблицу: {supplier_data['name']}")
        else:
            logger.error("Ошибка добавления поставщика в таблицу")
    
    # Сохраняем поставщика в черновик продукта
    _save_draft(context, {
        "supplier_name": supplier_data["name"],
        "supplier_inn": supplier_data["inn"],
        "supplier_kpp": supplier_data["kpp"],
        "supplier_email": supplier_data["email"],
        "supplier_phone": supplier_data["phone"],
        "supplier_contact": supplier_data["contact_name"],
        "supplier_locations": supplier_data["locations"],
    })
    
    # Проверяем сценарий
    scenario = context.user_data.get("supplier_add_scenario", "prorabotka")
    if scenario == "zavedenie":
        # Переходим к загрузке карточки поставщика
        await update.message.reply_text(
            f"✅ Данные поставщика *{supplier_data['name']}* сохранены!\n\n"
            "📎 Теперь загрузите *карточку поставщика* (PDF, Word, Excel или изображение).\n"
            "Это необходимо для отправки на заведение.",
            parse_mode="Markdown",
            reply_markup=get_upload_keyboard(allow_skip=False),
        )
        return SUPPLIER_CARD
    
    # Сценарий "для проработки" — сразу к единице измерения
    await update.message.reply_text(
        f"✅ Поставщик *{supplier_data['name']}* добавлен!\n\n"
        "Выберите единицу измерения:",
        parse_mode="Markdown",
        reply_markup=get_unit_keyboard(),
    )
    return UNIT


# nomenclature_received удалена — название определяется после фото этикетки через Vision/GPT


async def supplier_card_uploaded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка загрузки карточки поставщика (документ или фото)."""
    logger.info("supplier_card_uploaded called")
    
    company_info = context.user_data.get("product_company_info", {})
    supplier_data = context.user_data.get("current_supplier_data", {})
    drive_folder_id = company_info.get("drive_folder_id")
    
    # Определяем тип загрузки
    if update.message.document:
        file = await update.message.document.get_file()
        filename = update.message.document.file_name
        mime_type = update.message.document.mime_type or "application/octet-stream"
    elif update.message.photo:
        photo = update.message.photo[-1]  # Максимальное разрешение
        file = await photo.get_file()
        filename = f"card_{supplier_data.get('inn', 'unknown')}.jpg"
        mime_type = "image/jpeg"
    else:
        await update.message.reply_text(
            "❌ Пожалуйста, отправьте файл (PDF, Word, Excel) или фото карточки поставщика.",
            reply_markup=get_upload_keyboard(allow_skip=False),
        )
        return SUPPLIER_CARD
    
    # Скачиваем файл
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix) as tmp:
        await file.download_to_drive(tmp.name)
        tmp_path = Path(tmp.name)
    
    logger.debug(f"Карточка скачана: {tmp_path}, size={tmp_path.stat().st_size}")
    
    # Загружаем в Google Drive (папка "Поставщики" -> "Наименование поставщика")
    card_file_id = None
    card_link = None
    
    if drive_folder_id:
        supplier_name = supplier_data.get("name", "Неизвестный")
        supplier_folder_id = create_supplier_folder(supplier_name, drive_folder_id)
        
        if supplier_folder_id:
            card_file_id = upload_supplier_card(tmp_path, supplier_folder_id, filename, mime_type)
            if card_file_id:
                card_link = get_file_link(card_file_id)
                context.user_data["supplier_card_path"] = tmp_path
                context.user_data["supplier_card_link"] = card_link
                logger.info(f"Карточка загружена в Drive: {card_link}")
            else:
                logger.error("Ошибка загрузки карточки в Drive")
        else:
            logger.error("Ошибка создания папки поставщика")
    
    await update.message.reply_text(
        "📎 Карточка поставщика получена!\n\n"
        "Отправляю письма на заведение...",
        parse_mode="Markdown",
    )
    
    # Отправляем 4 письма
    await _send_registration_emails(update, context, tmp_path if tmp_path.exists() else None)
    
    # Переходим к выбору единицы измерения
    await update.message.reply_text(
        f"✅ Поставщик *{supplier_data.get('name', '')}* отправлен на заведение!\n\n"
        "Теперь продолжим с проработкой продукта.\n"
        "Выберите единицу измерения:",
        parse_mode="Markdown",
        reply_markup=get_unit_keyboard(),
    )
    return UNIT


async def _send_registration_emails(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    card_path: Path = None,
) -> None:
    """Отправить 4 письма для заведения поставщика."""
    supplier_data = context.user_data.get("current_supplier_data", {})
    
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
    )
    
    # Формируем отчёт об отправке
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


async def supplier_card_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена загрузки карточки — возврат в меню."""
    logger.info("supplier_card_cancel called")
    is_superadmin = update.effective_user.id in SUPERADMIN_IDS
    await update.message.reply_text(
        "❌ Заведение продукта отменено.",
        reply_markup=get_main_menu_keyboard(is_superadmin),
    )
    return ConversationHandler.END


async def unit_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора единицы измерения."""
    query = update.callback_query
    await query.answer()
    unit = query.data.split(":")[1]
    logger.debug(f"unit_selected: unit={unit}")
    _save_draft(context, {"unit": unit})
    await query.edit_message_text(f"✅ Ед. изм: {unit}")
    await query.message.reply_text(
        "Введите цену (без НДС), число:",
        reply_markup=get_cancel_keyboard(),
    )
    return PRICE


async def price_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получена цена (без НДС)."""
    text = update.message.text.strip().replace(",", ".")
    logger.debug(f"price_received: text={text}")
    try:
        price = float(text)
        if price <= 0:
            raise ValueError("Цена должна быть положительной")
    except ValueError as e:
        await update.message.reply_text(
            f"❌ Неверный формат. Введите число (например: 150.50):\n{e}",
            reply_markup=get_cancel_keyboard(),
        )
        return PRICE
    _save_draft(context, {"price": price})
    
    # Переходим сразу к сертификатам (НДС убран)
    await update.message.reply_text(
        f"✅ Цена без НДС: {price} ₽\n\n"
        "📄 *Сертификаты/декларации* (опционально)\n\n"
        "Отправьте файлы или нажмите «Пропустить»:",
        parse_mode="Markdown",
        reply_markup=get_upload_keyboard(allow_skip=True),
    )
    return CERTS


async def vat_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора ставки НДС."""
    query = update.callback_query
    await query.answer()
    vat = query.data.split(":")[1]
    logger.debug(f"vat_selected: vat={vat}")
    _save_draft(context, {"vat_rate": vat})
    await query.edit_message_text(f"✅ НДС: {vat}")
    await query.message.reply_text(
        "📄 *Загрузка сертификатов и деклараций*\n\n"
        "Отправьте файлы (PDF, изображения). Можно несколько.\n"
        "Когда закончите — нажмите «Завершить загрузку».",
        parse_mode="Markdown",
        reply_markup=get_upload_keyboard(),
    )
    return CERTS


async def certs_uploaded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен файл сертификата/декларации."""
    if update.message.document:
        file = await update.message.document.get_file()
        fname = update.message.document.file_name or "document"
    elif update.message.photo:
        file = await update.message.photo[-1].get_file()
        fname = f"photo_{file.file_id[:8]}.jpg"
    else:
        await update.message.reply_text("Отправьте документ или фото.")
        return CERTS

    draft = _get_draft(context)
    certs = draft.get("certs", [])
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(fname).suffix) as tmp:
        await file.download_to_drive(tmp.name)
        # TODO: загрузить в Google Drive
        certs.append({"name": fname, "local_path": tmp.name})
    _save_draft(context, {"certs": certs})
    logger.info(f"Сертификат получен: {fname}, всего: {len(certs)}")
    
    # Inline-кнопка "Завершить загрузку" в чате + минимальная Reply-клавиатура
    await update.message.reply_text(
        f"✅ Файл «{fname}» принят. Можно отправить ещё.",
        reply_markup=get_upload_keyboard_minimal(allow_skip=True),
    )
    await update.message.reply_text(
        "Нажмите для завершения:",
        reply_markup=get_finish_upload_inline_keyboard(),
    )
    return CERTS


async def certs_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершение загрузки сертификатов."""
    logger.debug("certs_done: переход к фото продукта")
    draft = _get_draft(context)
    certs_count = len(draft.get("certs", []))
    await update.message.reply_text(
        f"✅ Сертификатов загружено: {certs_count}\n\n"
        "📷 *Фото продукта* (обязательно)\n\n"
        "Отправьте фото продукта (общее). Можно несколько.\n"
        "Когда закончите — нажмите «Завершить загрузку».",
        parse_mode="Markdown",
        reply_markup=get_upload_keyboard(allow_skip=False),
    )
    return PHOTOS_PRODUCT


async def certs_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершение загрузки сертификатов (через inline-кнопку)."""
    query = update.callback_query
    await query.answer()
    logger.debug("certs_done_callback: переход к фото продукта")
    
    draft = _get_draft(context)
    certs_count = len(draft.get("certs", []))
    
    await query.edit_message_text("✅ Загрузка сертификатов завершена.")
    await query.message.reply_text(
        f"✅ Сертификатов загружено: {certs_count}\n\n"
        "📷 *Фото продукта* (обязательно)\n\n"
        "Отправьте фото продукта (общее). Можно несколько.\n"
        "Когда закончите — нажмите «Завершить загрузку».",
        parse_mode="Markdown",
        reply_markup=get_upload_keyboard(allow_skip=False),
    )
    return PHOTOS_PRODUCT


async def certs_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пропуск загрузки сертификатов."""
    logger.info(f"certs_skip called: user_id={update.effective_user.id}")
    await update.message.reply_text(
        "⏭ Сертификаты и декларации пропущены.\n\n"
        "📷 *Фото продукта* (обязательно)\n\n"
        "Отправьте фото продукта (общее). Можно несколько.\n"
        "Когда закончите — нажмите «Завершить загрузку».",
        parse_mode="Markdown",
        reply_markup=get_upload_keyboard(allow_skip=False),
    )
    return PHOTOS_PRODUCT


async def photos_product_uploaded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получено фото продукта (как фото или как документ-изображение)."""
    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        fname = f"product_{file.file_id[:8]}.jpg"
        suffix = ".jpg"
    elif update.message.document:
        doc = update.message.document
        mime = doc.mime_type or ""
        if not mime.startswith("image/"):
            await update.message.reply_text("Отправьте изображение (фото или файл-картинку).")
            return PHOTOS_PRODUCT
        file = await doc.get_file()
        fname = doc.file_name or f"product_{file.file_id[:8]}.jpg"
        suffix = Path(fname).suffix or ".jpg"
    else:
        await update.message.reply_text("Отправьте фото.")
        return PHOTOS_PRODUCT

    draft = _get_draft(context)
    photos = draft.get("photos_product", [])
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        await file.download_to_drive(tmp.name)
        photos.append({"name": fname, "local_path": tmp.name})
    _save_draft(context, {"photos_product": photos})
    logger.info(f"Фото продукта получено: {fname}, всего: {len(photos)}")
    
    # Inline-кнопка "Завершить загрузку" в чате + минимальная Reply-клавиатура
    await update.message.reply_text(
        f"✅ Фото «{fname}» принято. Можно отправить ещё.",
        reply_markup=get_upload_keyboard_minimal(allow_skip=False),
    )
    await update.message.reply_text(
        "Нажмите для завершения:",
        reply_markup=get_finish_upload_inline_keyboard(),
    )
    return PHOTOS_PRODUCT


async def photos_product_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершение загрузки фото продукта."""
    logger.debug("photos_product_done: переход к фото этикетки")
    draft = _get_draft(context)
    photos_count = len(draft.get("photos_product", []))
    await update.message.reply_text(
        f"✅ Фото продукта загружено: {photos_count}\n\n"
        "🏷 *Фото этикетки*\n\n"
        "Отправьте фото этикетки. Можно несколько.\n"
        "Когда закончите — нажмите «Завершить загрузку».",
        parse_mode="Markdown",
        reply_markup=get_upload_keyboard(),
    )
    return PHOTOS_LABEL


async def photos_product_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершение загрузки фото продукта (через inline-кнопку)."""
    query = update.callback_query
    await query.answer()
    logger.debug("photos_product_done_callback: переход к фото этикетки")
    
    draft = _get_draft(context)
    photos_count = len(draft.get("photos_product", []))
    
    await query.edit_message_text("✅ Загрузка фото продукта завершена.")
    await query.message.reply_text(
        f"✅ Фото продукта загружено: {photos_count}\n\n"
        "🏷 *Фото этикетки*\n\n"
        "Отправьте фото этикетки. Можно несколько.\n"
        "Когда закончите — нажмите «Завершить загрузку».",
        parse_mode="Markdown",
        reply_markup=get_upload_keyboard(),
    )
    return PHOTOS_LABEL


async def photos_label_uploaded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получено фото этикетки (как фото или как документ-изображение)."""
    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        fname = f"label_{file.file_id[:8]}.jpg"
        suffix = ".jpg"
    elif update.message.document:
        doc = update.message.document
        mime = doc.mime_type or ""
        if not mime.startswith("image/"):
            await update.message.reply_text("Отправьте изображение (фото или файл-картинку).")
            return PHOTOS_LABEL
        file = await doc.get_file()
        fname = doc.file_name or f"label_{file.file_id[:8]}.jpg"
        suffix = Path(fname).suffix or ".jpg"
    else:
        await update.message.reply_text("Отправьте фото.")
        return PHOTOS_LABEL

    draft = _get_draft(context)
    photos = draft.get("photos_label", [])
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        await file.download_to_drive(tmp.name)
        photos.append({"name": fname, "local_path": tmp.name})
    _save_draft(context, {"photos_label": photos})
    logger.info(f"Фото этикетки получено: {fname}, всего: {len(photos)}")
    
    # Inline-кнопка "Завершить загрузку" в чате + минимальная Reply-клавиатура
    await update.message.reply_text(
        f"✅ Фото «{fname}» принято. Можно отправить ещё.",
        reply_markup=get_upload_keyboard_minimal(allow_skip=True),
    )
    await update.message.reply_text(
        "Нажмите для завершения:",
        reply_markup=get_finish_upload_inline_keyboard(),
    )
    return PHOTOS_LABEL


async def photos_label_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершение загрузки фото этикетки — распознавание названия через Vision/GPT."""
    draft = _get_draft(context)
    photos_label = draft.get("photos_label", [])
    logger.info(f"photos_label_done: photos_label={len(photos_label)}")

    if not photos_label:
        # Нет фото — сразу ручной ввод
        await update.message.reply_text(
            "📝 Введите название продукта вручную:",
            reply_markup=get_cancel_keyboard(),
        )
        return NOMENCLATURE_MANUAL

    # Собираем пути ко всем фото
    image_paths = []
    for photo in photos_label:
        local_path = photo.get("local_path")
        if local_path and Path(local_path).exists():
            image_paths.append(Path(local_path))
    
    if not image_paths:
        await update.message.reply_text(
            "⚠️ Не удалось найти фото для распознавания.\n"
            "Введите название продукта вручную:",
            reply_markup=get_cancel_keyboard(),
        )
        return NOMENCLATURE_MANUAL

    photos_count = len(image_paths)
    await update.message.reply_text(
        f"🔍 Распознаю текст на этикетке ({photos_count} фото)..."
    )

    try:
        from bot.services.yandex_ai import get_product_name_from_multiple_labels
        
        product_name, ocr_text = await get_product_name_from_multiple_labels(image_paths)
        
        if product_name:
            # Название определено — предлагаем подтвердить
            _save_draft(context, {"suggested_nomenclature": product_name, "ocr_text": ocr_text})
            
            keyboard = [
                [InlineKeyboardButton(f"✅ {product_name}", callback_data="nom_confirm:yes")],
                [InlineKeyboardButton("✏️ Ввести вручную", callback_data="nom_confirm:manual")],
            ]
            
            await update.message.reply_text(
                f"🎯 *Определено название продукта:*\n\n"
                f"📦 *{product_name}*\n\n"
                "Подтвердите или введите другое:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return NOMENCLATURE_CONFIRM
        else:
            # Не удалось определить
            logger.warning("Vision/GPT не определил название")
            if ocr_text:
                _save_draft(context, {"ocr_text": ocr_text})
                await update.message.reply_text(
                    "⚠️ Не удалось автоматически определить название.\n\n"
                    f"📄 Распознанный текст:\n_{ocr_text[:300]}..._\n\n"
                    "Введите название продукта вручную:",
                    parse_mode="Markdown",
                    reply_markup=get_cancel_keyboard(),
                )
            else:
                await update.message.reply_text(
                    "⚠️ Не удалось распознать текст на этикетке.\n"
                    "Введите название продукта вручную:",
                    reply_markup=get_cancel_keyboard(),
                )
            return NOMENCLATURE_MANUAL
            
    except Exception as e:
        logger.error(f"Ошибка распознавания: {e}", exc_info=True)
        await update.message.reply_text(
            "⚠️ Ошибка при распознавании. Введите название продукта вручную:",
            reply_markup=get_cancel_keyboard(),
        )
        return NOMENCLATURE_MANUAL


async def photos_label_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершение загрузки фото этикетки (через inline-кнопку) — распознавание названия через Vision/GPT."""
    query = update.callback_query
    await query.answer()
    
    draft = _get_draft(context)
    photos_label = draft.get("photos_label", [])
    logger.info(f"photos_label_done_callback: photos_label={len(photos_label)}")
    
    await query.edit_message_text("✅ Загрузка фото этикетки завершена.")

    if not photos_label:
        # Нет фото — сразу ручной ввод
        await query.message.reply_text(
            "📝 Введите название продукта вручную:",
            reply_markup=get_cancel_keyboard(),
        )
        return NOMENCLATURE_MANUAL

    # Собираем пути ко всем фото
    image_paths = []
    for photo in photos_label:
        local_path = photo.get("local_path")
        if local_path and Path(local_path).exists():
            image_paths.append(Path(local_path))
    
    if not image_paths:
        await query.message.reply_text(
            "⚠️ Не удалось найти фото для распознавания.\n"
            "Введите название продукта вручную:",
            reply_markup=get_cancel_keyboard(),
        )
        return NOMENCLATURE_MANUAL

    photos_count = len(image_paths)
    await query.message.reply_text(
        f"🔍 Распознаю текст на этикетке ({photos_count} фото)..."
    )

    try:
        from bot.services.yandex_ai import get_product_name_from_multiple_labels
        
        product_name, ocr_text = await get_product_name_from_multiple_labels(image_paths)
        
        if product_name:
            # Название определено — предлагаем подтвердить
            _save_draft(context, {"suggested_nomenclature": product_name, "ocr_text": ocr_text})
            
            keyboard = [
                [InlineKeyboardButton(f"✅ {product_name}", callback_data="nom_confirm:yes")],
                [InlineKeyboardButton("✏️ Ввести вручную", callback_data="nom_confirm:manual")],
            ]
            
            await query.message.reply_text(
                f"🎯 *Определено название продукта:*\n\n"
                f"📦 *{product_name}*\n\n"
                "Подтвердите или введите другое:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return NOMENCLATURE_CONFIRM
        else:
            # Не удалось определить
            logger.warning("Vision/GPT не определил название")
            if ocr_text:
                _save_draft(context, {"ocr_text": ocr_text})
                await query.message.reply_text(
                    "⚠️ Не удалось автоматически определить название.\n\n"
                    f"📄 Распознанный текст:\n_{ocr_text[:300]}..._\n\n"
                    "Введите название продукта вручную:",
                    parse_mode="Markdown",
                    reply_markup=get_cancel_keyboard(),
                )
            else:
                await query.message.reply_text(
                    "⚠️ Не удалось распознать текст на этикетке.\n"
                    "Введите название продукта вручную:",
                    reply_markup=get_cancel_keyboard(),
                )
            return NOMENCLATURE_MANUAL
            
    except Exception as e:
        logger.error(f"Ошибка распознавания (callback): {e}", exc_info=True)
        await query.message.reply_text(
            "⚠️ Ошибка при распознавании. Введите название продукта вручную:",
            reply_markup=get_cancel_keyboard(),
        )
        return NOMENCLATURE_MANUAL


async def photos_label_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пропуск загрузки фото этикетки — переход к ручному вводу названия."""
    logger.info(f"photos_label_skip called: user_id={update.effective_user.id}")
    
    await update.message.reply_text(
        "⏭ Фото этикетки пропущены.\n\n"
        "📝 Введите название продукта:",
        reply_markup=get_cancel_keyboard(),
    )
    return NOMENCLATURE_MANUAL


async def nomenclature_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Подтверждение названия продукта из Vision/GPT."""
    query = update.callback_query
    await query.answer()
    data = query.data
    logger.debug(f"nomenclature_confirm: data={data}")
    
    if data == "nom_confirm:manual":
        await query.edit_message_text("✏️ Введите название продукта вручную:")
        await query.message.reply_text(
            "Введите название:",
            reply_markup=get_cancel_keyboard(),
        )
        return NOMENCLATURE_MANUAL
    
    # Подтверждено — берём предложенное название
    draft = _get_draft(context)
    nomenclature = draft.get("suggested_nomenclature", "")
    _save_draft(context, {"supplier_nomenclature": nomenclature})
    
    await query.edit_message_text(f"✅ Название: *{nomenclature}*", parse_mode="Markdown")
    
    # Переходим к выбору типа заявки
    return await ask_request_type(query.message, context)


async def nomenclature_manual_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получено название продукта вручную."""
    nomenclature = update.message.text.strip()
    logger.info(f"nomenclature_manual_received: nomenclature={nomenclature}")
    
    if len(nomenclature) < 2:
        await update.message.reply_text(
            "⚠️ Название слишком короткое. Введите снова:",
            reply_markup=get_cancel_keyboard(),
        )
        return NOMENCLATURE_MANUAL
    
    _save_draft(context, {"supplier_nomenclature": nomenclature})
    
    # Переходим к выбору типа заявки
    return await ask_request_type(update.message, context)


async def ask_request_type(message, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показать выбор типа заявки (срочная/регулярная)."""
    logger.debug("ask_request_type: показываем выбор типа заявки")
    
    keyboard = [
        [InlineKeyboardButton(
            "🔴 Срочная (SLA: 2 дня)",
            callback_data="req_type:urgent"
        )],
        [InlineKeyboardButton(
            "🟢 Регулярная (SLA: 5-14 дней)",
            callback_data="req_type:regular"
        )],
    ]
    
    await message.reply_text(
        "🚨 *Выберите тип заявки:*\n\n"
        "🔴 *Срочная* — проблемы с текущим сырьём, SLA: 2 рабочих дня\n\n"
        "🟢 *Регулярная* — альтернатива по цене/качеству, SLA: 5-14 рабочих дней",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return REQUEST_TYPE


async def request_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора типа заявки."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    logger.debug(f"request_type_selected: data={data}")
    
    if data == "req_type:urgent":
        request_type = "urgent"
        sla_days = 2
        type_label = "🔴 Срочная"
    else:
        request_type = "regular"
        sla_days = 14
        type_label = "🟢 Регулярная"
    
    _save_draft(context, {
        "request_type": request_type,
        "sla_days": sla_days,
    })
    
    await query.edit_message_text(f"✅ Тип заявки: {type_label} (SLA: {sla_days} дней)")
    
    # Переходим к финализации
    return await finalize_product(query.message, context)


async def finalize_product(message, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Финализация и сохранение заявки на проработку."""
    draft = _get_draft(context)
    logger.info(f"finalize_product: draft keys={list(draft.keys())}")

    company_info = context.user_data.get("product_company_info", {})
    user = message.chat
    telegram_username = user.username if hasattr(user, 'username') and user.username else str(user.id)

    await message.reply_text("⏳ Сохраняю заявку и загружаю файлы...")

    try:
        from bot.services.product_request import save_product_request

        result = await save_product_request(company_info, draft, telegram_username)
        
        if result and result.get("success"):
            request_id = result.get("request_id", "—")
            folder_link = result.get("folder_link", "")
            
            supplier_name = draft.get("supplier_name") or "—"
            nomenclature = draft.get("supplier_nomenclature") or "—"
            price = draft.get("price") or "—"
            request_type = draft.get("request_type", "regular")
            sla_days = draft.get("sla_days", 14)
            is_superadmin = user.id in SUPERADMIN_IDS
            
            # Форматируем тип заявки
            type_emoji = "🔴" if request_type == "urgent" else "🟢"
            type_text = "Срочная" if request_type == "urgent" else "Регулярная"
            
            summary = (
                f"✅ *Заявка на проработку создана!*\n\n"
                f"📋 ID: `{request_id}`\n"
                f"• Тип: {type_emoji} {type_text} (SLA: {sla_days} дней)\n"
                f"• Поставщик: {supplier_name}\n"
                f"• Название: {nomenclature}\n"
                f"• Ед. изм: {draft.get('unit', '—')}\n"
                f"• Цена без НДС: {price} ₽\n"
                f"• Сертификатов: {result.get('certs_count', 0)}\n"
                f"• Фото продукта: {result.get('photos_product_count', 0)}\n"
                f"• Фото этикетки: {result.get('photos_label_count', 0)}\n"
            )
            
            if folder_link:
                summary += f"\n📁 [Папка в Google Drive]({folder_link})"
            
            await message.reply_text(
                summary,
                parse_mode="Markdown",
                reply_markup=get_main_menu_keyboard(is_superadmin),
                disable_web_page_preview=True,
            )
            
            # Отправляем уведомления
            try:
                from bot.services.notifications import send_request_notifications
                
                company_id = company_info.get("company_id")
                if company_id:
                    bot = context.bot
                    notifications_sent = await send_request_notifications(
                        bot=bot,
                        company_id=company_id,
                        request_type=request_type,
                        request_id=request_id,
                        nomenclature=nomenclature,
                        supplier_name=supplier_name,
                        price=str(price),
                        sla_days=sla_days,
                        username=telegram_username,
                        folder_link=folder_link,
                    )
                    if notifications_sent > 0:
                        logger.info(f"Отправлено {notifications_sent} уведомлений")
            except Exception as e:
                logger.error(f"Ошибка отправки уведомлений: {e}", exc_info=True)
        else:
            await message.reply_text(
                "⚠️ Ошибка сохранения заявки. Попробуйте позже.",
                reply_markup=get_main_menu_keyboard(user.id in SUPERADMIN_IDS),
            )
    except Exception as e:
        logger.error(f"Ошибка сохранения продукта: {e}", exc_info=True)
        await message.reply_text(
            "⚠️ Произошла ошибка. Попробуйте позже.",
            reply_markup=get_main_menu_keyboard(user.id in SUPERADMIN_IDS),
        )

    # Очистка черновика и контекста
    context.user_data.pop(_get_draft_key(context), None)
    context.user_data.pop("selected_supplier_for_product", None)
    context.user_data.pop("product_company_info", None)
    return ConversationHandler.END


async def cancel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена заведения продукта (текстовая команда)."""
    logger.info(f"cancel_registration: user_id={update.effective_user.id}")
    context.user_data.pop(_get_draft_key(context), None)
    context.user_data.pop("selected_supplier_for_product", None)  # Очищаем выбранного поставщика
    is_superadmin = update.effective_user.id in SUPERADMIN_IDS
    await update.message.reply_text(
        "❌ Заведение продукта отменено.",
        reply_markup=get_main_menu_keyboard(is_superadmin),
    )
    return ConversationHandler.END


def get_product_registration_handler() -> ConversationHandler:
    """Собрать ConversationHandler для заведения продукта."""
    return ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^📦 Заведение продукта на проработку$"),
                start_product_registration,
            ),
        ],
        states={
            SUPPLIER: [
                CallbackQueryHandler(supplier_selected, pattern="^sup_"),
            ],
            SUPPLIER_ADD_SCENARIO: [
                CallbackQueryHandler(supplier_add_scenario, pattern="^sup_scenario:"),
            ],
            SUPPLIER_INN: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    supplier_inn_received,
                ),
            ],
            SUPPLIER_CONFIRM: [
                CallbackQueryHandler(supplier_confirm, pattern="^sup_confirm:"),
            ],
            SUPPLIER_EMAIL: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    supplier_email_received,
                ),
            ],
            SUPPLIER_PHONE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    supplier_phone_received,
                ),
            ],
            SUPPLIER_CONTACT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    supplier_contact_received,
                ),
            ],
            SUPPLIER_SUBJECT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    supplier_subject_received,
                ),
            ],
            SUPPLIER_LOCATIONS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    supplier_locations_received,
                ),
            ],
            SUPPLIER_CARD: [
                MessageHandler(
                    (filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND,
                    supplier_card_uploaded,
                ),
                MessageHandler(
                    filters.Regex("^❌ Отмена$"),
                    supplier_card_cancel,
                ),
            ],
            # NOMENCLATURE удалено — название определяется после фото этикетки
            UNIT: [
                CallbackQueryHandler(unit_selected, pattern="^unit:"),
            ],
            PRICE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    price_received,
                ),
            ],
            # VAT убран — цена всегда без НДС
            CERTS: [
                MessageHandler(
                    (filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND,
                    certs_uploaded,
                ),
                MessageHandler(
                    filters.Regex("^✅ Завершить загрузку$"),
                    certs_done,
                ),
                CallbackQueryHandler(certs_done_callback, pattern="^upload_done$"),
                MessageHandler(
                    filters.Regex("^⏭ Пропустить$"),
                    certs_skip,
                ),
            ],
            PHOTOS_PRODUCT: [
                MessageHandler(
                    (filters.PHOTO | filters.Document.IMAGE) & ~filters.COMMAND,
                    photos_product_uploaded,
                ),
                MessageHandler(
                    filters.Regex("^✅ Завершить загрузку$"),
                    photos_product_done,
                ),
                CallbackQueryHandler(photos_product_done_callback, pattern="^upload_done$"),
            ],
            PHOTOS_LABEL: [
                MessageHandler(
                    (filters.PHOTO | filters.Document.IMAGE) & ~filters.COMMAND,
                    photos_label_uploaded,
                ),
                MessageHandler(
                    filters.Regex("^✅ Завершить загрузку$"),
                    photos_label_done,
                ),
                CallbackQueryHandler(photos_label_done_callback, pattern="^upload_done$"),
                MessageHandler(
                    filters.Regex("^⏭ Пропустить$"),
                    photos_label_skip,
                ),
            ],
            NOMENCLATURE_CONFIRM: [
                CallbackQueryHandler(nomenclature_confirm, pattern="^nom_confirm:"),
            ],
            NOMENCLATURE_MANUAL: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex("^❌ Отмена$"),
                    nomenclature_manual_received,
                ),
            ],
            REQUEST_TYPE: [
                CallbackQueryHandler(request_type_selected, pattern="^req_type:"),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^/cancel$"), cancel_registration),
            MessageHandler(filters.Regex("^❌ Отмена$"), cancel_registration),
            MessageHandler(
                filters.Regex("^(📦 Заведение продукта на проработку|🔄 Процесс проработки)$"),
                cancel_registration,
            ),
        ],
        name="product_registration",
    )
