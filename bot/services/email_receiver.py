"""Сервис получения входящих писем через IMAP."""
from __future__ import annotations

import asyncio
import email
import imaplib
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.header import decode_header
from email.message import Message
from pathlib import Path
from typing import List, Optional

from loguru import logger

from bot.config import get_env


# Конфигурация IMAP
IMAP_HOST = get_env("GMAIL_IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(get_env("GMAIL_IMAP_PORT", "993"))
IMAP_USER = get_env("GMAIL_IMAP_USER", "")
IMAP_PASSWORD = get_env("GMAIL_IMAP_PASSWORD", "")


@dataclass
class EmailAttachment:
    """Вложение из входящего письма."""
    filename: str
    content_type: str
    data: bytes
    temp_path: Optional[Path] = None
    
    def save_to_temp(self) -> Path:
        """Сохранить вложение во временный файл."""
        suffix = Path(self.filename).suffix or ""
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(self.data)
            self.temp_path = Path(tmp.name)
        return self.temp_path
    
    def cleanup(self):
        """Удалить временный файл."""
        if self.temp_path and self.temp_path.exists():
            try:
                self.temp_path.unlink()
            except Exception:
                pass


@dataclass
class IncomingEmail:
    """Входящее письмо."""
    uid: str                           # UID письма в IMAP
    message_id: str                    # Message-ID
    in_reply_to: Optional[str]         # In-Reply-To header
    references: List[str]              # References header (список Message-ID)
    from_addr: str                     # От кого
    to_addrs: List[str]                # Кому
    subject: str                       # Тема
    date: Optional[datetime]           # Дата
    body_text: str                     # Текст письма
    body_html: str                     # HTML версия
    attachments: List[EmailAttachment] = field(default_factory=list)
    matched_message_id: Optional[str] = None  # Message-ID нашего письма (заполняется при сопоставлении)
    matched_tracking_code: Optional[str] = None  # Код заявки [ML-XXXXX] из темы
    
    def cleanup_attachments(self):
        """Удалить временные файлы вложений."""
        for att in self.attachments:
            att.cleanup()


def _decode_header_value(value: str) -> str:
    """Декодировать заголовок письма (может быть в разных кодировках)."""
    if not value:
        return ""
    
    try:
        decoded_parts = decode_header(value)
        result = []
        for data, charset in decoded_parts:
            if isinstance(data, bytes):
                result.append(data.decode(charset or "utf-8", errors="replace"))
            else:
                result.append(data)
        return "".join(result)
    except Exception as e:
        logger.warning(f"Ошибка декодирования заголовка: {e}")
        return str(value)


def _parse_email_message(raw_email: bytes, uid: str) -> Optional[IncomingEmail]:
    """Распарсить сырое письмо в структуру IncomingEmail."""
    try:
        msg = email.message_from_bytes(raw_email)
        
        # Извлекаем заголовки
        message_id = msg.get("Message-ID", "")
        in_reply_to = msg.get("In-Reply-To", "")
        references_raw = msg.get("References", "")
        references = references_raw.split() if references_raw else []
        
        from_addr = _decode_header_value(msg.get("From", ""))
        to_addrs = [_decode_header_value(addr.strip()) 
                    for addr in msg.get("To", "").split(",")]
        subject = _decode_header_value(msg.get("Subject", ""))
        
        # Дата
        date_str = msg.get("Date", "")
        email_date = None
        if date_str:
            try:
                from email.utils import parsedate_to_datetime
                email_date = parsedate_to_datetime(date_str)
            except Exception:
                pass
        
        # Извлекаем тело и вложения
        body_text = ""
        body_html = ""
        attachments: List[EmailAttachment] = []
        
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))
                
                # Вложение
                if "attachment" in content_disposition:
                    filename = part.get_filename()
                    if filename:
                        filename = _decode_header_value(filename)
                        payload = part.get_payload(decode=True)
                        if payload:
                            attachments.append(EmailAttachment(
                                filename=filename,
                                content_type=content_type,
                                data=payload,
                            ))
                # Текстовая часть
                elif content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        body_text = payload.decode(charset, errors="replace")
                # HTML часть
                elif content_type == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        body_html = payload.decode(charset, errors="replace")
        else:
            # Не multipart
            content_type = msg.get_content_type()
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                if content_type == "text/plain":
                    body_text = payload.decode(charset, errors="replace")
                elif content_type == "text/html":
                    body_html = payload.decode(charset, errors="replace")
        
        return IncomingEmail(
            uid=uid,
            message_id=message_id,
            in_reply_to=in_reply_to,
            references=references,
            from_addr=from_addr,
            to_addrs=to_addrs,
            subject=subject,
            date=email_date,
            body_text=body_text,
            body_html=body_html,
            attachments=attachments,
        )
        
    except Exception as e:
        logger.error(f"Ошибка парсинга письма: {e}", exc_info=True)
        return None


