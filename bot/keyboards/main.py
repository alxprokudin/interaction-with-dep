"""Главное меню и кнопки."""
from loguru import logger

from telegram import ReplyKeyboardMarkup, KeyboardButton


def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Главное меню: две кнопки — заведение продукта и процесс проработки."""
    logger.debug("get_main_menu_keyboard called")
    keyboard = [
        [KeyboardButton("📦 Заведение продукта на проработку")],
        [KeyboardButton("🔄 Процесс проработки")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
