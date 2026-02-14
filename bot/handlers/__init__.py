"""Обработчики бота."""
from bot.handlers.admin import get_admin_handlers
from bot.handlers.registration import get_registration_handler
from bot.handlers.settings import get_settings_handlers
from bot.handlers.start import cmd_start, main_menu
from bot.handlers.product_registration import get_product_registration_handler
from bot.handlers.superadmin import get_superadmin_handler

__all__ = [
    "get_admin_handlers",
    "get_registration_handler",
    "get_settings_handlers",
    "get_superadmin_handler",
    "cmd_start",
    "main_menu",
    "get_product_registration_handler",
]
