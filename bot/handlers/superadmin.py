"""Панель суперадминистратора."""
from __future__ import annotations

import secrets

from loguru import logger

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from sqlalchemy import func, select

from bot.config import SUPERADMIN_IDS
from bot.models import Company, CompanyIntegrations, Position, User, UserRole
from bot.models.base import async_session_factory
from bot.models.telegram_group import TelegramGroup
from bot.models.notification_settings import NotificationPosition
from bot.services.google_sheets import google_sheets_service


# States для ConversationHandler
(
    SA_MAIN_MENU,
    SA_COMPANIES_LIST,
    SA_COMPANY_DETAIL,
    SA_CREATE_COMPANY_NAME,
    SA_POSITIONS_SELECT_COMPANY,
    SA_POSITIONS_LIST,
    SA_ADD_POSITION_NAME,
    SA_USERS_SELECT_COMPANY,
    SA_USERS_LIST,
    SA_USER_DETAIL,
    SA_USER_CHANGE_POSITION,
    SA_INTEGRATIONS,
    SA_ENTER_SHEET_ID,
    SA_ENTER_FOLDER_ID,
    SA_GROUPS_SELECT_COMPANY,
    SA_GROUPS_LIST,
    SA_NOTIFY_POSITIONS_SELECT_COMPANY,
    SA_NOTIFY_POSITIONS_LIST,
) = range(18)


def is_superadmin(telegram_id: int) -> bool:
    """Проверить, является ли пользователь суперадмином."""
    return telegram_id in SUPERADMIN_IDS


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /admin — главное меню суперадмина."""
    user_id = update.effective_user.id
    logger.debug(f"cmd_admin called by user_id={user_id}")

    if not is_superadmin(user_id):
        await update.message.reply_text("⛔ У вас нет доступа к панели администратора.")
        return ConversationHandler.END

    return await show_admin_menu(update, context)


async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показать главное меню админки."""
    keyboard = [
        [InlineKeyboardButton("🏢 Компании", callback_data="sa:companies")],
        [InlineKeyboardButton("👔 Должности", callback_data="sa:positions")],
        [InlineKeyboardButton("👥 Пользователи", callback_data="sa:users")],
        [InlineKeyboardButton("📢 Группы уведомлений", callback_data="sa:groups")],
        [InlineKeyboardButton("🔔 Должности для уведомлений", callback_data="sa:notify_positions")],
        [InlineKeyboardButton("❌ Закрыть", callback_data="sa:close")],
    ]

    text = (
        "🔧 <b>Панель суперадминистратора</b>\n\n"
        "Выберите раздел для управления:"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )

    return SA_MAIN_MENU


# ============== КОМПАНИИ ==============


async def show_companies_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показать список компаний."""
    query = update.callback_query
    await query.answer()

    async with async_session_factory() as session:
        result = await session.execute(
            select(Company).order_by(Company.created_at.desc())
        )
        companies = result.scalars().all()

    keyboard = []
    for company in companies:
        keyboard.append([
            InlineKeyboardButton(
                f"🏢 {company.name}",
                callback_data=f"sa:company:{company.id}"
            )
        ])

    keyboard.append([InlineKeyboardButton("➕ Создать компанию", callback_data="sa:create_company")])
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="sa:back_main")])

    text = f"🏢 <b>Компании</b> ({len(companies)})\n\nВыберите компанию для просмотра:"

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )

    return SA_COMPANIES_LIST


async def show_company_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показать детали компании."""
    query = update.callback_query
    await query.answer()

    company_id = int(query.data.split(":")[2])
    context.user_data["sa_company_id"] = company_id

    async with async_session_factory() as session:
        # Компания
        result = await session.execute(
            select(Company).where(Company.id == company_id)
        )
        company = result.scalar_one_or_none()

        if not company:
            await query.edit_message_text("❌ Компания не найдена.")
            return SA_COMPANIES_LIST

        # Статистика
        users_count = await session.execute(
            select(func.count()).select_from(User).where(User.company_id == company_id)
        )
        users_count = users_count.scalar()

        positions_count = await session.execute(
            select(func.count()).select_from(Position).where(
                Position.company_id == company_id, Position.is_active == True
            )
        )
        positions_count = positions_count.scalar()

        admins = await session.execute(
            select(User).where(
                User.company_id == company_id, User.role == UserRole.ADMIN
            )
        )
        admins = admins.scalars().all()

    admins_text = ", ".join([a.full_name or f"ID:{a.telegram_id}" for a in admins]) or "Нет"

    text = (
        f"🏢 <b>{company.name}</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"  👥 Пользователей: {users_count}\n"
        f"  👔 Должностей: {positions_count}\n"
        f"  👑 Админы: {admins_text}\n\n"
        f"🔑 <b>Код приглашения:</b>\n"
        f"<code>{company.invite_code}</code>\n\n"
        f"📅 Создана: {company.created_at.strftime('%d.%m.%Y')}"
    )

    keyboard = [
        [InlineKeyboardButton("🔄 Сбросить код", callback_data=f"sa:reset_code:{company_id}")],
        [InlineKeyboardButton("👔 Должности", callback_data=f"sa:company_positions:{company_id}")],
        [InlineKeyboardButton("👥 Пользователи", callback_data=f"sa:company_users:{company_id}")],
        [InlineKeyboardButton("🔗 Интеграции (Google)", callback_data=f"sa:integrations:{company_id}")],
        [InlineKeyboardButton("⬅️ К списку", callback_data="sa:companies")],
    ]

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )

    return SA_COMPANY_DETAIL


