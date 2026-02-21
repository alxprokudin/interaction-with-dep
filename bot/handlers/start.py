"""Старт и главное меню."""
from __future__ import annotations

from loguru import logger

from telegram import Update
from telegram.ext import ContextTypes

from sqlalchemy import select

from bot.config import SUPERADMIN_IDS
from bot.keyboards.main import get_main_menu_keyboard, get_registration_keyboard, get_webapp_inline_keyboard
from bot.models import Company, User
from bot.models.base import async_session_factory


async def get_user_companies(telegram_id: int) -> list[tuple[int, str]]:
    """Получить список компаний пользователя."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(User.company_id, Company.name)
            .join(Company, User.company_id == Company.id)
            .where(User.telegram_id == telegram_id)
            .order_by(Company.name)
        )
        return [(row[0], row[1]) for row in result.all()]


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик /start."""
    user = update.effective_user
    logger.info(f"cmd_start called: user_id={user.id}, username={user.username}")

    # Проверяем, есть ли у пользователя компании
    companies = await get_user_companies(user.id)

    if not companies:
        # Новый пользователь — нужно присоединиться к компании
        logger.debug("Пользователь не состоит ни в одной компании")
        is_superadmin = user.id in SUPERADMIN_IDS
        
        if is_superadmin:
            msg = (
                "👋 Добро пожаловать в **WorkFlow Hub**!\n\n"
                "Вы суперадмин. Используйте **Админ-панель** для создания компании."
            )
        else:
            msg = (
                "👋 Добро пожаловать в **WorkFlow Hub**!\n\n"
                "Для начала работы вам нужно присоединиться к компании.\n"
                "Попросите код приглашения у администратора вашей компании."
            )
        
        await update.message.reply_text(
            msg,
            parse_mode="Markdown",
            reply_markup=get_registration_keyboard(is_superadmin=is_superadmin),
        )
        return

    # Устанавливаем активную компанию (первую, если не выбрана)
    active_company_id = context.user_data.get("active_company_id")
    active_company_name = None

    for cid, cname in companies:
        if cid == active_company_id:
            active_company_name = cname
            break

    if not active_company_name:
        # Выбираем первую компанию по умолчанию
        active_company_id = companies[0][0]
        active_company_name = companies[0][1]
        context.user_data["active_company_id"] = active_company_id

    logger.debug(f"Активная компания: {active_company_id} ({active_company_name})")

    # Проверяем, является ли пользователь суперадмином
    is_superadmin = user.id in SUPERADMIN_IDS

    # Показываем главное меню
    greeting = f"👋 Добро пожаловать, {user.first_name}!\n\n"
    if len(companies) > 1:
        greeting += f"🏢 Текущая компания: **{active_company_name}**\n"
        greeting += f"_(у вас доступ к {len(companies)} компаниям)_\n\n"
    else:
        greeting += f"🏢 Компания: **{active_company_name}**\n\n"

    greeting += "Выберите действие:"

    await update.message.reply_text(
        greeting,
        parse_mode="Markdown",
        reply_markup=get_main_menu_keyboard(is_superadmin=is_superadmin),
    )
    
    await update.message.reply_text(
        "💡 Или откройте веб-приложение:",
        reply_markup=get_webapp_inline_keyboard(),
    )
    logger.debug("Стартовое сообщение отправлено")


async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка выбора из главного меню и прочий текст."""
    text = update.message.text or ""
    user_id = update.effective_user.id
    logger.debug(f"main_menu called with: text={text}")

    is_superadmin = user_id in SUPERADMIN_IDS

    if text == "🔄 Проработки (Заявки)":
        from bot.handlers.development import show_development_menu

        await show_development_menu(update, context)
    elif text == "🔧 Админ-панель":
        # Обрабатывается в superadmin ConversationHandler
        pass
    else:
        # Проверяем, есть ли у пользователя компании
        companies = await get_user_companies(user_id)
        if not companies:
            await update.message.reply_text(
                "Для начала работы присоединитесь к компании.",
                reply_markup=get_registration_keyboard(is_superadmin=is_superadmin),
            )
        else:
            await update.message.reply_text(
                "Используйте кнопки меню или команду /start.",
                reply_markup=get_main_menu_keyboard(is_superadmin=is_superadmin),
            )
