"""Процесс проработки — заглушка."""
from loguru import logger

from telegram import Update
from telegram.ext import ContextTypes


async def show_development_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать меню процесса проработки (заглушка)."""
    logger.info(f"show_development_menu called: user_id={update.effective_user.id}")
    await update.message.reply_text(
        "🔄 **Процесс проработки**\n\n"
        "Этот блок будет реализован в следующей итерации.",
        parse_mode="Markdown",
    )
    logger.debug("Меню проработки отправлено")
