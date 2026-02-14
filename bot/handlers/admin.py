"""Обработка заявок администратором."""
from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from sqlalchemy import select

from bot.models import Company, JoinRequest, JoinRequestStatus, Position, User, UserRole
from bot.models.base import async_session_factory


async def handle_join_request_decision(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработка решения админа по заявке (одобрить/отклонить)."""
    query = update.callback_query
    await query.answer()

    data = query.data  # jr:approve:123 или jr:reject:123
    parts = data.split(":")
    if len(parts) != 3:
        logger.error(f"Неверный формат callback_data: {data}")
        return

    action = parts[1]  # approve или reject
    request_id = int(parts[2])

    logger.info(
        f"handle_join_request_decision: action={action}, request_id={request_id}, admin_id={update.effective_user.id}"
    )

    async with async_session_factory() as session:
        # Получаем заявку
        result = await session.execute(
            select(JoinRequest).where(JoinRequest.id == request_id)
        )
        join_request = result.scalar_one_or_none()

        if not join_request:
            await query.edit_message_text("❌ Заявка не найдена.")
            return

        if join_request.status != JoinRequestStatus.PENDING:
            status_text = "одобрена" if join_request.status == JoinRequestStatus.APPROVED else "отклонена"
            await query.edit_message_text(f"ℹ️ Эта заявка уже {status_text}.")
            return

        # Получаем информацию о компании
        company_result = await session.execute(
            select(Company).where(Company.id == join_request.company_id)
        )
        company = company_result.scalar_one_or_none()
        company_name = company.name if company else "Компания"

        # Получаем User админа для записи reviewed_by
        admin_result = await session.execute(
            select(User).where(
                User.telegram_id == update.effective_user.id,
                User.company_id == join_request.company_id,
            )
        )
        admin_user = admin_result.scalar_one_or_none()

        if action == "approve":
            # Получаем список должностей компании
            positions_result = await session.execute(
                select(Position)
                .where(Position.company_id == join_request.company_id, Position.is_active == True)
                .order_by(Position.sort_order)
            )
            positions = positions_result.scalars().all()

            user_display = (
                f"@{join_request.telegram_username}"
                if join_request.telegram_username
                else join_request.telegram_full_name or f"ID:{join_request.telegram_id}"
            )

            if not positions:
                # Нет должностей — показываем предупреждение
                keyboard = [
                    [InlineKeyboardButton("✅ Добавить без должности", callback_data=f"jr:pos:{request_id}:0")],
                    [InlineKeyboardButton("❌ Отмена", callback_data=f"jr:cancel:{request_id}")],
                ]
                await query.edit_message_text(
                    f"⚠️ <b>Выбор должности</b>\n\n"
                    f"👤 Пользователь: {user_display}\n"
                    f"🏢 Компания: {company_name}\n\n"
                    f"В компании пока нет должностей.\n"
                    f"Вы можете добавить их через скрипт <code>manage_positions.py</code>",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML",
                )
            else:
                # Показываем список должностей для выбора
                keyboard = []
                for pos in positions:
                    keyboard.append([
                        InlineKeyboardButton(
                            f"👔 {pos.name}",
                            callback_data=f"jr:pos:{request_id}:{pos.id}"
                        )
                    ])
                keyboard.append([InlineKeyboardButton("📝 Без должности", callback_data=f"jr:pos:{request_id}:0")])
                keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data=f"jr:cancel:{request_id}")])

                await query.edit_message_text(
                    f"👔 <b>Выберите должность</b>\n\n"
                    f"👤 Пользователь: {user_display}\n"
                    f"🏢 Компания: {company_name}\n\n"
                    f"Выберите должность для сотрудника:",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML",
                )

        elif action == "reject":
            # Отклоняем заявку
            join_request.status = JoinRequestStatus.REJECTED
            join_request.reviewed_at = datetime.now(timezone.utc)
            if admin_user:
                join_request.reviewed_by_user_id = admin_user.id
            await session.commit()

            logger.info(f"Заявка {request_id} отклонена")

            # Обновляем сообщение админа
            user_display = (
                f"@{join_request.telegram_username}"
                if join_request.telegram_username
                else join_request.telegram_full_name or f"ID:{join_request.telegram_id}"
            )
            await query.edit_message_text(
                f"❌ <b>Заявка отклонена</b>\n\n"
                f"👤 Пользователь: {user_display}\n"
                f"🏢 Компания: {company_name}",
                parse_mode="HTML",
            )

            # Уведомляем пользователя
            try:
                await context.bot.send_message(
                    chat_id=join_request.telegram_id,
                    text=(
                        f"❌ <b>Ваша заявка отклонена</b>\n\n"
                        f"Заявка на вступление в компанию «{company_name}» была отклонена.\n\n"
                        f"Если считаете это ошибкой, свяжитесь с администратором компании."
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления пользователя {join_request.telegram_id}: {e}")


async def handle_position_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработка выбора должности для нового сотрудника."""
    query = update.callback_query
    await query.answer()

    data = query.data  # jr:pos:123:456 (request_id:position_id)
    parts = data.split(":")
    if len(parts) != 4:
        logger.error(f"Неверный формат callback_data: {data}")
        return

    request_id = int(parts[2])
    position_id = int(parts[3])  # 0 = без должности

    logger.info(
        f"handle_position_selection: request_id={request_id}, position_id={position_id}, admin_id={update.effective_user.id}"
    )

    async with async_session_factory() as session:
        # Получаем заявку
        result = await session.execute(
            select(JoinRequest).where(JoinRequest.id == request_id)
        )
        join_request = result.scalar_one_or_none()

        if not join_request:
            await query.edit_message_text("❌ Заявка не найдена.")
            return

        if join_request.status != JoinRequestStatus.PENDING:
            status_text = "одобрена" if join_request.status == JoinRequestStatus.APPROVED else "отклонена"
            await query.edit_message_text(f"ℹ️ Эта заявка уже {status_text}.")
            return

        # Получаем информацию о компании
        company_result = await session.execute(
            select(Company).where(Company.id == join_request.company_id)
        )
        company = company_result.scalar_one_or_none()
        company_name = company.name if company else "Компания"

        # Получаем должность (если выбрана)
        position_name = None
        if position_id > 0:
            position_result = await session.execute(
                select(Position).where(Position.id == position_id)
            )
            position = position_result.scalar_one_or_none()
            if position:
                position_name = position.name

        # Получаем User админа для записи reviewed_by
        admin_result = await session.execute(
            select(User).where(
                User.telegram_id == update.effective_user.id,
                User.company_id == join_request.company_id,
            )
        )
        admin_user = admin_result.scalar_one_or_none()

        # Одобряем заявку
        join_request.status = JoinRequestStatus.APPROVED
        join_request.reviewed_at = datetime.now(timezone.utc)
        if admin_user:
            join_request.reviewed_by_user_id = admin_user.id

        # Создаём пользователя в компании
        new_user = User(
            telegram_id=join_request.telegram_id,
            company_id=join_request.company_id,
            role=UserRole.EMPLOYEE,
            full_name=join_request.telegram_full_name,
            position_id=position_id if position_id > 0 else None,
        )
        session.add(new_user)
        await session.commit()

        logger.info(
            f"Заявка {request_id} одобрена с должностью {position_name or 'без должности'}, "
            f"создан user для telegram_id={join_request.telegram_id}"
        )

        # Обновляем сообщение админа
        user_display = (
            f"@{join_request.telegram_username}"
            if join_request.telegram_username
            else join_request.telegram_full_name or f"ID:{join_request.telegram_id}"
        )
        position_text = f"👔 Должность: {position_name}" if position_name else "👔 Должность: не назначена"

        await query.edit_message_text(
            f"✅ <b>Заявка одобрена</b>\n\n"
            f"👤 Пользователь: {user_display}\n"
            f"🏢 Компания: {company_name}\n"
            f"{position_text}",
            parse_mode="HTML",
        )

        # Уведомляем пользователя
        position_info = f"\n👔 Должность: {position_name}" if position_name else ""
        try:
            await context.bot.send_message(
                chat_id=join_request.telegram_id,
                text=(
                    f"🎉 <b>Ваша заявка одобрена!</b>\n\n"
                    f"Вы добавлены в компанию «{company_name}».{position_info}\n\n"
                    f"Отправьте /start для входа в меню."
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления пользователя {join_request.telegram_id}: {e}")


async def handle_cancel_approval(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Отмена процесса одобрения — возврат к исходным кнопкам."""
    query = update.callback_query
    await query.answer()

    data = query.data  # jr:cancel:123
    parts = data.split(":")
    if len(parts) != 3:
        logger.error(f"Неверный формат callback_data: {data}")
        return

    request_id = int(parts[2])

    async with async_session_factory() as session:
        # Получаем заявку
        result = await session.execute(
            select(JoinRequest).where(JoinRequest.id == request_id)
        )
        join_request = result.scalar_one_or_none()

        if not join_request or join_request.status != JoinRequestStatus.PENDING:
            await query.edit_message_text("ℹ️ Заявка уже обработана или не найдена.")
            return

        # Получаем информацию о компании
        company_result = await session.execute(
            select(Company).where(Company.id == join_request.company_id)
        )
        company = company_result.scalar_one_or_none()
        company_name = company.name if company else "Компания"

        user_display = (
            f"@{join_request.telegram_username}"
            if join_request.telegram_username
            else join_request.telegram_full_name or f"ID:{join_request.telegram_id}"
        )

        # Возвращаем исходные кнопки
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Одобрить", callback_data=f"jr:approve:{request_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"jr:reject:{request_id}"),
            ]
        ])

        await query.edit_message_text(
            f"📬 <b>Заявка на вступление</b>\n\n"
            f"👤 Пользователь: {user_display}\n"
            f"🏢 Компания: {company_name}\n\n"
            f"Что сделать с заявкой?",
            reply_markup=keyboard,
            parse_mode="HTML",
        )


def get_admin_handlers() -> list[CallbackQueryHandler]:
    """Получить хэндлеры для админских действий."""
    return [
        CallbackQueryHandler(
            handle_join_request_decision,
            pattern=r"^jr:(approve|reject):\d+$",
        ),
        CallbackQueryHandler(
            handle_position_selection,
            pattern=r"^jr:pos:\d+:\d+$",
        ),
        CallbackQueryHandler(
            handle_cancel_approval,
            pattern=r"^jr:cancel:\d+$",
        ),
    ]