async def reset_invite_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сбросить код приглашения компании."""
    query = update.callback_query
    await query.answer()

    company_id = int(query.data.split(":")[2])

    async with async_session_factory() as session:
        result = await session.execute(
            select(Company).where(Company.id == company_id)
        )
        company = result.scalar_one_or_none()

        if company:
            company.invite_code = secrets.token_urlsafe(8)
            await session.commit()
            logger.info(f"Код приглашения компании {company_id} сброшен")

    # Возвращаемся к деталям компании
    context.user_data["sa_company_id"] = company_id
    # Имитируем callback для показа деталей
    query.data = f"sa:company:{company_id}"
    return await show_company_detail(update, context)


async def start_create_company(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начать создание компании."""
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "🏢 <b>Создание компании</b>\n\n"
        "Введите название новой компании:",
        parse_mode="HTML",
    )

    return SA_CREATE_COMPANY_NAME


async def create_company_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получено название компании — создаём."""
    company_name = update.message.text.strip()

    if len(company_name) < 2:
        await update.message.reply_text("❌ Название слишком короткое. Попробуйте ещё раз:")
        return SA_CREATE_COMPANY_NAME

    async with async_session_factory() as session:
        # Проверяем уникальность
        existing = await session.execute(
            select(Company).where(Company.name == company_name)
        )
        if existing.scalar_one_or_none():
            await update.message.reply_text(
                f"❌ Компания «{company_name}» уже существует. Введите другое название:"
            )
            return SA_CREATE_COMPANY_NAME

        # Создаём компанию
        new_company = Company(name=company_name)
        session.add(new_company)
        await session.commit()
        await session.refresh(new_company)

        logger.info(f"Создана компания: {company_name}, id={new_company.id}")

    keyboard = [
        [InlineKeyboardButton("🏢 К списку компаний", callback_data="sa:companies")],
        [InlineKeyboardButton("⬅️ Главное меню", callback_data="sa:back_main")],
    ]

    await update.message.reply_text(
        f"✅ <b>Компания создана!</b>\n\n"
        f"🏢 Название: {company_name}\n"
        f"🔑 Код приглашения: <code>{new_company.invite_code}</code>\n\n"
        f"Теперь добавьте должности и назначьте админа.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )

    return SA_MAIN_MENU


# ============== ДОЛЖНОСТИ ==============


async def show_positions_companies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Выбор компании для управления должностями."""
    query = update.callback_query
    await query.answer()

    async with async_session_factory() as session:
        result = await session.execute(
            select(Company).order_by(Company.name)
        )
        companies = result.scalars().all()

    keyboard = []
    for company in companies:
        keyboard.append([
            InlineKeyboardButton(
                f"🏢 {company.name}",
                callback_data=f"sa:pos_company:{company.id}"
            )
        ])

    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="sa:back_main")])

    await query.edit_message_text(
        "👔 <b>Управление должностями</b>\n\nВыберите компанию:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )

    return SA_POSITIONS_SELECT_COMPANY


async def _render_positions_list(query, context: ContextTypes.DEFAULT_TYPE, company_id: int) -> int:
    """Вспомогательная функция для отрисовки списка должностей."""
    context.user_data["sa_positions_company_id"] = company_id

    async with async_session_factory() as session:
        company_result = await session.execute(
            select(Company).where(Company.id == company_id)
        )
        company = company_result.scalar_one_or_none()

        positions_result = await session.execute(
            select(Position)
            .where(Position.company_id == company_id)
            .order_by(Position.sort_order)
        )
        positions = positions_result.scalars().all()

    if not company:
        await query.edit_message_text("❌ Компания не найдена.")
        return SA_POSITIONS_SELECT_COMPANY

    keyboard = []
    for pos in positions:
        status = "✅" if pos.is_active else "❌"
        keyboard.append([
            InlineKeyboardButton(
                f"{status} {pos.name}",
                callback_data=f"sa:toggle_pos:{pos.id}"
            )
        ])

    keyboard.append([InlineKeyboardButton("➕ Добавить должность", callback_data=f"sa:add_position:{company_id}")])
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="sa:positions")])

    text = (
        f"👔 <b>Должности: {company.name}</b>\n\n"
        f"✅ — активная, ❌ — неактивная\n"
        f"Нажмите на должность, чтобы включить/выключить:"
    )

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )

    return SA_POSITIONS_LIST


async def show_positions_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показать список должностей компании."""
    query = update.callback_query
    await query.answer()

    company_id = int(query.data.split(":")[2])

    return await _render_positions_list(query, context, company_id)


async def _show_positions_list_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE, company_id: int) -> int:
    """Показать список должностей по company_id (без парсинга query.data)."""
    query = update.callback_query
    # answer уже был вызван ранее
    return await _render_positions_list(query, context, company_id)


async def toggle_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Переключить активность должности."""
    query = update.callback_query
    await query.answer()

    position_id = int(query.data.split(":")[2])
    company_id = None

    async with async_session_factory() as session:
        result = await session.execute(
            select(Position).where(Position.id == position_id)
        )
        position = result.scalar_one_or_none()

        if position:
            position.is_active = not position.is_active
            await session.commit()
            company_id = position.company_id
            logger.info(f"Должность {position_id} is_active={position.is_active}")

    # Возвращаемся к списку должностей (используем сохранённый company_id)
    final_company_id = context.user_data.get("sa_positions_company_id", company_id)
    if final_company_id:
        return await _render_positions_list(query, context, final_company_id)
    else:
        await query.edit_message_text("❌ Ошибка: компания не найдена.")
        return SA_POSITIONS_SELECT_COMPANY


