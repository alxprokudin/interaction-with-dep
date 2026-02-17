"""Главное меню и кнопки."""
from loguru import logger

from telegram import ReplyKeyboardMarkup, KeyboardButton


def get_main_menu_keyboard(is_superadmin: bool = False) -> ReplyKeyboardMarkup:
    """Главное меню для пользователей, состоящих в компании."""
    logger.debug(f"get_main_menu_keyboard called, is_superadmin={is_superadmin}")
    keyboard = [
        [KeyboardButton("📦 Заведение продукта на проработку")],
        [KeyboardButton("➕ Добавить поставщика"), KeyboardButton("✅ Завершить заявку")],
        [KeyboardButton("🔄 Проработки (Заявки)"), KeyboardButton("📋 Заявки в работе")],
        [KeyboardButton("⚙️ Настройки")],
    ]
    if is_superadmin:
        keyboard.append([KeyboardButton("🔧 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_registration_keyboard(is_superadmin: bool = False) -> ReplyKeyboardMarkup:
    """Клавиатура для новых пользователей без компании."""
    logger.debug(f"get_registration_keyboard called, is_superadmin={is_superadmin}")
    keyboard = [
        [KeyboardButton("🔐 Присоединиться к компании")],
    ]
    if is_superadmin:
        keyboard.append([KeyboardButton("🔧 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
