"""Настройки пользователя и переключение компаний."""
from __future__ import annotations

from loguru import logger

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from sqlalchemy import select

from bot.models import Company, User
from bot.models.base import async_session_factory
from bot.keyboards.main import get_main_menu_keyboard


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


async def show_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать меню настроек."""
    user = update.effective_user
    logger.debug(f"show_settings_menu called: user_id={user.id}")

    companies = await get_user_companies(user.id)
    active_company_id = context.user_data.get("active_company_id")

    # Находим название активной компании
    active_company_name = None
    for cid, cname in companies:
        if cid == active_company_id:
            active_company_name = cname
            break

    if not active_company_name and companies:
        active_company_id = companies[0][0]
        active_company_name = companies[0][1]
        context.user_data["active_company_id"] = active_company_id

    buttons = []

    if len(companies) > 1:
        buttons.append([
            InlineKeyboardButton("🔄 Сменить компанию", callback_data="settings:switch_company")
        ])

    buttons.append([
        InlineKeyboardButton("🔐 Присоединиться к другой компании", callback_data="settings:join_company")
    ])

    keyboard = InlineKeyboardMarkup(buttons)

    text = (
        f"⚙️ **Настройки**\n\n"
        f"🏢 Текущая компания: **{active_company_name or 'Не выбрана'}**\n"
        f"📊 Доступно компаний: {len(companies)}"
    )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def show_company_switcher(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать список компаний для переключения."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    logger.debug(f"show_company_switcher called: user_id={user.id}")

    companies = await get_user_companies(user.id)
    active_company_id = context.user_data.get("active_company_id")

    buttons = []
    for company_id, company_name in companies:
        prefix = "✅ " if company_id == active_company_id else ""
        buttons.append([
            InlineKeyboardButton(
                f"{prefix}{company_name}",
                callback_data=f"switch:{company_id}",
            )
        ])

    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="settings:back")])

    keyboard = InlineKeyboardMarkup(buttons)

    await query.edit_message_text(
        "🔄 **Выберите компанию**\n\n"
        "Нажмите на компанию, чтобы переключиться:",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def switch_company(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Переключиться на выбранную компанию."""
    query = update.callback_query
    await query.answer()

    company_id = int(query.data.split(":")[1])
    user = update.effective_user
    logger.info(f"switch_company: user_id={user.id}, company_id={company_id}")

    # Проверяем, что пользователь действительно состоит в этой компании
    companies = await get_user_companies(user.id)
    company_name = None
    for cid, cname in companies:
        if cid == company_id:
            company_name = cname
            break

    if not company_name:
        await query.edit_message_text("❌ Компания не найдена или у вас нет доступа.")
        return

    context.user_data["active_company_id"] = company_id
    logger.debug(f"Активная компания изменена на: {company_id}")

    await query.edit_message_text(
        f"✅ Вы переключились на компанию «{company_name}»\n\n"
        f"Используйте меню ниже для продолжения работы.",
        parse_mode="Markdown",
    )

    # Отправляем главное меню
    await context.bot.send_message(
        chat_id=user.id,
        text="Выберите действие:",
        reply_markup=get_main_menu_keyboard(),
    )


async def handle_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка callback-ов настроек."""
    query = update.callback_query
    data = query.data

    if data == "settings:switch_company":
        await show_company_switcher(update, context)
    elif data == "settings:join_company":
        await query.answer()
        await query.edit_message_text(
            "🔐 **Присоединение к компании**\n\n"
            "Чтобы присоединиться к новой компании, нажмите кнопку "
            "«🔐 Присоединиться к компании» в главном меню или отправьте код приглашения."
        )
    elif data == "settings:back":
        await query.answer()
        await query.delete_message()


def get_settings_handlers() -> list:
    """Получить хэндлеры настроек."""
    return [
        MessageHandler(
            filters.Regex(r"^⚙️ Настройки$"),
            show_settings_menu,
        ),
        CallbackQueryHandler(
            handle_settings_callback,
            pattern=r"^settings:",
        ),
        CallbackQueryHandler(
            switch_company,
            pattern=r"^switch:\d+$",
        ),
    ]
