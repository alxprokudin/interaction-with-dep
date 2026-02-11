"""Старт и главное меню."""
from loguru import logger

from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards.main import get_main_menu_keyboard


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик /start."""
    logger.info(f"cmd_start called: user_id={update.effective_user.id}, username={update.effective_user.username}")
    await update.message.reply_text(
        "👋 Добро пожаловать!\n\n"
        "Выберите действие:",
        reply_markup=get_main_menu_keyboard(),
    )
    logger.debug("Стартовое сообщение отправлено")


async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка выбора из главного меню (Процесс проработки) и прочий текст."""
    text = update.message.text or ""
    logger.debug(f"main_menu called with: text={text}")

    if text == "🔄 Процесс проработки":
        from bot.handlers.development import show_development_menu

        await show_development_menu(update, context)
    else:
        await update.message.reply_text(
            "Используйте кнопки меню или команду /start.",
            reply_markup=get_main_menu_keyboard(),
        )
