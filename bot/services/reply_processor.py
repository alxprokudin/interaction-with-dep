"""Сервис обработки ответов на письма."""
from __future__ import annotations

import asyncio
import re
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


def extract_reply_text(full_text: str) -> str:
    """
    Извлечь только текст ответа, убрав цитирование исходного письма.
    
    Паттерны цитирования:
    - Строки, начинающиеся с ">"
    - Разделители типа "--- Original Message ---", "On ... wrote:"
    - Дата + время + email в угловых скобках (начало цитаты)
    """
    if not full_text:
        return "(текст отсутствует)"
    
    lines = full_text.split('\n')
    reply_lines = []
    in_quote = False
    
    for line in lines:
        stripped = line.strip()
        
        # Пропускаем пустые строки в начале
        if not reply_lines and not stripped:
            continue
        
        # Маркеры начала цитирования
        quote_markers = [
            r'^>',  # Цитата с >
            r'^On .+ wrote:',  # "On Mon, Jan 1 wrote:"
            r'^\d{1,2}[\./]\d{1,2}[\./]\d{2,4}.*<.*@.*>',  # Дата + email
            r'^---+\s*(Original|Исходное)',  # --- Original Message ---
            r'^_{3,}',  # _____ разделитель
            r'^From:.*@',  # From: email
            r'^Отправлено:',  # Outlook
            r'^Sent:',  # Outlook EN
        ]
        
        # Проверяем, начинается ли цитирование
        for pattern in quote_markers:
            if re.match(pattern, stripped, re.IGNORECASE):
                in_quote = True
                break
        
        if in_quote:
            # Дальше идёт цитата — пропускаем
            continue
        
        reply_lines.append(line)
    
    # Убираем пустые строки в конце
    while reply_lines and not reply_lines[-1].strip():
        reply_lines.pop()
    
    result = '\n'.join(reply_lines).strip()
    return result if result else "(текст отсутствует)"


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
            # Приоритет: matched_message_id (заполняется при fallback-поиске) > in_reply_to > references
            original_message_id = getattr(reply, 'matched_message_id', None) or reply.in_reply_to
            
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
                
                # Используем tracking_code как основной способ поиска строки
                tracking_code = getattr(sent_email, 'tracking_code', None) or reply.matched_tracking_code
                
                if tracking_code:
                    # Обновляем по коду заявки (более надежно)
                    updated = await google_sheets_service.update_reply_by_tracking_code(
                        sheet_id=sent_email.sheet_id,
                        tracking_code=tracking_code,
                        email_type=sent_email.email_type.value,
                    )
                else:
                    # Fallback: обновляем по ИНН
                    updated = await google_sheets_service.update_supplier_reply_status(
                        sheet_id=sent_email.sheet_id,
                        supplier_inn=sent_email.supplier_inn,
                        email_type=sent_email.email_type.value,
                    )
                
                if not updated:
                    logger.warning(f"Не удалось обновить статус в таблице для {sent_email.supplier_inn}")
            
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
        
        # Извлекаем только текст ответа (без цитирования)
        body_text = extract_reply_text(reply.body_text)
        
        # Ограничиваем длину
        if len(body_text) > 1500:
            body_text = body_text[:1500] + "...\n\n_(текст обрезан)_"
        
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