async def start_add_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начать добавление должности."""
    query = update.callback_query
    await query.answer()

    company_id = int(query.data.split(":")[2])
    context.user_data["sa_add_position_company_id"] = company_id

    await query.edit_message_text(
        "👔 <b>Добавление должности</b>\n\n"
        "Введите название должности:",
        parse_mode="HTML",
    )

    return SA_ADD_POSITION_NAME


async def add_position_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получено название должности — создаём."""
    position_name = update.message.text.strip()
    company_id = context.user_data.get("sa_add_position_company_id")

    if not company_id:
        await update.message.reply_text("❌ Ошибка: компания не выбрана.")
        return await show_admin_menu(update, context)

    if len(position_name) < 2:
        await update.message.reply_text("❌ Название слишком короткое. Попробуйте ещё раз:")
        return SA_ADD_POSITION_NAME

    async with async_session_factory() as session:
        # Получаем max sort_order
        max_order = await session.execute(
            select(func.max(Position.sort_order)).where(Position.company_id == company_id)
        )
        max_order = max_order.scalar() or 0

        new_position = Position(
            company_id=company_id,
            name=position_name,
            sort_order=max_order + 1,
        )
        session.add(new_position)
        await session.commit()

        logger.info(f"Создана должность: {position_name}, company_id={company_id}")

    keyboard = [
        [InlineKeyboardButton("👔 К должностям", callback_data=f"sa:pos_company:{company_id}")],
        [InlineKeyboardButton("⬅️ Главное меню", callback_data="sa:back_main")],
    ]

    await update.message.reply_text(
        f"✅ Должность «{position_name}» добавлена!",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    return SA_POSITIONS_LIST


# ============== ПОЛЬЗОВАТЕЛИ ==============


async def show_users_companies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Выбор компании для управления пользователями."""
    query = update.callback_query
    await query.answer()

    async with async_session_factory() as session:
        result = await session.execute(
            select(Company).order_by(Company.name)
        )
        companies = result.scalars().all()

    keyboard = []
    for company in companies:
        keyboard.append([
            InlineKeyboardButton(
                f"🏢 {company.name}",
                callback_data=f"sa:users_company:{company.id}"
            )
        ])

    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="sa:back_main")])

    await query.edit_message_text(
        "👥 <b>Управление пользователями</b>\n\nВыберите компанию:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )

    return SA_USERS_SELECT_COMPANY


async def show_users_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показать список пользователей компании."""
    query = update.callback_query
    await query.answer()

    company_id = int(query.data.split(":")[2])
    context.user_data["sa_users_company_id"] = company_id

    async with async_session_factory() as session:
        company_result = await session.execute(
            select(Company).where(Company.id == company_id)
        )
        company = company_result.scalar_one_or_none()

        users_result = await session.execute(
            select(User)
            .where(User.company_id == company_id)
            .order_by(User.role, User.full_name)
        )
        users = users_result.scalars().all()

    if not company:
        await query.edit_message_text("❌ Компания не найдена.")
        return SA_USERS_SELECT_COMPANY

    keyboard = []
    for user in users:
        role_icon = "👑" if user.role == UserRole.ADMIN else "👤"
        name = user.full_name or f"ID:{user.telegram_id}"
        keyboard.append([
            InlineKeyboardButton(
                f"{role_icon} {name}",
                callback_data=f"sa:user:{user.id}"
            )
        ])

    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="sa:users")])

    text = (
        f"👥 <b>Пользователи: {company.name}</b> ({len(users)})\n\n"
        f"👑 — админ, 👤 — сотрудник\n"
        f"Нажмите на пользователя для управления:"
    )

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )

    return SA_USERS_LIST


