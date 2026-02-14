"""Сервис обработки ответов на письма."""
from __future__ import annotations

import asyncio
from typing import Optional, TYPE_CHECKING

from loguru import logger

from bot.services.email_receiver import email_receiver, IncomingEmail
from bot.services.email_service import (
    mark_reply_received,
    get_sent_email_by_message_id,
    parse_message_id,
)

if TYPE_CHECKING:
    from telegram import Bot


# Email type descriptions for user messages
EMAIL_TYPE_DESCRIPTIONS = {
    "sb_check": "Проверка СБ",
    "docsinbox": "DocsInBox",
    "roaming": "Роуминг",
    "documents": "Документы поставщику",
}


async def process_email_replies(bot: Bot) -> int:
    """
    Проверить входящие письма и обработать ответы на наши отправленные.
    
    Args:
        bot: Telegram Bot instance для отправки уведомлений.
        
    Returns:
        Количество обработанных ответов.
    """
    logger.info("process_email_replies: начинаем проверку")
    
    # Получаем непрочитанные ответы на наши письма
    replies = await email_receiver.fetch_unprocessed_replies(since_days=7)
    
    if not replies:
        logger.info("Новых ответов не найдено")
        return 0
    
    processed_count = 0
    
    for reply in replies:
        try:
            # Определяем, на какое наше письмо это ответ
            original_message_id = reply.in_reply_to
            
            # Если In-Reply-To пустой, ищем в References
            if not original_message_id and reply.references:
                original_message_id = reply.references[-1]
            
            if not original_message_id:
                logger.warning(f"Не удалось определить исходное письмо для {reply.message_id}")
                continue
            
            # Ищем наше отправленное письмо
            sent_email = await get_sent_email_by_message_id(original_message_id)
            
            if not sent_email:
                # Может быть, это ответ не на наше письмо
                logger.debug(f"Письмо {original_message_id} не найдено в нашей БД")
                continue
            
            logger.info(
                f"Найден ответ на письмо {sent_email.email_type.value} "
                f"для поставщика {sent_email.supplier_name}"
            )
            
            # Отмечаем, что ответ получен
            await mark_reply_received(original_message_id, reply.message_id)
            
            # Обновляем статус в Google Sheets
            if sent_email.sheet_id:
                from bot.services.google_sheets import google_sheets_service
                await google_sheets_service.update_supplier_reply_status(
                    sheet_id=sent_email.sheet_id,
                    supplier_inn=sent_email.supplier_inn,
                    email_type=sent_email.email_type.value,
                )
            
            # Отправляем уведомление пользователю в Telegram
            await notify_user_about_reply(
                bot=bot,
                telegram_user_id=sent_email.telegram_user_id,
                sent_email=sent_email,
                reply=reply,
            )
            
            # Очищаем временные файлы вложений
            reply.cleanup_attachments()
            
            processed_count += 1
            
        except Exception as e:
            logger.error(f"Ошибка обработки ответа {reply.message_id}: {e}", exc_info=True)
            continue
    
    logger.info(f"Обработано ответов: {processed_count}")
    return processed_count


async def notify_user_about_reply(
    bot: Bot,
    telegram_user_id: int,
    sent_email,  # SentEmail model
    reply: IncomingEmail,
) -> bool:
    """
    Отправить уведомление пользователю о полученном ответе.
    
    Включает текст ответа и вложения.
    """
    logger.info(f"notify_user_about_reply: user={telegram_user_id}, type={sent_email.email_type.value}")
    
    try:
        # Формируем сообщение
        email_type_desc = EMAIL_TYPE_DESCRIPTIONS.get(
            sent_email.email_type.value, 
            sent_email.email_type.value
        )
        
        message_text = (
            f"📬 *Получен ответ на письмо*\n\n"
            f"📌 *Тип:* {email_type_desc}\n"
            f"🏢 *Поставщик:* {sent_email.supplier_name}\n"
            f"📋 *ИНН:* {sent_email.supplier_inn}\n"
            f"👤 *От:* {reply.from_addr}\n"
            f"📝 *Тема:* {reply.subject}\n\n"
        )
        
        # Добавляем текст ответа (ограничиваем длину)
        body_text = reply.body_text or "(текст отсутствует)"
        if len(body_text) > 2000:
            body_text = body_text[:2000] + "...\n\n_(текст обрезан)_"
        
        message_text += f"*Текст ответа:*\n{body_text}"
        
        # Отправляем основное сообщение
        await bot.send_message(
            chat_id=telegram_user_id,
            text=message_text,
            parse_mode="Markdown",
        )
        
        # Отправляем вложения
        if reply.attachments:
            await bot.send_message(
                chat_id=telegram_user_id,
                text=f"📎 *Вложения ({len(reply.attachments)}):*",
                parse_mode="Markdown",
            )
            
            for attachment in reply.attachments:
                try:
                    # Сохраняем во временный файл
                    temp_path = attachment.save_to_temp()
                    
                    # Отправляем как документ
                    with open(temp_path, "rb") as f:
                        await bot.send_document(
                            chat_id=telegram_user_id,
                            document=f,
                            filename=attachment.filename,
                        )
                except Exception as e:
                    logger.warning(f"Не удалось отправить вложение {attachment.filename}: {e}")
                    await bot.send_message(
                        chat_id=telegram_user_id,
                        text=f"⚠️ Не удалось отправить вложение: {attachment.filename}",
                    )
        
        logger.info(f"Уведомление отправлено пользователю {telegram_user_id}")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления: {e}", exc_info=True)
        return False


async def check_email_replies_job(bot: Bot):
    """
    Задача для APScheduler: проверять ответы на письма.
    
    Вызывается периодически (например, каждые 5 минут).
    """
    try:
        await process_email_replies(bot)
    except Exception as e:
        logger.error(f"Ошибка в задаче проверки email: {e}", exc_info=True)
