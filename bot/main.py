"""Точка входа бота."""
import sys

from loguru import logger

from bot.config import BOT_TOKEN
from bot.handlers.development import show_development_menu
from bot.handlers.product_registration import get_product_registration_handler
from bot.handlers.start import cmd_start, main_menu
from bot.models.base import init_db


def setup_logging() -> None:
    """Настройка loguru."""
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level="DEBUG",
    )


async def post_init(application) -> None:
    """Действия после инициализации бота."""
    logger.info("Инициализация базы данных")
    await init_db()


def main() -> None:
    """Запуск бота."""
    setup_logging()
    logger.info("Запуск бота", bot_token_prefix=BOT_TOKEN[:10] + "..." if BOT_TOKEN else "NOT SET")

    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не задан. Укажите в .env")
        sys.exit(1)

    from telegram.ext import Application, CommandHandler, MessageHandler, filters

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Порядок важен: более специфичные обработчики первыми
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(
        MessageHandler(
            filters.Regex("^🔄 Процесс проработки$"),
            show_development_menu,
        )
    )
    application.add_handler(get_product_registration_handler())
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            main_menu,
        )
    )

    logger.info("Бот запущен (polling)")
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