async def show_user_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показать детали пользователя."""
    query = update.callback_query
    await query.answer()

    user_id = int(query.data.split(":")[2])

    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            await query.edit_message_text("❌ Пользователь не найден.")
            return SA_USERS_LIST

        # Получаем должность
        position_name = None
        if user.position_id:
            pos_result = await session.execute(
                select(Position).where(Position.id == user.position_id)
            )
            position = pos_result.scalar_one_or_none()
            if position:
                position_name = position.name

        company_id = user.company_id

    role_text = "👑 Админ" if user.role == UserRole.ADMIN else "👤 Сотрудник"
    position_text = position_name or "Не назначена"

    text = (
        f"👤 <b>{user.full_name or 'Без имени'}</b>\n\n"
        f"🆔 Telegram ID: <code>{user.telegram_id}</code>\n"
        f"🎭 Роль: {role_text}\n"
        f"👔 Должность: {position_text}\n"
        f"📅 Добавлен: {user.created_at.strftime('%d.%m.%Y')}"
    )

    # Кнопки переключения роли
    if user.role == UserRole.ADMIN:
        role_btn = InlineKeyboardButton("👤 Сделать сотрудником", callback_data=f"sa:demote:{user_id}")
    else:
        role_btn = InlineKeyboardButton("👑 Сделать админом", callback_data=f"sa:promote:{user_id}")

    keyboard = [
        [role_btn],
        [InlineKeyboardButton("👔 Изменить должность", callback_data=f"sa:change_pos:{user_id}")],
        [InlineKeyboardButton("⬅️ К списку", callback_data=f"sa:users_company:{company_id}")],
    ]

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )

    return SA_USER_DETAIL


async def change_user_role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Изменить роль пользователя."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    action = parts[1]  # promote или demote
    user_id = int(parts[2])

    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            await query.edit_message_text("❌ Пользователь не найден.")
            return SA_USERS_LIST

        if action == "promote":
            user.role = UserRole.ADMIN
            logger.info(f"Пользователь {user_id} повышен до ADMIN")
        else:
            user.role = UserRole.EMPLOYEE
            logger.info(f"Пользователь {user_id} понижен до EMPLOYEE")
        await session.commit()

        # Получаем должность
        position_name = None
        if user.position_id:
            pos_result = await session.execute(
                select(Position).where(Position.id == user.position_id)
            )
            position = pos_result.scalar_one_or_none()
            if position:
                position_name = position.name

        company_id = user.company_id

        # Показываем обновлённую карточку
        role_text = "👑 Админ" if user.role == UserRole.ADMIN else "👤 Сотрудник"
        position_text = position_name or "Не назначена"

        text = (
            f"👤 <b>{user.full_name or 'Без имени'}</b>\n\n"
            f"🆔 Telegram ID: <code>{user.telegram_id}</code>\n"
            f"🎭 Роль: {role_text}\n"
            f"👔 Должность: {position_text}\n"
            f"📅 Добавлен: {user.created_at.strftime('%d.%m.%Y')}"
        )

        if user.role == UserRole.ADMIN:
            role_btn = InlineKeyboardButton("👤 Сделать сотрудником", callback_data=f"sa:demote:{user_id}")
        else:
            role_btn = InlineKeyboardButton("👑 Сделать админом", callback_data=f"sa:promote:{user_id}")

        keyboard = [
            [role_btn],
            [InlineKeyboardButton("👔 Изменить должность", callback_data=f"sa:change_pos:{user_id}")],
            [InlineKeyboardButton("⬅️ К списку", callback_data=f"sa:users_company:{company_id}")],
        ]

        await query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )

    return SA_USER_DETAIL


async def show_user_positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показать список должностей для выбора."""
    query = update.callback_query
    await query.answer()

    user_id = int(query.data.split(":")[2])
    context.user_data["sa_change_pos_user_id"] = user_id

    async with async_session_factory() as session:
        # Получаем пользователя
        user_result = await session.execute(
            select(User).where(User.id == user_id)
        )
        user = user_result.scalar_one_or_none()

        if not user:
            await query.edit_message_text("❌ Пользователь не найден.")
            return SA_USERS_LIST

        # Получаем должности компании
        positions_result = await session.execute(
            select(Position)
            .where(Position.company_id == user.company_id, Position.is_active == True)
            .order_by(Position.sort_order)
        )
        positions = positions_result.scalars().all()

        user_name = user.full_name or f"ID:{user.telegram_id}"
        company_id = user.company_id

    keyboard = []
    for pos in positions:
        keyboard.append([
            InlineKeyboardButton(
                f"👔 {pos.name}",
                callback_data=f"sa:set_pos:{user_id}:{pos.id}"
            )
        ])

    keyboard.append([InlineKeyboardButton("🚫 Убрать должность", callback_data=f"sa:set_pos:{user_id}:0")])
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"sa:user:{user_id}")])

    text = (
        f"👔 <b>Выбор должности</b>\n\n"
        f"👤 Пользователь: {user_name}\n\n"
        f"Выберите должность:"
    )

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )

    return SA_USER_CHANGE_POSITION


async def set_user_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Установить должность пользователю."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    user_id = int(parts[2])
    position_id = int(parts[3])  # 0 = убрать должность

    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if not user:
            await query.edit_message_text("❌ Пользователь не найден.")
            return SA_USERS_LIST

        user.position_id = position_id if position_id > 0 else None
        await session.commit()

        # Получаем название должности для лога и отображения
        position_name = None
        if position_id > 0:
            pos_result = await session.execute(
                select(Position).where(Position.id == position_id)
            )
            position = pos_result.scalar_one_or_none()
            if position:
                position_name = position.name
            logger.info(f"Пользователю {user_id} назначена должность: {position_name}")
        else:
            logger.info(f"У пользователя {user_id} убрана должность")

        company_id = user.company_id

        # Показываем обновлённую карточку пользователя
        role_text = "👑 Админ" if user.role == UserRole.ADMIN else "👤 Сотрудник"
        position_text = position_name or "Не назначена"

        text = (
            f"👤 <b>{user.full_name or 'Без имени'}</b>\n\n"
            f"🆔 Telegram ID: <code>{user.telegram_id}</code>\n"
            f"🎭 Роль: {role_text}\n"
            f"👔 Должность: {position_text}\n"
            f"📅 Добавлен: {user.created_at.strftime('%d.%m.%Y')}"
        )

        if user.role == UserRole.ADMIN:
            role_btn = InlineKeyboardButton("👤 Сделать сотрудником", callback_data=f"sa:demote:{user_id}")
        else:
            role_btn = InlineKeyboardButton("👑 Сделать админом", callback_data=f"sa:promote:{user_id}")

        keyboard = [
            [role_btn],
            [InlineKeyboardButton("👔 Изменить должность", callback_data=f"sa:change_pos:{user_id}")],
            [InlineKeyboardButton("⬅️ К списку", callback_data=f"sa:users_company:{company_id}")],
        ]

        await query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )

    return SA_USER_DETAIL


# ============== ИНТЕГРАЦИИ ==============


