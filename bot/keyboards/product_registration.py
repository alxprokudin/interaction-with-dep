"""Клавиатуры для процесса заведения продукта."""
from loguru import logger

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton


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
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def get_vat_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора ставки НДС."""
    logger.debug("get_vat_keyboard called")
    buttons = [
        [
            InlineKeyboardButton("10%", callback_data="vat:10%"),
            InlineKeyboardButton("22%", callback_data="vat:22%"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def get_upload_keyboard(allow_skip: bool = True) -> ReplyKeyboardMarkup:
    """Reply-клавиатура для этапа загрузки файлов (до первой загрузки).
    
    Args:
        allow_skip: Если True, добавляется кнопка "Пропустить".
    """
    logger.debug(f"get_upload_keyboard called, allow_skip={allow_skip}")
    buttons = [
        [KeyboardButton("✅ Завершить загрузку")],
    ]
    if allow_skip:
        buttons.append([KeyboardButton("⏭ Пропустить")])
    buttons.append([KeyboardButton("❌ Отмена")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def get_upload_keyboard_minimal(allow_skip: bool = True) -> ReplyKeyboardMarkup:
    """Reply-клавиатура для этапа загрузки (после первого файла) — без "Завершить".
    
    "Завершить загрузку" выносится в inline-кнопку в чате.
    
    Args:
        allow_skip: Если True, добавляется кнопка "Пропустить".
    """
    logger.debug(f"get_upload_keyboard_minimal called, allow_skip={allow_skip}")
    buttons = []
    if allow_skip:
        buttons.append([KeyboardButton("⏭ Пропустить")])
    buttons.append([KeyboardButton("❌ Отмена")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def get_finish_upload_inline_keyboard() -> InlineKeyboardMarkup:
    """Inline-кнопка 'Завершить загрузку' для отображения в чате."""
    logger.debug("get_finish_upload_inline_keyboard called")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Завершить загрузку", callback_data="upload_done")]
    ])


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Reply-клавиатура с кнопкой отмены для шагов с текстовым вводом."""
    logger.debug("get_cancel_keyboard called")
    return ReplyKeyboardMarkup(
        [[KeyboardButton("❌ Отмена")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
