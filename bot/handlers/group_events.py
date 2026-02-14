"""Обработчик событий в группах (добавление/удаление бота)."""
from __future__ import annotations

from typing import Optional

from loguru import logger

from telegram import Update, ChatMemberUpdated
from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import ContextTypes, ChatMemberHandler

from sqlalchemy import select, delete

from bot.models.base import async_session_factory
from bot.models.telegram_group import TelegramGroup
from bot.models.user import User
from bot.config import SUPERADMIN_IDS


def _extract_status_change(chat_member_update: ChatMemberUpdated) -> tuple[bool, bool]:
    """
    Извлечь информацию об изменении статуса бота.
    
    Returns:
        (was_member, is_member) — был ли участником, стал ли участником
    """
    status_changes = {
        ChatMemberStatus.LEFT: False,
        ChatMemberStatus.BANNED: False,
        ChatMemberStatus.MEMBER: True,
        ChatMemberStatus.ADMINISTRATOR: True,
        ChatMemberStatus.OWNER: True,
        ChatMemberStatus.RESTRICTED: True,
    }
    
    old_status = chat_member_update.old_chat_member.status
    new_status = chat_member_update.new_chat_member.status
    
    was_member = status_changes.get(old_status, False)
    is_member = status_changes.get(new_status, False)
    
    return was_member, is_member


async def _get_company_id_from_adder(adder_telegram_id: int) -> Optional[int]:
    """Получить company_id пользователя, который добавил бота."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(User.company_id).where(User.telegram_id == adder_telegram_id)
        )
        row = result.first()
        if row:
            return row[0]
    return None


async def handle_bot_added_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка добавления/удаления бота из группы."""
    chat_member = update.my_chat_member
    
    if not chat_member:
        return
    
    chat = chat_member.chat
    
    # Обрабатываем только группы и супергруппы
    if chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        return
    
    was_member, is_member = _extract_status_change(chat_member)
    
    # Бот был добавлен в группу
    if not was_member and is_member:
        adder = chat_member.from_user
        logger.info(
            f"Бот добавлен в группу: chat_id={chat.id}, title='{chat.title}', "
            f"добавил: @{adder.username or adder.id}"
        )
        
        # Определяем company_id от пользователя, который добавил бота
        company_id = await _get_company_id_from_adder(adder.id)
        
        if not company_id:
            # Если пользователь не в системе, проверяем суперадминов
            if adder.id in SUPERADMIN_IDS:
                logger.warning(
                    f"Суперадмин {adder.id} добавил бота в группу, "
                    "но не привязан к компании. Группа не сохранена."
                )
                await context.bot.send_message(
                    chat.id,
                    "⚠️ Я добавлен в группу, но не могу определить компанию.\n"
                    "Пожалуйста, добавьте бота из аккаунта, привязанного к компании."
                )
            else:
                logger.warning(
                    f"Пользователь {adder.id} не найден в системе. Группа не сохранена."
                )
            return
        
        # Сохраняем группу в БД
        async with async_session_factory() as session:
            # Проверяем, не сохранена ли уже эта группа
            existing = await session.execute(
                select(TelegramGroup).where(TelegramGroup.chat_id == chat.id)
            )
            existing_group = existing.scalar_one_or_none()
            
            if existing_group:
                # Обновляем название и активируем
                existing_group.title = chat.title or "Без названия"
                existing_group.is_active = True
                existing_group.company_id = company_id
                logger.info(f"Группа {chat.id} обновлена для компании {company_id}")
            else:
                # Создаём новую запись
                new_group = TelegramGroup(
                    company_id=company_id,
                    chat_id=chat.id,
                    title=chat.title or "Без названия",
                    is_active=True,
                )
                session.add(new_group)
                logger.info(f"Группа {chat.id} сохранена для компании {company_id}")
            
            await session.commit()
        
        await context.bot.send_message(
            chat.id,
            f"✅ Я добавлен в группу и готов отправлять уведомления о срочных заявках.\n\n"
            f"Управление группами доступно в настройках бота."
        )
    
    # Бот был удалён из группы
    elif was_member and not is_member:
        logger.info(f"Бот удалён из группы: chat_id={chat.id}, title='{chat.title}'")
        
        # Деактивируем группу (не удаляем из БД)
        async with async_session_factory() as session:
            result = await session.execute(
                select(TelegramGroup).where(TelegramGroup.chat_id == chat.id)
            )
            group = result.scalar_one_or_none()
            
            if group:
                group.is_active = False
                await session.commit()
                logger.info(f"Группа {chat.id} деактивирована")


def get_group_events_handler() -> ChatMemberHandler:
    """Получить обработчик событий группы."""
    return ChatMemberHandler(
        handle_bot_added_to_group,
        ChatMemberHandler.MY_CHAT_MEMBER,
    )