async def show_integrations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показать настройки интеграций компании."""
    query = update.callback_query
    await query.answer()

    company_id = int(query.data.split(":")[2])
    context.user_data["sa_integrations_company_id"] = company_id

    async with async_session_factory() as session:
        # Получаем компанию
        company_result = await session.execute(
            select(Company).where(Company.id == company_id)
        )
        company = company_result.scalar_one_or_none()

        if not company:
            await query.edit_message_text("❌ Компания не найдена.")
            return SA_COMPANIES_LIST

        # Получаем интеграции
        integrations_result = await session.execute(
            select(CompanyIntegrations).where(CompanyIntegrations.company_id == company_id)
        )
        integrations = integrations_result.scalar_one_or_none()

    # Получаем email сервисного аккаунта
    service_email = await google_sheets_service.get_service_account_email()

    # Формируем текст
    text = f"🔗 <b>Интеграции: {company.name}</b>\n\n"

    if service_email:
        text += f"📧 <b>Сервисный аккаунт:</b>\n<code>{service_email}</code>\n"
        text += "<i>(предоставьте доступ этому email)</i>\n\n"
    else:
        text += "⚠️ <b>Сервисный аккаунт не настроен</b>\n"
        text += "<i>Добавьте credentials.json в корень проекта</i>\n\n"

    text += "━━━━━━━━━━━━━━━\n\n"

    # Google Sheets
    if integrations and integrations.google_sheet_id:
        status = "✅" if integrations.google_sheet_verified else "⚠️"
        sheet_name = integrations.google_sheet_name or "Таблица"
        text += f"{status} <b>Google Таблица:</b> {sheet_name}\n"
        text += f"<code>{integrations.google_sheet_id[:20]}...</code>\n\n"
    else:
        text += "❌ <b>Google Таблица:</b> не настроена\n\n"

    # Google Drive
    if integrations and integrations.google_drive_folder_id:
        status = "✅" if integrations.google_drive_verified else "⚠️"
        folder_name = integrations.google_drive_folder_name or "Папка"
        text += f"{status} <b>Google Drive:</b> {folder_name}\n"
        text += f"<code>{integrations.google_drive_folder_id[:20]}...</code>\n"
    else:
        text += "❌ <b>Google Drive:</b> не настроена\n"

    keyboard = [
        [InlineKeyboardButton("📊 Настроить таблицу", callback_data=f"sa:set_sheet:{company_id}")],
        [InlineKeyboardButton("📁 Настроить папку", callback_data=f"sa:set_folder:{company_id}")],
        [InlineKeyboardButton("🔄 Проверить доступ", callback_data=f"sa:verify_integrations:{company_id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"sa:company:{company_id}")],
    ]

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )

    return SA_INTEGRATIONS


async def start_set_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начать настройку Google Таблицы."""
    query = update.callback_query
    await query.answer()

    company_id = int(query.data.split(":")[2])
    context.user_data["sa_set_sheet_company_id"] = company_id

    await query.edit_message_text(
        "📊 <b>Настройка Google Таблицы</b>\n\n"
        "Введите ID таблицы.\n\n"
        "<i>ID можно найти в URL таблицы:\n"
        "https://docs.google.com/spreadsheets/d/<b>ID_ТАБЛИЦЫ</b>/edit</i>\n\n"
        "Или отправьте /cancel для отмены.",
        parse_mode="HTML",
    )

    return SA_ENTER_SHEET_ID


async def receive_sheet_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен ID таблицы — сохраняем и проверяем доступ."""
    sheet_id = update.message.text.strip()
    company_id = context.user_data.get("sa_set_sheet_company_id")

    if not company_id:
        await update.message.reply_text("❌ Ошибка: компания не выбрана.")
        return await show_admin_menu(update, context)

    # Извлекаем ID из URL если передана полная ссылка
    if "spreadsheets/d/" in sheet_id:
        try:
            sheet_id = sheet_id.split("spreadsheets/d/")[1].split("/")[0]
        except IndexError:
            pass

    logger.info(f"Настройка таблицы для компании {company_id}: {sheet_id}")

    # Проверяем доступ
    await update.message.reply_text("🔄 Проверяю доступ к таблице...")
    success, result = await google_sheets_service.verify_sheet_access(sheet_id)

    async with async_session_factory() as session:
        # Получаем или создаём интеграции
        integrations_result = await session.execute(
            select(CompanyIntegrations).where(CompanyIntegrations.company_id == company_id)
        )
        integrations = integrations_result.scalar_one_or_none()

        if not integrations:
            integrations = CompanyIntegrations(company_id=company_id)
            session.add(integrations)

        integrations.google_sheet_id = sheet_id
        integrations.google_sheet_verified = success
        integrations.google_sheet_name = result if success else None

        await session.commit()

    if success:
        text = f"✅ <b>Таблица подключена!</b>\n\n📊 Название: {result}"
    else:
        text = f"⚠️ <b>Таблица сохранена, но есть проблема:</b>\n\n{result}"

    keyboard = [[InlineKeyboardButton("⬅️ К интеграциям", callback_data=f"sa:integrations:{company_id}")]]

    await update.message.reply_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )

    return SA_INTEGRATIONS


async def start_set_folder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начать настройку папки Google Drive."""
    query = update.callback_query
    await query.answer()

    company_id = int(query.data.split(":")[2])
    context.user_data["sa_set_folder_company_id"] = company_id

    await query.edit_message_text(
        "📁 <b>Настройка папки Google Drive</b>\n\n"
        "Введите ID папки.\n\n"
        "<i>ID можно найти в URL папки:\n"
        "https://drive.google.com/drive/folders/<b>ID_ПАПКИ</b></i>\n\n"
        "Или отправьте /cancel для отмены.",
        parse_mode="HTML",
    )

    return SA_ENTER_FOLDER_ID


