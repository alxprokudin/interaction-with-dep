"""Сервис уведомлений о заявках."""
from __future__ import annotations

from typing import Optional, List

from loguru import logger

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.models.base import async_session_factory
from bot.models.telegram_group import TelegramGroup
from bot.models.notification_settings import NotificationPosition
from bot.models.user import User


async def get_active_groups_for_company(company_id: int) -> List[TelegramGroup]:
    """Получить активные группы компании для уведомлений."""
    logger.debug(f"get_active_groups_for_company: company_id={company_id}")
    
    async with async_session_factory() as session:
        result = await session.execute(
            select(TelegramGroup).where(
                TelegramGroup.company_id == company_id,
                TelegramGroup.is_active == True,
            )
        )
        groups = result.scalars().all()
        logger.info(f"Найдено {len(groups)} активных групп для компании {company_id}")
        return list(groups)


async def get_users_for_notification(company_id: int) -> List[User]:
    """
    Получить пользователей, которые должны получать уведомления о регулярных заявках.
    
    Это пользователи с должностями, настроенными в notification_positions.
    """
    logger.debug(f"get_users_for_notification: company_id={company_id}")
    
    async with async_session_factory() as session:
        # Получаем ID должностей, настроенных для уведомлений
        positions_result = await session.execute(
            select(NotificationPosition.position_id).where(
                NotificationPosition.company_id == company_id
            )
        )
        position_ids = [row[0] for row in positions_result.all()]
        
        if not position_ids:
            logger.warning(f"Нет настроенных должностей для уведомлений в компании {company_id}")
            return []
        
        # Получаем пользователей с этими должностями
        users_result = await session.execute(
            select(User).where(
                User.company_id == company_id,
                User.position_id.in_(position_ids),
            )
        )
        users = users_result.scalars().all()
        logger.info(f"Найдено {len(users)} пользователей для уведомлений в компании {company_id}")
        return list(users)


def format_request_notification(
    request_type: str,
    request_id: str,
    nomenclature: str,
    supplier_name: str,
    price: str,
    sla_days: int,
    username: str,
    folder_link: Optional[str] = None,
) -> str:
    """Форматировать сообщение уведомления о заявке."""
    
    if request_type == "urgent":
        emoji = "🔴"
        type_text = "СРОЧНАЯ ЗАЯВКА"
        sla_text = f"{sla_days} рабочих дня"
    else:
        emoji = "🟢"
        type_text = "Регулярная заявка"
        sla_text = f"{sla_days} рабочих дней"
    
    lines = [
        f"{emoji} *{type_text}* [{request_id}]",
        "",
        f"📦 *Номенклатура:* {nomenclature}",
        f"🏢 *Поставщик:* {supplier_name}",
        f"💰 *Цена:* {price} ₽",
        f"⏰ *SLA:* {sla_text}",
        f"👤 *От:* @{username}",
    ]
    
    if folder_link:
        lines.append("")
        lines.append(f"📁 [Открыть папку]({folder_link})")
    
    return "\n".join(lines)


