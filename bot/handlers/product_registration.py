"""Процесс заведения продукта на проработку."""
import tempfile
from pathlib import Path

from loguru import logger

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.keyboards.product_registration import (
    get_supplier_keyboard,
    get_unit_keyboard,
    get_vat_keyboard,
    get_upload_done_keyboard,
)
from bot.services.database import (
    get_or_create_default_company,
    get_suppliers_for_company,
    get_supplier_by_id,
)

# Состояния диалога
(
    SUPPLIER,
    SUPPLIER_NEW,
    NOMENCLATURE,
    UNIT,
    PRICE,
    VAT,
    CERTS,
    PHOTOS_PRODUCT,
    PHOTOS_LABEL,
) = range(9)


def _get_draft_key(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Ключ для хранения черновика в user_data."""
    return f"product_draft_{context.user_data.get('company_id', 0)}"


def _get_draft(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Получить черновик продукта."""
    return context.user_data.get(_get_draft_key(context), {})


def _save_draft(context: ContextTypes.DEFAULT_TYPE, data: dict) -> None:
    """Сохранить черновик."""
    key = _get_draft_key(context)
    context.user_data[key] = {**_get_draft(context), **data}
    logger.debug(f"Черновик сохранён: keys={list(context.user_data[key].keys())}")


async def start_product_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало заведения продукта — выбор поставщика."""
    logger.info(f"start_product_registration called: user_id={update.effective_user.id}")

    company = await get_or_create_default_company()
    context.user_data["company_id"] = company.id
    _save_draft(context, {})

    suppliers = await get_suppliers_for_company(company.id)
    if not suppliers:
        await update.message.reply_text(
            "📋 Список поставщиков пуст. Добавление нового поставщика будет реализовано позже.\n\n"
            "Пока введите название поставщика вручную:",
        )
        return SUPPLIER_NEW

    await update.message.reply_text(
        "📦 **Заведение продукта на проработку**\n\n"
        "Выберите поставщика:",
        parse_mode="Markdown",
        reply_markup=get_supplier_keyboard(suppliers),
    )
    return SUPPLIER


async def supplier_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора поставщика (callback)."""
    query = update.callback_query
    await query.answer()
    logger.debug(f"supplier_selected: data={query.data}")

    if query.data == "supplier:new":
        await query.edit_message_text("Введите название нового поставщика:")
        return SUPPLIER_NEW

    supplier_id = int(query.data.split(":")[1])
    supplier = await get_supplier_by_id(supplier_id)
    supplier_name = supplier.name if supplier else "Поставщик"
    _save_draft(context, {"supplier_id": supplier_id, "supplier_name": supplier_name})
    await query.edit_message_text("✅ Поставщик выбран.\n\nВведите номенклатуру поставщика (прайсовое название):")
    return NOMENCLATURE


async def supplier_new_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получено название нового поставщика (заглушка — пока не добавляем в БД)."""
    name = update.message.text.strip()
    logger.info(f"supplier_new_received: name={name}")
    # TODO: добавить поставщика в БД
    _save_draft(context, {"supplier_name": name, "supplier_id": None})
    await update.message.reply_text("✅ Поставщик сохранён.\n\nВведите номенклатуру поставщика (прайсовое название):")
    return NOMENCLATURE


async def nomenclature_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получена номенклатура."""
    text = update.message.text.strip()
    logger.debug(f"nomenclature_received: text={text}")
    _save_draft(context, {"supplier_nomenclature": text})
    await update.message.reply_text("Выберите единицу измерения:", reply_markup=get_unit_keyboard())
    return UNIT


async def unit_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора единицы измерения."""
    query = update.callback_query
    await query.answer()
    unit = query.data.split(":")[1]
    logger.debug(f"unit_selected: unit={unit}")
    _save_draft(context, {"unit": unit})
    await query.edit_message_text(f"✅ Ед. изм: {unit}\n\nВведите цену (без НДС), число:")
    return PRICE


async def price_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получена цена."""
    text = update.message.text.strip().replace(",", ".")
    logger.debug(f"price_received: text={text}")
    try:
        price = float(text)
        if price <= 0:
            raise ValueError("Цена должна быть положительной")
    except ValueError as e:
        await update.message.reply_text(f"❌ Неверный формат. Введите число (например: 150.50):\n{e}")
        return PRICE
    _save_draft(context, {"price": price})
    await update.message.reply_text("Выберите ставку НДС:", reply_markup=get_vat_keyboard())
    return VAT


async def vat_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора ставки НДС."""
    query = update.callback_query
    await query.answer()
    vat = query.data.split(":")[1]
    logger.debug(f"vat_selected: vat={vat}")
    _save_draft(context, {"vat_rate": vat})
    await query.edit_message_text(
        "📄 **Загрузка сертификатов и деклараций**\n\n"
        "Отправьте файлы (PDF, изображения). Можно несколько.\n"
        "Когда закончите — нажмите кнопку ниже.",
        parse_mode="Markdown",
        reply_markup=get_upload_done_keyboard(),
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
    await update.message.reply_text(f"✅ Файл «{fname}» принят. Можно отправить ещё или нажать «Завершить загрузку».")
    return CERTS


async def certs_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершение загрузки сертификатов."""
    query = update.callback_query
    await query.answer()
    logger.debug("certs_done: переход к фото продукта")
    await query.edit_message_text(
        "📷 **Фото продукта**\n\n"
        "Отправьте фото продукта (общее). Можно несколько.\n"
        "Когда закончите — нажмите кнопку ниже.",
        parse_mode="Markdown",
        reply_markup=get_upload_done_keyboard(),
    )
    return PHOTOS_PRODUCT


async def photos_product_uploaded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получено фото продукта."""
    if not update.message.photo:
        await update.message.reply_text("Отправьте фото.")
        return PHOTOS_PRODUCT
    file = await update.message.photo[-1].get_file()
    fname = f"product_{file.file_id[:8]}.jpg"
    draft = _get_draft(context)
    photos = draft.get("photos_product", [])
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
        await file.download_to_drive(tmp.name)
        photos.append({"name": fname, "local_path": tmp.name})
    _save_draft(context, {"photos_product": photos})
    logger.info(f"Фото продукта получено: {fname}, всего: {len(photos)}")
    await update.message.reply_text(f"✅ Фото принято. Можно отправить ещё или нажать «Завершить загрузку».")
    return PHOTOS_PRODUCT


async def photos_product_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершение загрузки фото продукта."""
    query = update.callback_query
    await query.answer()
    logger.debug("photos_product_done: переход к фото этикетки")
    await query.edit_message_text(
        "🏷 **Фото этикетки**\n\n"
        "Отправьте фото этикетки. Можно несколько.\n"
        "Когда закончите — нажмите кнопку ниже.",
        parse_mode="Markdown",
        reply_markup=get_upload_done_keyboard(),
    )
    return PHOTOS_LABEL


async def photos_label_uploaded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получено фото этикетки."""
    if not update.message.photo:
        await update.message.reply_text("Отправьте фото.")
        return PHOTOS_LABEL
    file = await update.message.photo[-1].get_file()
    fname = f"label_{file.file_id[:8]}.jpg"
    draft = _get_draft(context)
    photos = draft.get("photos_label", [])
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
        await file.download_to_drive(tmp.name)
        photos.append({"name": fname, "local_path": tmp.name})
    _save_draft(context, {"photos_label": photos})
    logger.info(f"Фото этикетки получено: {fname}, всего: {len(photos)}")
    await update.message.reply_text(f"✅ Фото принято. Можно отправить ещё или нажать «Завершить загрузку».")
    return PHOTOS_LABEL


async def photos_label_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершение заведения продукта."""
    query = update.callback_query
    await query.answer()
    draft = _get_draft(context)
    logger.info(f"photos_label_done: завершение, draft keys={list(draft.keys())}")

    company_id = context.user_data.get("company_id")
    telegram_user_id = update.effective_user.id if update.effective_user else 0

    try:
        from bot.services.product_upload import save_product_with_files

        product = await save_product_with_files(company_id, telegram_user_id, draft)
        if product:
            logger.info(f"Продукт создан: id={product.id}")
    except Exception as e:
        logger.error(f"Ошибка сохранения продукта: {e}", exc_info=True)
    supplier_name = draft.get("supplier_name") or "—"
    summary = (
        f"✅ **Продукт заведён на проработку**\n\n"
        f"• Поставщик: {supplier_name}\n"
        f"• Номенклатура: {draft.get('supplier_nomenclature', '—')}\n"
        f"• Ед. изм: {draft.get('unit', '—')}\n"
        f"• Цена: {draft.get('price', '—')} ₽\n"
        f"• НДС: {draft.get('vat_rate', '—')}\n"
        f"• Сертификатов: {len(draft.get('certs', []))}\n"
        f"• Фото продукта: {len(draft.get('photos_product', []))}\n"
        f"• Фото этикетки: {len(draft.get('photos_label', []))}"
    )
    await query.edit_message_text(summary, parse_mode="Markdown")

    # Очистка черновика
    context.user_data.pop(_get_draft_key(context), None)
    return ConversationHandler.END


async def cancel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена заведения продукта."""
    logger.info(f"cancel_registration: user_id={update.effective_user.id}")
    context.user_data.pop(_get_draft_key(context), None)
    await update.message.reply_text("❌ Заведение продукта отменено.")
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
                CallbackQueryHandler(supplier_selected, pattern="^supplier:"),
            ],
            SUPPLIER_NEW: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, supplier_new_received),
            ],
            NOMENCLATURE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, nomenclature_received),
            ],
            UNIT: [
                CallbackQueryHandler(unit_selected, pattern="^unit:"),
            ],
            PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, price_received),
            ],
            VAT: [
                CallbackQueryHandler(vat_selected, pattern="^vat:"),
            ],
            CERTS: [
                MessageHandler(
                    (filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND,
                    certs_uploaded,
                ),
                CallbackQueryHandler(certs_done, pattern="^upload_done$"),
            ],
            PHOTOS_PRODUCT: [
                MessageHandler(filters.PHOTO & ~filters.COMMAND, photos_product_uploaded),
                CallbackQueryHandler(photos_product_done, pattern="^upload_done$"),
            ],
            PHOTOS_LABEL: [
                MessageHandler(filters.PHOTO & ~filters.COMMAND, photos_label_uploaded),
                CallbackQueryHandler(photos_label_done, pattern="^upload_done$"),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^/cancel$"), cancel_registration),
            MessageHandler(
                filters.Regex("^(📦 Заведение продукта на проработку|🔄 Процесс проработки)$"),
                cancel_registration,
            ),
        ],
        name="product_registration",
    )