async def receive_folder_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен ID папки — сохраняем и проверяем доступ."""
    folder_id = update.message.text.strip()
    company_id = context.user_data.get("sa_set_folder_company_id")

    if not company_id:
        await update.message.reply_text("❌ Ошибка: компания не выбрана.")
        return await show_admin_menu(update, context)

    # Извлекаем ID из URL если передана полная ссылка
    if "folders/" in folder_id:
        try:
            folder_id = folder_id.split("folders/")[1].split("?")[0]
        except IndexError:
            pass

    logger.info(f"Настройка папки для компании {company_id}: {folder_id}")

    # Проверяем доступ
    await update.message.reply_text("🔄 Проверяю доступ к папке...")
    success, result = await google_sheets_service.verify_drive_folder_access(folder_id)

    async with async_session_factory() as session:
        # Получаем или создаём интеграции
        integrations_result = await session.execute(
            select(CompanyIntegrations).where(CompanyIntegrations.company_id == company_id)
        )
        integrations = integrations_result.scalar_one_or_none()

        if not integrations:
            integrations = CompanyIntegrations(company_id=company_id)
            session.add(integrations)

        integrations.google_drive_folder_id = folder_id
        integrations.google_drive_verified = success
        integrations.google_drive_folder_name = result if success else None

        await session.commit()

    if success:
        text = f"✅ <b>Папка подключена!</b>\n\n📁 Название: {result}"
    else:
        text = f"⚠️ <b>Папка сохранена, но есть проблема:</b>\n\n{result}"

    keyboard = [[InlineKeyboardButton("⬅️ К интеграциям", callback_data=f"sa:integrations:{company_id}")]]

    await update.message.reply_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )

    return SA_INTEGRATIONS


async def verify_integrations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Повторная проверка доступа к интеграциям."""
    query = update.callback_query
    await query.answer("Проверяю доступ...")

    company_id = int(query.data.split(":")[2])

    async with async_session_factory() as session:
        integrations_result = await session.execute(
            select(CompanyIntegrations).where(CompanyIntegrations.company_id == company_id)
        )
        integrations = integrations_result.scalar_one_or_none()

        if not integrations:
            await query.edit_message_text("❌ Интеграции не настроены.")
            return SA_INTEGRATIONS

        results = []

        # Проверяем таблицу
        if integrations.google_sheet_id:
            success, msg = await google_sheets_service.verify_sheet_access(integrations.google_sheet_id)
            integrations.google_sheet_verified = success
            if success:
                integrations.google_sheet_name = msg
            results.append(f"📊 Таблица: {'✅' if success else '❌'} {msg}")

        # Проверяем папку
        if integrations.google_drive_folder_id:
            success, msg = await google_sheets_service.verify_drive_folder_access(integrations.google_drive_folder_id)
            integrations.google_drive_verified = success
            if success:
                integrations.google_drive_folder_name = msg
            results.append(f"📁 Папка: {'✅' if success else '❌'} {msg}")

        await session.commit()

    if not results:
        results.append("Нет настроенных интеграций")

    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data=f"sa:integrations:{company_id}")]]

    await query.edit_message_text(
        "🔄 <b>Результаты проверки:</b>\n\n" + "\n".join(results),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )

    return SA_INTEGRATIONS


# ============== НАВИГАЦИЯ ==============


async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Вернуться в главное меню."""
    return await show_admin_menu(update, context)


async def close_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Закрыть панель администратора."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("👋 Панель администратора закрыта.")
    return ConversationHandler.END


async def cancel_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена (выход из ConversationHandler)."""
    await update.message.reply_text("👋 Панель администратора закрыта.")
    return ConversationHandler.END


async def btn_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка кнопки 'Админ-панель' из клавиатуры."""
    user_id = update.effective_user.id
    logger.debug(f"btn_admin_panel called by user_id={user_id}")

    if not is_superadmin(user_id):
        await update.message.reply_text("⛔ У вас нет доступа к панели администратора.")
        return ConversationHandler.END

    return await show_admin_menu(update, context)


# ============ УПРАВЛЕНИЕ ГРУППАМИ ДЛЯ УВЕДОМЛЕНИЙ ============

async def show_groups_select_company(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Выбор компании для управления группами."""
    query = update.callback_query
    await query.answer()

    async with async_session_factory() as session:
        result = await session.execute(select(Company).order_by(Company.id))
        companies = result.scalars().all()

    if not companies:
        await query.edit_message_text(
            "🏢 Нет компаний.\n\nСначала создайте компанию.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="sa:main")]]),
        )
        return SA_MAIN_MENU

    keyboard = [
        [InlineKeyboardButton(c.name, callback_data=f"sa:groups_company:{c.id}")]
        for c in companies
    ]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="sa:main")])

    await query.edit_message_text(
        "📢 <b>Группы для уведомлений</b>\n\n"
        "Выберите компанию:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return SA_GROUPS_SELECT_COMPANY


async def show_groups_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показать список групп компании."""
    query = update.callback_query
    await query.answer()

    company_id = int(query.data.split(":")[2])
    context.user_data["sa_groups_company_id"] = company_id

    async with async_session_factory() as session:
        company_result = await session.execute(
            select(Company).where(Company.id == company_id)
        )
        company = company_result.scalar_one_or_none()

        groups_result = await session.execute(
            select(TelegramGroup).where(TelegramGroup.company_id == company_id)
        )
        groups = groups_result.scalars().all()

    if not company:
        await query.edit_message_text("❌ Компания не найдена.")
        return SA_GROUPS_SELECT_COMPANY

    keyboard = []
    for g in groups:
        status = "✅" if g.is_active else "❌"
        keyboard.append([
            InlineKeyboardButton(
                f"{status} {g.title}",
                callback_data=f"sa:toggle_group:{g.id}"
            )
        ])

    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="sa:groups")])

    if groups:
        text = (
            f"📢 <b>Группы: {company.name}</b>\n\n"
            f"✅ — уведомления включены, ❌ — отключены\n"
            f"Нажмите на группу, чтобы включить/выключить:"
        )
    else:
        text = (
            f"📢 <b>Группы: {company.name}</b>\n\n"
            f"Нет добавленных групп.\n\n"
            f"Чтобы добавить группу, добавьте бота в Telegram-группу. "
            f"Бот автоматически сохранит группу."
        )

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )
    return SA_GROUPS_LIST


async def toggle_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Переключить активность группы."""
    query = update.callback_query
    await query.answer()

    group_id = int(query.data.split(":")[2])

    async with async_session_factory() as session:
        result = await session.execute(
            select(TelegramGroup).where(TelegramGroup.id == group_id)
        )
        group = result.scalar_one_or_none()

        if group:
            group.is_active = not group.is_active
            await session.commit()
            logger.info(f"Группа {group_id} is_active={group.is_active}")

    company_id = context.user_data.get("sa_groups_company_id")
    if company_id:
        return await _render_groups_list(query, context, company_id)
    else:
        await query.edit_message_text("❌ Ошибка: компания не найдена.")
        return SA_GROUPS_SELECT_COMPANY


async def _render_groups_list(query, context: ContextTypes.DEFAULT_TYPE, company_id: int) -> int:
    """Вспомогательная функция для отрисовки списка групп."""
    async with async_session_factory() as session:
        company_result = await session.execute(
            select(Company).where(Company.id == company_id)
        )
        company = company_result.scalar_one_or_none()

        groups_result = await session.execute(
            select(TelegramGroup).where(TelegramGroup.company_id == company_id)
        )
        groups = groups_result.scalars().all()

    if not company:
        await query.edit_message_text("❌ Компания не найдена.")
        return SA_GROUPS_SELECT_COMPANY

    keyboard = []
    for g in groups:
        status = "✅" if g.is_active else "❌"
        keyboard.append([
            InlineKeyboardButton(
                f"{status} {g.title}",
                callback_data=f"sa:toggle_group:{g.id}"
            )
        ])

    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="sa:groups")])

    text = (
        f"📢 <b>Группы: {company.name}</b>\n\n"
        f"✅ — уведомления включены, ❌ — отключены\n"
        f"Нажмите на группу, чтобы включить/выключить:"
    )

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )
    return SA_GROUPS_LIST