async def send_urgent_notifications(
    bot,
    company_id: int,
    request_id: str,
    nomenclature: str,
    supplier_name: str,
    price: str,
    sla_days: int,
    username: str,
    folder_link: Optional[str] = None,
) -> int:
    """
    Отправить уведомления о срочной заявке во все активные группы компании.
    
    Returns:
        Количество успешно отправленных уведомлений
    """
    logger.info(f"send_urgent_notifications: company_id={company_id}, request_id={request_id}")
    
    groups = await get_active_groups_for_company(company_id)
    
    if not groups:
        logger.warning(f"Нет активных групп для уведомлений в компании {company_id}")
        return 0
    
    message = format_request_notification(
        request_type="urgent",
        request_id=request_id,
        nomenclature=nomenclature,
        supplier_name=supplier_name,
        price=price,
        sla_days=sla_days,
        username=username,
        folder_link=folder_link,
    )
    
    sent_count = 0
    for group in groups:
        try:
            await bot.send_message(
                chat_id=group.chat_id,
                text=message,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            sent_count += 1
            logger.debug(f"Уведомление отправлено в группу {group.chat_id} ({group.title})")
        except Exception as e:
            logger.error(f"Ошибка отправки в группу {group.chat_id}: {e}")
    
    logger.info(f"Отправлено {sent_count}/{len(groups)} уведомлений в группы")
    return sent_count


async def send_regular_notifications(
    bot,
    company_id: int,
    request_id: str,
    nomenclature: str,
    supplier_name: str,
    price: str,
    sla_days: int,
    username: str,
    folder_link: Optional[str] = None,
    override_type: Optional[str] = None,
) -> int:
    """
    Отправить уведомления о заявке пользователям с настроенными должностями.
    
    Args:
        override_type: Если указан, использовать этот тип вместо "regular" для форматирования
    
    Returns:
        Количество успешно отправленных уведомлений
    """
    logger.info(f"send_regular_notifications: company_id={company_id}, request_id={request_id}")
    
    users = await get_users_for_notification(company_id)
    
    if not users:
        logger.warning(f"Нет пользователей для уведомлений в компании {company_id}")
        return 0
    
    # Используем override_type если указан (для срочных заявок, отправляемых по должностям)
    message_type = override_type or "regular"
    
    message = format_request_notification(
        request_type=message_type,
        request_id=request_id,
        nomenclature=nomenclature,
        supplier_name=supplier_name,
        price=price,
        sla_days=sla_days,
        username=username,
        folder_link=folder_link,
    )
    
    sent_count = 0
    for user in users:
        try:
            await bot.send_message(
                chat_id=user.telegram_id,
                text=message,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            sent_count += 1
            logger.debug(f"Уведомление отправлено пользователю {user.telegram_id} ({user.full_name})")
        except Exception as e:
            logger.error(f"Ошибка отправки пользователю {user.telegram_id}: {e}")
    
    logger.info(f"Отправлено {sent_count}/{len(users)} уведомлений пользователям")
    return sent_count


async def send_request_notifications(
    bot,
    company_id: int,
    request_type: str,
    request_id: str,
    nomenclature: str,
    supplier_name: str,
    price: str,
    sla_days: int,
    username: str,
    folder_link: Optional[str] = None,
) -> int:
    """
    Отправить уведомления о заявке в зависимости от типа.
    
    - Срочные → в группы + пользователям с настроенными должностями
    - Регулярные → только пользователям с настроенными должностями
    
    Returns:
        Количество успешно отправленных уведомлений
    """
    total_sent = 0
    
    if request_type == "urgent":
        # Срочные: в группы + по должностям
        groups_sent = await send_urgent_notifications(
            bot=bot,
            company_id=company_id,
            request_id=request_id,
            nomenclature=nomenclature,
            supplier_name=supplier_name,
            price=price,
            sla_days=sla_days,
            username=username,
            folder_link=folder_link,
        )
        total_sent += groups_sent
        
        # + личные сообщения по должностям
        users_sent = await send_regular_notifications(
            bot=bot,
            company_id=company_id,
            request_id=request_id,
            nomenclature=nomenclature,
            supplier_name=supplier_name,
            price=price,
            sla_days=sla_days,
            username=username,
            folder_link=folder_link,
            override_type="urgent",  # Сохраняем тип "срочная" в сообщении
        )
        total_sent += users_sent
        
        logger.info(f"Срочная заявка {request_id}: {groups_sent} в группы, {users_sent} по должностям")
    else:
        # Регулярные: только по должностям
        total_sent = await send_regular_notifications(
            bot=bot,
            company_id=company_id,
            request_id=request_id,
            nomenclature=nomenclature,
            supplier_name=supplier_name,
            price=price,
            sla_days=sla_days,
            username=username,
            folder_link=folder_link,
        )
    
    return total_sent
