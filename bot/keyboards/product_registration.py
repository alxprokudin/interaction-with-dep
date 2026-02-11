"""Клавиатуры для процесса заведения продукта."""
from loguru import logger

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


# Единицы измерения
UNITS = ["шт", "кг", "упак"]

# Ставки НДС
VAT_RATES = ["10%", "22%"]


def get_supplier_keyboard(suppliers: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Клавиатура выбора поставщика из списка."""
    logger.debug(f"get_supplier_keyboard called with: suppliers_count={len(suppliers)}")
    buttons = [
        [InlineKeyboardButton(name, callback_data=f"supplier:{supplier_id}")]
        for supplier_id, name in suppliers
    ]
    buttons.append([InlineKeyboardButton("➕ Добавить нового поставщика", callback_data="supplier:new")])
    return InlineKeyboardMarkup(buttons)


def get_unit_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора единицы измерения."""
    logger.debug("get_unit_keyboard called")
    buttons = [
        [
            InlineKeyboardButton("шт", callback_data="unit:шт"),
            InlineKeyboardButton("кг", callback_data="unit:кг"),
            InlineKeyboardButton("упак", callback_data="unit:упак"),
        ]
    ]
    return InlineKeyboardMarkup(buttons)


def get_vat_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора ставки НДС."""
    logger.debug("get_vat_keyboard called")
    buttons = [
        [
            InlineKeyboardButton("10%", callback_data="vat:10%"),
            InlineKeyboardButton("22%", callback_data="vat:22%"),
        ]
    ]
    return InlineKeyboardMarkup(buttons)


def get_skip_or_done_keyboard(skip_text: str = "⏭ Пропустить", done_text: str = "✅ Готово") -> InlineKeyboardMarkup:
    """Клавиатура для пропуска шага или завершения загрузки."""
    buttons = [[InlineKeyboardButton(skip_text, callback_data="skip")]]
    return InlineKeyboardMarkup(buttons)


def get_upload_done_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура завершения загрузки файлов."""
    buttons = [[InlineKeyboardButton("✅ Завершить загрузку", callback_data="upload_done")]]
    return InlineKeyboardMarkup(buttons)