# ============ УПРАВЛЕНИЕ ДОЛЖНОСТЯМИ ДЛЯ УВЕДОМЛЕНИЙ ============

async def show_notify_positions_select_company(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Выбор компании для настройки должностей для уведомлений."""
    query = update.callback_query
    await query.answer()

    async with async_session_factory() as session:
        result = await session.execute(select(Company).order_by(Company.id))
        companies = result.scalars().all()

    if not companies:
        await query.edit_message_text(
            "🏢 Нет компаний.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="sa:main")]]),
        )
        return SA_MAIN_MENU

    keyboard = [
        [InlineKeyboardButton(c.name, callback_data=f"sa:notify_pos_company:{c.id}")]
        for c in companies
    ]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="sa:main")])

    await query.edit_message_text(
        "🔔 <b>Должности для регулярных уведомлений</b>\n\n"
        "Выберите компанию:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return SA_NOTIFY_POSITIONS_SELECT_COMPANY


async def show_notify_positions_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показать список должностей для настройки уведомлений."""
    query = update.callback_query
    await query.answer()

    company_id = int(query.data.split(":")[2])
    context.user_data["sa_notify_positions_company_id"] = company_id

    return await _render_notify_positions_list(query, context, company_id)


async def _render_notify_positions_list(query, context: ContextTypes.DEFAULT_TYPE, company_id: int) -> int:
    """Вспомогательная функция для отрисовки списка должностей для уведомлений."""
    async with async_session_factory() as session:
        company_result = await session.execute(
            select(Company).where(Company.id == company_id)
        )
        company = company_result.scalar_one_or_none()

        # Все должности компании
        positions_result = await session.execute(
            select(Position)
            .where(Position.company_id == company_id, Position.is_active == True)
            .order_by(Position.sort_order)
        )
        positions = positions_result.scalars().all()

        # Должности, настроенные для уведомлений
        notify_result = await session.execute(
            select(NotificationPosition.position_id).where(
                NotificationPosition.company_id == company_id
            )
        )
        notify_position_ids = {row[0] for row in notify_result.all()}

    if not company:
        await query.edit_message_text("❌ Компания не найдена.")
        return SA_NOTIFY_POSITIONS_SELECT_COMPANY

    keyboard = []
    for pos in positions:
        is_enabled = pos.id in notify_position_ids
        status = "✅" if is_enabled else "❌"
        keyboard.append([
            InlineKeyboardButton(
                f"{status} {pos.name}",
                callback_data=f"sa:toggle_notify_pos:{pos.id}"
            )
        ])

    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="sa:notify_positions")])

    if positions:
        text = (
            f"🔔 <b>Должности для уведомлений: {company.name}</b>\n\n"
            f"✅ — получает уведомления о регулярных заявках\n"
            f"❌ — не получает\n\n"
            f"Нажмите, чтобы включить/выключить:"
        )
    else:
        text = (
            f"🔔 <b>Должности для уведомлений: {company.name}</b>\n\n"
            f"Нет активных должностей. Сначала создайте должности в разделе 'Должности'."
        )

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )
    return SA_NOTIFY_POSITIONS_LIST


