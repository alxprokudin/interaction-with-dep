"""Процесс регистрации пользователя в компании."""
from __future__ import annotations

from loguru import logger

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from sqlalchemy import select

from bot.config import SUPERADMIN_IDS
from bot.models import Company, JoinRequest, JoinRequestStatus, User, UserRole
from bot.models.base import async_session_factory


# Состояния диалога
ENTER_CODE = 0


async def get_user_companies(telegram_id: int) -> list[tuple[int, str]]:
    """Получить список компаний пользователя."""
    logger.debug(f"get_user_companies called with: telegram_id={telegram_id}")
    async with async_session_factory() as session:
        result = await session.execute(
            select(User.company_id, Company.name)
            .join(Company, User.company_id == Company.id)
            .where(User.telegram_id == telegram_id)
        )
        companies = result.all()
        logger.debug(f"Найдено компаний: {len(companies)}")
        return [(row[0], row[1]) for row in companies]


async def get_pending_request(telegram_id: int) -> JoinRequest | None:
    """Проверить есть ли ожидающая заявка."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(JoinRequest)
            .where(
                JoinRequest.telegram_id == telegram_id,
                JoinRequest.status == JoinRequestStatus.PENDING,
            )
            .order_by(JoinRequest.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def get_company_by_code(invite_code: str) -> Company | None:
    """Найти компанию по коду приглашения."""
    logger.debug(f"get_company_by_code called with: invite_code={invite_code}")
    async with async_session_factory() as session:
        result = await session.execute(
            select(Company).where(Company.invite_code == invite_code)
        )
        return result.scalar_one_or_none()


async def get_company_admins(company_id: int) -> list[int]:
    """Получить telegram_id всех админов компании."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(User.telegram_id).where(
                User.company_id == company_id,
                User.role == UserRole.ADMIN,
            )
        )
        return [row[0] for row in result.all()]


async def create_join_request(
    telegram_id: int,
    telegram_username: str | None,
    telegram_full_name: str | None,
    company_id: int,
) -> JoinRequest:
    """Создать заявку на вступление."""
    logger.info(
        f"create_join_request called: telegram_id={telegram_id}, company_id={company_id}"
    )
    async with async_session_factory() as session:
        # Проверим, нет ли уже pending заявки
        existing = await session.execute(
            select(JoinRequest).where(
                JoinRequest.telegram_id == telegram_id,
                JoinRequest.company_id == company_id,
                JoinRequest.status == JoinRequestStatus.PENDING,
            )
        )
        if existing.scalar_one_or_none():
            logger.warning("Заявка уже существует")
            raise ValueError("Заявка уже существует")

        join_request = JoinRequest(
            telegram_id=telegram_id,
            telegram_username=telegram_username,
            telegram_full_name=telegram_full_name,
            company_id=company_id,
        )
        session.add(join_request)
        await session.commit()
        await session.refresh(join_request)
        logger.info(f"Заявка создана: id={join_request.id}")
        return join_request


async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало регистрации — запрос кода компании."""
    user = update.effective_user
    logger.info(f"start_registration called: user_id={user.id}")

    # Проверяем, есть ли ожидающая заявка
    pending = await get_pending_request(user.id)
    if pending:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Company.name).where(Company.id == pending.company_id)
            )
            company_name = result.scalar_one_or_none() or "компанию"

        await update.message.reply_text(
            f"⏳ У вас уже есть заявка на вступление в «{company_name}».\n\n"
            "Ожидайте подтверждения от администратора."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "🔐 **Регистрация в компании**\n\n"
        "Введите код приглашения, который вам дал администратор компании:\n\n"
        "_Для отмены отправьте /cancel_",
        parse_mode="Markdown",
    )
    return ENTER_CODE


async def code_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка введённого кода компании."""
    user = update.effective_user
    code = update.message.text.strip()
    logger.debug(f"code_received: user_id={user.id}, code={code}")

    # Ищем компанию по коду
    company = await get_company_by_code(code)
    if not company:
        await update.message.reply_text(
            "❌ Код не найден. Проверьте правильность и попробуйте снова.\n\n"
            "_Для отмены отправьте /cancel_",
            parse_mode="Markdown",
        )
        return ENTER_CODE

    # Проверяем, не состоит ли уже в этой компании
    user_companies = await get_user_companies(user.id)
    if company.id in [c[0] for c in user_companies]:
        await update.message.reply_text(
            f"✅ Вы уже состоите в компании «{company.name}».\n\n"
            "Используйте /start для входа в меню."
        )
        return ConversationHandler.END

    # Создаём заявку
    try:
        join_request = await create_join_request(
            telegram_id=user.id,
            telegram_username=user.username,
            telegram_full_name=user.full_name,
            company_id=company.id,
        )
    except ValueError:
        await update.message.reply_text(
            f"⏳ У вас уже есть заявка в компанию «{company.name}».\n\n"
            "Ожидайте подтверждения от администратора."
        )
        return ConversationHandler.END

    # Автоодобрение для суперадминов
    if user.id in SUPERADMIN_IDS:
        logger.info(f"Автоодобрение заявки для суперадмина: user_id={user.id}")
        async with async_session_factory() as session:
            # Обновляем статус заявки
            result = await session.execute(
                select(JoinRequest).where(JoinRequest.id == join_request.id)
            )
            jr = result.scalar_one()
            jr.status = JoinRequestStatus.APPROVED
            
            # Создаём пользователя в компании
            new_user = User(
                telegram_id=user.id,
                full_name=user.full_name,
                company_id=company.id,
                role=UserRole.ADMIN,  # Суперадмин получает роль админа
            )
            session.add(new_user)
            await session.commit()
        
        from bot.keyboards.main import get_main_menu_keyboard
        await update.message.reply_text(
            f"✅ Вы добавлены в компанию «{company.name}» как администратор!\n\n"
            "Используйте меню для работы.",
            reply_markup=get_main_menu_keyboard(is_superadmin=True),
        )
        return ConversationHandler.END

    # Уведомляем админов
    admins = await get_company_admins(company.id)
    logger.info(f"Уведомляем админов: {admins}")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f"jr:approve:{join_request.id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"jr:reject:{join_request.id}"),
        ]
    ])

    user_display = f"@{user.username}" if user.username else user.full_name or f"ID:{user.id}"
    admin_message = (
        f"📥 <b>Новая заявка на вступление</b>\n\n"
        f"👤 Пользователь: {user_display}\n"
        f"🏢 Компания: {company.name}\n"
        f"🆔 Telegram ID: <code>{user.id}</code>"
    )

    for admin_id in admins:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=admin_message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            logger.debug(f"Уведомление отправлено админу: {admin_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления админу {admin_id}: {e}")

    if not admins:
        logger.warning(f"В компании {company.id} нет админов для уведомления")

    await update.message.reply_text(
        f"✅ Заявка на вступление в «{company.name}» отправлена!\n\n"
        "Ожидайте подтверждения от администратора. "
        "Вам придёт уведомление, когда заявку рассмотрят."
    )
    return ConversationHandler.END


async def cancel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена регистрации."""
    logger.info(f"cancel_registration: user_id={update.effective_user.id}")
    await update.message.reply_text(
        "❌ Регистрация отменена.\n\n"
        "Чтобы начать заново, отправьте /start"
    )
    return ConversationHandler.END


def get_registration_handler() -> ConversationHandler:
    """Собрать ConversationHandler для регистрации."""
    return ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(r"^🔐 Присоединиться к компании$"),
                start_registration,
            ),
        ],
        states={
            ENTER_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, code_received),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex(r"^/cancel$"), cancel_registration),
        ],
        name="registration",
    )