class EmailReceiver:
    """Сервис получения входящих писем через IMAP."""
    
    def __init__(self):
        self._connection: Optional[imaplib.IMAP4_SSL] = None
    
    def _check_config(self) -> bool:
        """Проверить настройки IMAP."""
        if not IMAP_USER or not IMAP_PASSWORD:
            logger.warning("IMAP не настроен: GMAIL_IMAP_USER или GMAIL_IMAP_PASSWORD не заданы")
            return False
        return True
    
    def _connect(self) -> Optional[imaplib.IMAP4_SSL]:
        """Подключиться к IMAP серверу."""
        logger.debug(f"Подключение к IMAP: {IMAP_HOST}:{IMAP_PORT}")
        
        try:
            conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
            conn.login(IMAP_USER, IMAP_PASSWORD)
            logger.info("IMAP: подключение успешно")
            return conn
        except imaplib.IMAP4.error as e:
            logger.error(f"IMAP ошибка аутентификации: {e}")
            return None
        except Exception as e:
            logger.error(f"IMAP ошибка подключения: {e}")
            return None
    
    def _disconnect(self, conn: imaplib.IMAP4_SSL):
        """Отключиться от IMAP сервера."""
        try:
            conn.logout()
        except Exception:
            pass
    
    async def fetch_replies(self, since_days: int = 7) -> List[IncomingEmail]:
        """
        Получить письма-ответы на наши отправленные письма.
        
        Ищет письма с In-Reply-To header, которые ссылаются на наши Message-ID.
        
        Args:
            since_days: За сколько дней искать письма.
            
        Returns:
            Список входящих писем.
        """
        logger.info(f"fetch_replies: checking emails for last {since_days} days")
        
        if not self._check_config():
            return []
        
        def _fetch_sync() -> List[IncomingEmail]:
            conn = self._connect()
            if not conn:
                return []
            
            try:
                # Выбираем INBOX
                conn.select("INBOX")
                
                # Ищем письма за последние N дней
                from datetime import timedelta
                since_date = (datetime.now() - timedelta(days=since_days)).strftime("%d-%b-%Y")
                
                # Поиск всех писем с In-Reply-To header за период
                # IMAP не умеет искать по наличию header, поэтому получаем все за период
                status, message_ids = conn.search(None, f'(SINCE "{since_date}")')
                
                if status != "OK":
                    logger.error(f"IMAP search failed: {status}")
                    return []
                
                emails: List[IncomingEmail] = []
                
                for uid in message_ids[0].split():
                    try:
                        # Получаем письмо
                        status, data = conn.fetch(uid, "(RFC822)")
                        if status != "OK":
                            continue
                        
                        raw_email = data[0][1]
                        parsed = _parse_email_message(raw_email, uid.decode())
                        
                        if not parsed:
                            continue
                        
                        # Добавляем письмо если:
                        # 1. Есть In-Reply-To header
                        # 2. ИЛИ тема начинается с Re:/RE: (для систем без In-Reply-To)
                        is_reply = bool(parsed.in_reply_to)
                        is_re_subject = parsed.subject and parsed.subject.upper().startswith(("RE:", "RE :", "FW:", "FWD:"))
                        
                        if is_reply or is_re_subject:
                            emails.append(parsed)
                            if is_reply:
                                logger.debug(f"Найден ответ: {parsed.subject} (in_reply_to={parsed.in_reply_to})")
                            else:
                                logger.debug(f"Найден ответ (по теме): {parsed.subject}")
                    
                    except Exception as e:
                        logger.warning(f"Ошибка обработки письма {uid}: {e}")
                        continue
                
                logger.info(f"Найдено писем-ответов: {len(emails)}")
                return emails
                
            finally:
                self._disconnect(conn)
        
        # Выполняем синхронную операцию в отдельном потоке
        return await asyncio.to_thread(_fetch_sync)
    
    async def fetch_unprocessed_replies(self, since_days: int = 7) -> List[IncomingEmail]:
        """
        Получить только непрочитанные ответы на наши письма.
        
        Основной способ поиска: по коду заявки [ML-XXXXX] в теме.
        Fallback: по In-Reply-To / References.
        
        Returns:
            Список писем, которые являются ответами на наши отправленные.
        """
        import re
        from sqlalchemy import select, or_
        from bot.models.base import async_session_factory
        from bot.models.sent_email import SentEmail
        from bot.services.email_service import extract_tracking_code
        
        # Получаем все письма-ответы
        all_replies = await self.fetch_replies(since_days)
        
        if not all_replies:
            return []
        
        # Получаем данные наших отправленных писем из БД
        async with async_session_factory() as session:
            result = await session.execute(
                select(SentEmail).where(SentEmail.reply_received == False)
            )
            pending_emails = result.scalars().all()
            
            # Также получаем уже обработанные reply_message_id, чтобы не обрабатывать повторно
            processed_result = await session.execute(
                select(SentEmail.reply_message_id).where(
                    SentEmail.reply_received == True,
                    SentEmail.reply_message_id.isnot(None)
                )
            )
            processed_reply_ids = {r[0] for r in processed_result.fetchall()}
        
        if not pending_emails:
            logger.debug("Нет ожидающих ответа писем")
            return []
        
        logger.debug(f"Ожидающих ответа: {len(pending_emails)}, уже обработанных ответов: {len(processed_reply_ids)}")
        
        # Строим индексы для поиска
        our_message_ids = {e.message_id for e in pending_emails}
        
        # tracking_code -> список отправленных писем (для поиска по коду в теме)
        code_to_emails: dict[str, list] = {}
        for email_obj in pending_emails:
            if hasattr(email_obj, 'tracking_code') and email_obj.tracking_code:
                code = email_obj.tracking_code
                if code not in code_to_emails:
                    code_to_emails[code] = []
                code_to_emails[code].append(email_obj)
        
        # Фильтруем только ответы на наши письма
        matched_replies: List[IncomingEmail] = []
        matched_message_ids: set[str] = set()  # Для избежания дублей
        
        for reply in all_replies:
            # Пропускаем уже обработанные входящие письма
            if reply.message_id in processed_reply_ids:
                logger.debug(f"Пропускаем уже обработанный ответ: {reply.message_id}")
                continue
            
            matched_email = None
            matched_code = None
            subject_lower = reply.subject.lower() if reply.subject else ""
            
            # 1. ОСНОВНОЙ СПОСОБ: ищем код [ML-XXXXX] в теме
            tracking_code = extract_tracking_code(reply.subject)
            if tracking_code and tracking_code in code_to_emails:
                # Определяем тип письма по ключевым словам
                for email_obj in code_to_emails[tracking_code]:
                    if email_obj.message_id in matched_message_ids:
                        continue
                    
                    # Сопоставляем тип письма с темой
                    # ВАЖНО: порядок проверки имеет значение! 
                    # roaming должен проверяться ДО docsinbox, т.к. "Настройка роуминга" содержит "настройк"
                    email_type = email_obj.email_type.value
                    if email_type == "roaming" and "роуминг" in subject_lower:
                        matched_email = email_obj.message_id
                        matched_code = tracking_code
                        logger.debug(f"Match by code: roaming, code={tracking_code}")
                        break
                    elif email_type == "sb_check" and ("сб" in subject_lower or "проверк" in subject_lower):
                        matched_email = email_obj.message_id
                        matched_code = tracking_code
                        logger.debug(f"Match by code: sb_check, code={tracking_code}")
                        break
                    elif email_type == "docsinbox" and (
                        "docsinbox" in subject_lower or 
                        ("настройк" in subject_lower and "роуминг" not in subject_lower)
                    ):
                        # "настройк" только если НЕТ "роуминг" в теме
                        matched_email = email_obj.message_id
                        matched_code = tracking_code
                        logger.debug(f"Match by code: docsinbox, code={tracking_code}")
                        break
                    elif email_type == "documents" and "документ" in subject_lower:
                        matched_email = email_obj.message_id
                        matched_code = tracking_code
                        logger.debug(f"Match by code: documents, code={tracking_code}")
                        break
                
                # Если код найден, но не можем определить тип — берем первый неотвеченный
                if not matched_email and code_to_emails[tracking_code]:
                    for email_obj in code_to_emails[tracking_code]:
                        if email_obj.message_id not in matched_message_ids:
                            matched_email = email_obj.message_id
                            matched_code = tracking_code
                            logger.debug(f"Match by code (any type): code={tracking_code}")
                            break
            
            # 2. Fallback: проверяем In-Reply-To
            if not matched_email:
                if reply.in_reply_to and reply.in_reply_to in our_message_ids:
                    matched_email = reply.in_reply_to
                    logger.debug(f"Match by In-Reply-To: {matched_email}")
            
            # 3. Fallback: проверяем References
            if not matched_email:
                for ref in reply.references:
                    if ref in our_message_ids:
                        matched_email = ref
                        logger.debug(f"Match by References: {matched_email}")
                        break
            
            if matched_email and matched_email not in matched_message_ids:
                reply.matched_message_id = matched_email
                reply.matched_tracking_code = matched_code
                matched_replies.append(reply)
                matched_message_ids.add(matched_email)
        
        logger.info(f"Из {len(all_replies)} ответов {len(matched_replies)} относятся к нашим письмам")
        return matched_replies


# Singleton instance
email_receiver = EmailReceiver()