async def toggle_notify_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Включить/выключить должность для уведомлений."""
    query = update.callback_query
    await query.answer()

    position_id = int(query.data.split(":")[2])
    company_id = context.user_data.get("sa_notify_positions_company_id")

    if not company_id:
        await query.edit_message_text("❌ Ошибка: компания не найдена.")
        return SA_NOTIFY_POSITIONS_SELECT_COMPANY

    async with async_session_factory() as session:
        # Проверяем, есть ли уже запись
        existing_result = await session.execute(
            select(NotificationPosition).where(
                NotificationPosition.company_id == company_id,
                NotificationPosition.position_id == position_id,
            )
        )
        existing = existing_result.scalar_one_or_none()

        if existing:
            # Удаляем — выключаем уведомления
            await session.delete(existing)
            logger.info(f"Должность {position_id} удалена из уведомлений компании {company_id}")
        else:
            # Добавляем — включаем уведомления
            new_notify = NotificationPosition(
                company_id=company_id,
                position_id=position_id,
            )
            session.add(new_notify)
            logger.info(f"Должность {position_id} добавлена для уведомлений компании {company_id}")

        await session.commit()

    return await _render_notify_positions_list(query, context, company_id)


def get_superadmin_handler() -> ConversationHandler:
    """Получить ConversationHandler для панели суперадмина."""
    return ConversationHandler(
        entry_points=[
            CommandHandler("admin", cmd_admin),
            MessageHandler(filters.Regex("^🔧 Админ-панель$"), btn_admin_panel),
        ],
        states={
            SA_MAIN_MENU: [
                CallbackQueryHandler(show_companies_list, pattern=r"^sa:companies$"),
                CallbackQueryHandler(show_positions_companies, pattern=r"^sa:positions$"),
                CallbackQueryHandler(show_users_companies, pattern=r"^sa:users$"),
                CallbackQueryHandler(show_groups_select_company, pattern=r"^sa:groups$"),
                CallbackQueryHandler(show_notify_positions_select_company, pattern=r"^sa:notify_positions$"),
                CallbackQueryHandler(close_admin, pattern=r"^sa:close$"),
            ],
            SA_COMPANIES_LIST: [
                CallbackQueryHandler(show_company_detail, pattern=r"^sa:company:\d+$"),
                CallbackQueryHandler(start_create_company, pattern=r"^sa:create_company$"),
                CallbackQueryHandler(back_to_main, pattern=r"^sa:back_main$"),
            ],
            SA_COMPANY_DETAIL: [
                CallbackQueryHandler(reset_invite_code, pattern=r"^sa:reset_code:\d+$"),
                CallbackQueryHandler(show_positions_list, pattern=r"^sa:company_positions:\d+$"),
                CallbackQueryHandler(show_users_list, pattern=r"^sa:company_users:\d+$"),
                CallbackQueryHandler(show_integrations, pattern=r"^sa:integrations:\d+$"),
                CallbackQueryHandler(show_companies_list, pattern=r"^sa:companies$"),
            ],
            SA_CREATE_COMPANY_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_company_name_received),
            ],
            SA_POSITIONS_SELECT_COMPANY: [
                CallbackQueryHandler(show_positions_list, pattern=r"^sa:pos_company:\d+$"),
                CallbackQueryHandler(back_to_main, pattern=r"^sa:back_main$"),
            ],
            SA_POSITIONS_LIST: [
                CallbackQueryHandler(toggle_position, pattern=r"^sa:toggle_pos:\d+$"),
                CallbackQueryHandler(start_add_position, pattern=r"^sa:add_position:\d+$"),
                CallbackQueryHandler(show_positions_list, pattern=r"^sa:pos_company:\d+$"),
                CallbackQueryHandler(show_positions_companies, pattern=r"^sa:positions$"),
            ],
            SA_ADD_POSITION_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_position_name_received),
            ],
            SA_USERS_SELECT_COMPANY: [
                CallbackQueryHandler(show_users_list, pattern=r"^sa:users_company:\d+$"),
                CallbackQueryHandler(back_to_main, pattern=r"^sa:back_main$"),
            ],
            SA_USERS_LIST: [
                CallbackQueryHandler(show_user_detail, pattern=r"^sa:user:\d+$"),
                CallbackQueryHandler(show_users_list, pattern=r"^sa:users_company:\d+$"),
                CallbackQueryHandler(show_users_companies, pattern=r"^sa:users$"),
            ],
            SA_USER_DETAIL: [
                CallbackQueryHandler(change_user_role, pattern=r"^sa:(promote|demote):\d+$"),
                CallbackQueryHandler(show_user_positions, pattern=r"^sa:change_pos:\d+$"),
                CallbackQueryHandler(show_users_list, pattern=r"^sa:users_company:\d+$"),
            ],
            SA_USER_CHANGE_POSITION: [
                CallbackQueryHandler(set_user_position, pattern=r"^sa:set_pos:\d+:\d+$"),
                CallbackQueryHandler(show_user_detail, pattern=r"^sa:user:\d+$"),
            ],
            SA_INTEGRATIONS: [
                CallbackQueryHandler(start_set_sheet, pattern=r"^sa:set_sheet:\d+$"),
                CallbackQueryHandler(start_set_folder, pattern=r"^sa:set_folder:\d+$"),
                CallbackQueryHandler(verify_integrations, pattern=r"^sa:verify_integrations:\d+$"),
                CallbackQueryHandler(show_company_detail, pattern=r"^sa:company:\d+$"),
                CallbackQueryHandler(show_integrations, pattern=r"^sa:integrations:\d+$"),
            ],
            SA_ENTER_SHEET_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sheet_id),
            ],
            SA_ENTER_FOLDER_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_folder_id),
            ],
            SA_GROUPS_SELECT_COMPANY: [
                CallbackQueryHandler(show_groups_list, pattern=r"^sa:groups_company:\d+$"),
                CallbackQueryHandler(back_to_main, pattern=r"^sa:main$"),
            ],
            SA_GROUPS_LIST: [
                CallbackQueryHandler(toggle_group, pattern=r"^sa:toggle_group:\d+$"),
                CallbackQueryHandler(show_groups_select_company, pattern=r"^sa:groups$"),
            ],
            SA_NOTIFY_POSITIONS_SELECT_COMPANY: [
                CallbackQueryHandler(show_notify_positions_list, pattern=r"^sa:notify_pos_company:\d+$"),
                CallbackQueryHandler(back_to_main, pattern=r"^sa:main$"),
            ],
            SA_NOTIFY_POSITIONS_LIST: [
                CallbackQueryHandler(toggle_notify_position, pattern=r"^sa:toggle_notify_pos:\d+$"),
                CallbackQueryHandler(show_notify_positions_select_company, pattern=r"^sa:notify_positions$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_admin),
            CallbackQueryHandler(close_admin, pattern=r"^sa:close$"),
        ],
        name="superadmin_conversation",
        persistent=False,
        allow_reentry=True,
    )
