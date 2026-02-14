"""Точка входа бота."""
import sys

from loguru import logger

from bot.config import BOT_TOKEN, get_env
from bot.handlers.admin import get_admin_handlers
from bot.handlers.development import show_development_menu
from bot.handlers.group_events import get_group_events_handler
from bot.handlers.product_registration import get_product_registration_handler
from bot.handlers.registration import get_registration_handler
from bot.handlers.supplier_add import get_supplier_add_handler
from bot.handlers.settings import get_settings_handlers
from bot.handlers.start import cmd_start, main_menu
from bot.handlers.superadmin import get_superadmin_handler
# from bot.handlers.supplier_search import get_supplier_search_handler  # Временно отключен
from bot.models.base import init_db


# Интервал проверки ответов на письма (в минутах)
EMAIL_CHECK_INTERVAL = int(get_env("EMAIL_CHECK_INTERVAL", "5"))


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
    
    # Запускаем периодическую проверку ответов на письма
    await setup_email_reply_checker(application)


async def setup_email_reply_checker(application) -> None:
    """Настроить периодическую проверку ответов на письма."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from bot.services.reply_processor import check_email_replies_job
    
    # Проверяем, настроен ли IMAP
    imap_user = get_env("GMAIL_IMAP_USER", "")
    imap_password = get_env("GMAIL_IMAP_PASSWORD", "")
    
    if not imap_user or not imap_password:
        logger.warning("IMAP не настроен — проверка ответов на письма отключена")
        return
    
    scheduler = AsyncIOScheduler()
    
    # Добавляем задачу проверки ответов
    scheduler.add_job(
        check_email_replies_job,
        "interval",
        minutes=EMAIL_CHECK_INTERVAL,
        args=[application.bot],
        id="check_email_replies",
        name="Проверка ответов на письма",
        replace_existing=True,
    )
    
    scheduler.start()
    logger.info(f"Запущена проверка ответов на письма каждые {EMAIL_CHECK_INTERVAL} минут")


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
    # 1. Команды
    application.add_handler(CommandHandler("start", cmd_start))

    # 2. ConversationHandler'ы (должны быть перед общими MessageHandler'ами)
    application.add_handler(get_superadmin_handler())  # /admin
    application.add_handler(get_registration_handler())
    application.add_handler(get_product_registration_handler())
    application.add_handler(get_supplier_add_handler())  # Добавление поставщика без привязки к заявке
    # application.add_handler(get_supplier_search_handler())  # Поиск поставщиков — временно отключен

    # 3. Callback-хэндлеры для админов (одобрение/отклонение заявок)
    for handler in get_admin_handlers():
        application.add_handler(handler)

    # 4. Хэндлеры настроек
    for handler in get_settings_handlers():
        application.add_handler(handler)

    # 5. Конкретные кнопки меню
    application.add_handler(
        MessageHandler(
            filters.Regex("^🔄 Процесс проработки$"),
            show_development_menu,
        )
    )

    # 6. Общий fallback для текста
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            main_menu,
        )
    )

    # 7. Обработка событий групп (добавление/удаление бота)
    application.add_handler(get_group_events_handler())

    logger.info("Бот запущен (polling)")
    application.run_polling(allowed_updates=["message", "callback_query", "my_chat_member"])


if __name__ == "__main__":
    main()
