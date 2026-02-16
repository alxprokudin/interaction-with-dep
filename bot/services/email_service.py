"""Сервис отправки email через SMTP."""
from __future__ import annotations

import smtplib
import asyncio
import uuid
import zipfile
import tempfile
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass, field

from loguru import logger

from bot.config import get_env


# Конфигурация SMTP
SMTP_EMAIL = get_env("SMTP_EMAIL", "")
SMTP_PASSWORD = get_env("SMTP_PASSWORD", "")
SMTP_HOST = get_env("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(get_env("SMTP_PORT", "587"))

# Тестовый режим
TEST_EMAIL = get_env("TEST_EMAIL", "alxprokudin@gmail.com")
EMAIL_TEST_MODE = get_env("EMAIL_TEST_MODE", "true").lower() == "true"

# Реквизиты компании (константы)
COMPANY_NAME = "ООО \"Гастрономия\""
COMPANY_INN = "9704003050"
COMPANY_KPP = "772201001"
COMPANY_GUID = "2BE76ddf68ec9c14cfdb09f08448dda32c7"

# CC адреса для всех писем
DEFAULT_CC = [
    "oz@mnogolososya.ru",
    "pgadyaka@mnogolososya.ru",
    "bkhatchukaev@mnogolososya.ru",
    "karina.petrakova@mnogolososya.ru",
    "aprokudin@mnogolososya.ru",
]

# ID папки с документами для заключения договора (для вложений в письмо)
DOCUMENTS_FOLDER_ID = "18bkmRQBvwXxp5HGjzYQASksFSf4__V4C"

# Ссылка на полный пакет документов компании
DOCUMENTS_FULL_LINK = "https://drive.google.com/drive/folders/1-Iw9csAjHGjh0oRuuW4k2kOcBikzp878"


@dataclass
class SupplierData:
    """Данные поставщика для писем."""
    name: str
    inn: str
    kpp: str
    contact_name: str = ""
    contact_phone: str = ""
    contact_email: str = ""
    delivery_points: str = ""


@dataclass
class EmailMessage:
    """Структура email сообщения.
    
    attachments может быть:
    - List[Path] — имя файла берётся из path.name
    - List[tuple[str, Path]] — первый элемент используется как имя файла
    """
    to: List[str]
    cc: List[str]
    subject: str
    body: str
    attachments: list = None
    message_id: str = field(default_factory=lambda: "")
    
    def __post_init__(self):
        if self.attachments is None:
            self.attachments = []


def generate_tracking_code() -> str:
    """
    Генерирует уникальный код заявки для отслеживания.
    
    Формат: ML-XXXXX (5 символов: буквы + цифры)
    Этот код включается в тему всех писем и позволяет 100% найти ответ.
    """
    import random
    import string
    chars = string.ascii_uppercase + string.digits
    code = ''.join(random.choices(chars, k=5))
    tracking_code = f"ML-{code}"
    logger.debug(f"generate_tracking_code: {tracking_code}")
    return tracking_code


def generate_message_id(email_type: str, supplier_inn: str) -> str:
    """
    Генерирует уникальный Message-ID для письма.
    
    Формат: <uuid>.<email_type>.<inn>@mnogolososya.ru
    Это позволит идентифицировать ответы по In-Reply-To header.
    """
    unique_id = uuid.uuid4().hex[:12]
    message_id = f"<{unique_id}.{email_type}.{supplier_inn}@mnogolososya.ru>"
    logger.debug(f"generate_message_id: {message_id}")
    return message_id


def extract_tracking_code(subject: str) -> Optional[str]:
    """
    Извлечь код заявки [ML-XXXXX] из темы письма.
    
    Returns:
        Код заявки или None.
    """
    import re
    match = re.search(r'\[ML-([A-Z0-9]{5})\]', subject)
    if match:
        return f"ML-{match.group(1)}"
    return None


def parse_message_id(message_id: str) -> Optional[tuple[str, str]]:
    """
    Извлечь email_type и supplier_inn из Message-ID.
    
    Returns:
        (email_type, supplier_inn) или None если формат не распознан.
    """
    try:
        # Убираем < и >
        clean_id = message_id.strip("<>")
        # Формат: uuid.email_type.inn@domain
        parts = clean_id.split("@")[0].split(".")
        if len(parts) >= 3:
            email_type = parts[1]
            supplier_inn = parts[2]
            return (email_type, supplier_inn)
    except Exception as e:
        logger.warning(f"Не удалось распарсить Message-ID {message_id}: {e}")
    return None


def _check_smtp_config() -> bool:
    """Проверить настройки SMTP."""
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        logger.warning("SMTP не настроен: SMTP_EMAIL или SMTP_PASSWORD не заданы")
        return False
    return True


def _create_mime_message(
    sender: str,
    to: List[str],
    cc: List[str],
    subject: str,
    body: str,
    attachments: List[Path] = None,
    message_id: str = None,
) -> MIMEMultipart:
    """Создать MIME сообщение для SMTP."""
    
    message = MIMEMultipart()
    message["From"] = sender
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    
    # Добавляем Message-ID для отслеживания ответов
    if message_id:
        message["Message-ID"] = message_id
    
    # Тело письма
    message.attach(MIMEText(body, "plain", "utf-8"))
    
    # Вложения
    if attachments:
        from email.header import Header
        from urllib.parse import quote
        
        for item in attachments:
            # Поддержка двух форматов: Path или (name, Path)
            if isinstance(item, tuple):
                display_name, file_path = item
            else:
                file_path = item
                display_name = file_path.name
            
            if file_path.exists():
                with open(file_path, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                
                # Кодируем имя файла по RFC 2231 для поддержки кириллицы
                # filename* использует URL-encoding для UTF-8
                encoded_filename = quote(display_name, safe='')
                part.add_header(
                    "Content-Disposition",
                    "attachment",
                    filename=("utf-8", "", display_name),  # RFC 2231 формат
                )
                message.attach(part)
    
    return message


def _send_via_smtp(message: MIMEMultipart, recipients: List[str]) -> bool:
    """Отправить сообщение через SMTP (синхронно)."""
    logger.debug(f"_send_via_smtp: host={SMTP_HOST}, port={SMTP_PORT}, from={SMTP_EMAIL}")
    
    try:
        if SMTP_PORT == 465:
            # SSL (порт 465)
            logger.debug("Используем SMTP_SSL (порт 465)")
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.login(SMTP_EMAIL, SMTP_PASSWORD)
                server.sendmail(SMTP_EMAIL, recipients, message.as_string())
        else:
            # TLS (порт 587)
            logger.debug("Используем SMTP + STARTTLS (порт 587)")
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.starttls()
                server.login(SMTP_EMAIL, SMTP_PASSWORD)
                server.sendmail(SMTP_EMAIL, recipients, message.as_string())
        return True
    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP аутентификация не удалась: {e.smtp_code} {e.smtp_error}")
        return False
    except smtplib.SMTPException as e:
        logger.error(f"SMTP ошибка: {type(e).__name__}: {e}")
        return False
    except TimeoutError:
        logger.error(f"SMTP таймаут: не удалось подключиться к {SMTP_HOST}:{SMTP_PORT}")
        return False
    except Exception as e:
        logger.error(f"Ошибка отправки через SMTP: {type(e).__name__}: {e}")
        return False


async def send_email(email: EmailMessage, sender: str = None) -> bool:
    """
    Отправить email через SMTP.
    
    В тестовом режиме (EMAIL_TEST_MODE=true) все письма отправляются на TEST_EMAIL.
    
    Returns:
        True если отправлено успешно
    """
    logger.info(f"send_email: to={email.to}, subject={email.subject[:50]}...")
    
    # Проверяем настройки SMTP
    if not _check_smtp_config():
        logger.error("SMTP не настроен, письмо не отправлено")
        _log_email(email)
        return False
    
    # Используем SMTP_EMAIL как отправителя
    sender = sender or SMTP_EMAIL
    
    # Тестовый режим — меняем получателей
    original_to = email.to.copy()
    original_cc = email.cc.copy()
    
    if EMAIL_TEST_MODE:
        email.to = [TEST_EMAIL]
        email.cc = []
        logger.warning(
            f"TEST MODE: письмо перенаправлено на {TEST_EMAIL} "
            f"(оригинал: to={original_to}, cc={original_cc})"
        )
    
    # Создаём MIME сообщение
    message = _create_mime_message(
        sender=sender,
        to=email.to,
        cc=email.cc,
        subject=email.subject,
        body=email.body,
        attachments=email.attachments,
        message_id=email.message_id,
    )
    
    # Собираем всех получателей (to + cc)
    all_recipients = email.to + email.cc
    
    try:
        # Отправляем в отдельном потоке (SMTP синхронный)
        success = await asyncio.to_thread(_send_via_smtp, message, all_recipients)
        
        if success:
            logger.info(f"Email отправлен успешно: to={email.to}")
            return True
        else:
            _log_email(email)
            return False
        
    except Exception as e:
        error_text = str(e).replace("{", "{{").replace("}", "}}")
        logger.error(f"Ошибка отправки email: {error_text}")
        _log_email(email)
        return False


def _log_email(email: EmailMessage) -> None:
    """Логировать email если отправка не удалась."""
    logger.info(
        f"=== EMAIL (не отправлено) ===\n"
        f"To: {', '.join(email.to)}\n"
        f"Cc: {', '.join(email.cc)}\n"
        f"Subject: {email.subject}\n"
        f"Attachments: {[str(a) for a in email.attachments]}\n"
        f"---\n{email.body}\n"
        f"=== END EMAIL ==="
    )


async def save_sent_email(
    message_id: str,
    tracking_code: str,
    supplier_inn: str,
    supplier_name: str,
    email_type: str,
    recipient: str,
    cc_recipients: List[str],
    subject: str,
    telegram_user_id: int,
    company_id: Optional[int] = None,
    sheet_id: Optional[str] = None,
) -> bool:
    """
    Сохранить информацию об отправленном письме в БД для отслеживания ответов.
    
    Returns:
        True если сохранено успешно.
    """
    from bot.models.base import async_session_factory
    from bot.models.sent_email import SentEmail, EmailType
    
    logger.debug(f"save_sent_email: message_id={message_id}, tracking_code={tracking_code}, type={email_type}")
    
    try:
        # Преобразуем строку в EmailType
        email_type_enum = EmailType(email_type)
        
        async with async_session_factory() as session:
            sent_email = SentEmail(
                message_id=message_id,
                tracking_code=tracking_code,
                supplier_inn=supplier_inn,
                supplier_name=supplier_name,
                email_type=email_type_enum,
                recipient=recipient,
                cc_recipients=", ".join(cc_recipients) if cc_recipients else None,
                subject=subject,
                telegram_user_id=telegram_user_id,
                company_id=company_id,
                sheet_id=sheet_id,
            )
            session.add(sent_email)
            await session.commit()
            logger.info(f"Сохранено отправленное письмо: {email_type} для ИНН {supplier_inn}")
            return True
    except Exception as e:
        logger.error(f"Ошибка сохранения письма в БД: {e}", exc_info=True)
        return False


async def get_sent_email_by_message_id(message_id: str):
    """
    Найти отправленное письмо по Message-ID.
    
    Returns:
        SentEmail или None.
    """
    from sqlalchemy import select
    from bot.models.base import async_session_factory
    from bot.models.sent_email import SentEmail
    
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(SentEmail).where(SentEmail.message_id == message_id)
            )
            return result.scalar_one_or_none()
    except Exception as e:
        logger.error(f"Ошибка поиска письма по Message-ID: {e}")
        return None


async def mark_reply_received(message_id: str, reply_message_id: str = None) -> bool:
    """
    Отметить, что на письмо получен ответ.
    
    Returns:
        True если успешно обновлено.
    """
    from bot.models.base import async_session_factory
    from bot.models.sent_email import SentEmail
    from sqlalchemy import select
    
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(SentEmail).where(SentEmail.message_id == message_id)
            )
            sent_email = result.scalar_one_or_none()
            
            if sent_email:
                sent_email.reply_received = True
                sent_email.reply_received_at = datetime.now(timezone.utc)
                if reply_message_id:
                    sent_email.reply_message_id = reply_message_id
                await session.commit()
                logger.info(f"Отмечен ответ на письмо: {message_id}")
                return True
            else:
                logger.warning(f"Письмо не найдено: {message_id}")
                return False
    except Exception as e:
        logger.error(f"Ошибка отметки ответа: {e}", exc_info=True)
        return False


# ============ ШАБЛОНЫ ПИСЕМ ДЛЯ ЗАВЕДЕНИЯ ПОСТАВЩИКА ============

def create_email_1_sb_check(
    supplier: SupplierData, 
    card_path: Optional[Path] = None,
    tracking_code: str = "",
) -> EmailMessage:
    """
    Письмо 1: Проверка СБ.
    To: Pak, Olga <Ol.Pak@x5.ru>
    """
    body = f"""Добрый день! Просим направить нового поставщика на проверку СБ:

Наименование: {supplier.name}
ИНН: {supplier.inn}
КПП: {supplier.kpp}

Наши реквизиты: {COMPANY_NAME}
ИНН: {COMPANY_INN}
КПП: {COMPANY_KPP}
GUID: {COMPANY_GUID}
"""
    
    attachments = [card_path] if card_path and card_path.exists() else []
    
    # Код заявки в теме для отслеживания ответов
    code_prefix = f"[{tracking_code}] " if tracking_code else ""
    
    return EmailMessage(
        to=["Ol.Pak@x5.ru"],
        cc=DEFAULT_CC.copy(),
        subject=f"{code_prefix}Новый поставщик на проверку СБ - \"{supplier.name}\" {supplier.inn}",
        body=body,
        attachments=attachments,
    )


def create_email_2_docsinbox(supplier: SupplierData, tracking_code: str = "") -> EmailMessage:
    """
    Письмо 2: Настройка в DocsInBox.
    To: m.chernykh@docsinbox.ru
    """
    body = f"""Добрый день! Прошу выполнить настройки нового партнера:

Наименование: {supplier.name}
ИНН: {supplier.inn}
КПП: {supplier.kpp}
ФИО Менеджера: {supplier.contact_name}
Тел. Менеджера: {supplier.contact_phone}
Email Менеджера: {supplier.contact_email}
Точки доставки: {supplier.delivery_points}

Наши реквизиты: {COMPANY_NAME}
ИНН: {COMPANY_INN}
КПП: {COMPANY_KPP}
GUID: {COMPANY_GUID}
"""
    
    code_prefix = f"[{tracking_code}] " if tracking_code else ""
    
    return EmailMessage(
        to=["m.chernykh@docsinbox.ru"],
        cc=DEFAULT_CC.copy(),
        subject=f"{code_prefix}Настройка поставщика для МЛ - {supplier.name} {supplier.inn}",
        body=body,
    )


def create_email_3_roaming(supplier: SupplierData, tracking_code: str = "") -> EmailMessage:
    """
    Письмо 3: Настройка роуминга.
    To: edi_request@skbkontur.ru
    """
    body = f"""Добрый день! Просим настроить роуминг с новым поставщиком:

Наименование: {supplier.name}
ИНН: {supplier.inn}
КПП: {supplier.kpp}

Наши реквизиты: {COMPANY_NAME}
ИНН: {COMPANY_INN}
КПП: {COMPANY_KPP}
GUID: {COMPANY_GUID}
"""
    
    cc = DEFAULT_CC.copy()
    cc.insert(0, "l.ermakova@skbkontur.ru")
    
    code_prefix = f"[{tracking_code}] " if tracking_code else ""
    
    return EmailMessage(
        to=["edi_request@skbkontur.ru"],
        cc=cc,
        subject=f"{code_prefix}Настройка роуминга - {supplier.name} {supplier.inn}",
        body=body,
    )


def create_email_4_documents(
    supplier: SupplierData,
    document_files: Optional[List[Path]] = None,
    tracking_code: str = "",
) -> EmailMessage:
    """
    Письмо 4: Документы для заключения договора.
    To: email поставщика
    """
    body = f"""Добрый день!

Направляем Вам документы, необходимые для заключения договора поставки с {COMPANY_NAME}.

К письму приложены:
- Договор поставки
- Комментарии к договору (файл «Договор поставки_Комментарии.xlsx»)
- Карточка организации

Полный пакет учредительных документов компании доступен по ссылке:
{DOCUMENTS_FULL_LINK}

При наличии замечаний к условиям договора просим направить их в форме Протокола разногласий. Обращаем внимание, что перечень существенных условий, не подлежащих изменению, указан в файле «Договор поставки_Комментарии.xlsx».

Уведомляем Вас о смене фирменного наименования: с 21.05.2024 г. ООО «Много лосося» переименовано в {COMPANY_NAME}. Регистрационные и банковские реквизиты остались без изменений.

С уважением,
{COMPANY_NAME}
"""
    
    attachments = document_files or []
    code_prefix = f"[{tracking_code}] " if tracking_code else ""
    
    return EmailMessage(
        to=[supplier.contact_email] if supplier.contact_email else [],
        cc=DEFAULT_CC.copy(),
        subject=f"{code_prefix}Документы {COMPANY_NAME} (Рестораны Много лосося) - {supplier.name} {supplier.inn}",
        body=body,
        attachments=attachments,
    )


def create_email_contract_completed(
    supplier_name: str,
    supplier_inn: str,
    attachments: Optional[List[Path]] = None,
) -> EmailMessage:
    """
    Письмо бухгалтеру о заключении договора с поставщиком.
    
    To: Ol.Pak@x5.ru
    Cc: opak@mnogolososya.ru
    """
    body = f"""Добрый день!

Уведомляем о заключении договора поставки с контрагентом:

Наименование: {supplier_name}
ИНН: {supplier_inn}

Договор и протокол разногласий (при наличии) приложены к письму.

Просим завершить настройки в учётной системе и сообщить о возможности начала закупок.

С уважением,
{COMPANY_NAME}
"""
    
    logger.info(f"create_email_contract_completed: supplier={supplier_name}, inn={supplier_inn}")
    
    cc_list = ["opak@mnogolososya.ru"] + DEFAULT_CC.copy()
    
    return EmailMessage(
        to=["Ol.Pak@x5.ru"],
        cc=cc_list,
        subject=f"Заключён договор с поставщиком - {supplier_name} {supplier_inn}",
        body=body,
        attachments=attachments or [],
    )


def _create_documents_archive(files_list: List[tuple], archive_name: str = "Документы_для_договора.zip") -> Optional[Path]:
    """
    Создать zip-архив из списка файлов.
    
    Args:
        files_list: Список кортежей (original_filename, temp_path)
        archive_name: Имя архива
    
    Returns:
        Path к созданному архиву или None
    """
    if not files_list:
        return None
    
    try:
        # Создаём временный файл для архива
        tmp_archive = tempfile.NamedTemporaryFile(
            delete=False, 
            suffix=".zip", 
            prefix="docs_"
        )
        archive_path = Path(tmp_archive.name)
        tmp_archive.close()
        
        logger.info(f"Создаём архив {archive_name} из {len(files_list)} файлов")
        
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for original_filename, temp_path in files_list:
                if temp_path and temp_path.exists():
                    # Добавляем файл в архив с оригинальным именем
                    zf.write(temp_path, arcname=original_filename)
                    logger.debug(f"Добавлен в архив: {original_filename}")
        
        archive_size = archive_path.stat().st_size
        logger.info(f"Архив создан: {archive_path}, размер: {archive_size / 1024 / 1024:.2f} MB")
        
        return archive_path
        
    except Exception as e:
        logger.error(f"Ошибка создания архива: {e}", exc_info=True)
        return None


async def send_supplier_registration_emails(
    supplier: SupplierData,
    card_path: Optional[Path] = None,
    telegram_user_id: int = 0,
    company_id: Optional[int] = None,
    sheet_id: Optional[str] = None,
    tracking_code: Optional[str] = None,
) -> dict:
    """
    Отправить все 4 письма для регистрации поставщика.
    
    Args:
        supplier: Данные поставщика
        card_path: Путь к карточке поставщика
        telegram_user_id: ID пользователя Telegram (для уведомлений об ответах)
        company_id: ID компании
        sheet_id: ID таблицы Google Sheets
        tracking_code: Уникальный код заявки (если не указан — генерируется)
    
    Returns:
        Словарь с результатами: {"email_1": True/False, ..., "tracking_code": "ML-XXXXX"}
    """
    # Генерируем код заявки один раз для всех писем
    if not tracking_code:
        tracking_code = generate_tracking_code()
    
    logger.info(f"send_supplier_registration_emails: supplier={supplier.name}, user_id={telegram_user_id}, tracking_code={tracking_code}")
    
    results = {"tracking_code": tracking_code}
    downloaded_files: list[Path] = []
    
    async def send_and_save(email: EmailMessage, email_type: str) -> bool:
        """Отправить письмо и сохранить в БД."""
        # Генерируем Message-ID
        message_id = generate_message_id(email_type, supplier.inn)
        email.message_id = message_id
        
        # Отправляем
        success = await send_email(email)
        
        # Сохраняем в БД (даже если не отправлено — для отладки)
        if telegram_user_id:
            await save_sent_email(
                message_id=message_id,
                tracking_code=tracking_code,
                supplier_inn=supplier.inn,
                supplier_name=supplier.name,
                email_type=email_type,
                recipient=email.to[0] if email.to else "",
                cc_recipients=email.cc,
                subject=email.subject,
                telegram_user_id=telegram_user_id,
                company_id=company_id,
                sheet_id=sheet_id,
            )
        
        return success
    
    try:
        # Письмо 1: Проверка СБ
        email_1 = create_email_1_sb_check(supplier, card_path, tracking_code)
        results["email_1_sb"] = await send_and_save(email_1, "sb_check")
        
        # Письмо 2: DocsInBox
        email_2 = create_email_2_docsinbox(supplier, tracking_code)
        results["email_2_docsinbox"] = await send_and_save(email_2, "docsinbox")
        
        # Письмо 3: Роуминг
        email_3 = create_email_3_roaming(supplier, tracking_code)
        results["email_3_roaming"] = await send_and_save(email_3, "roaming")
        
        # Письмо 4: Документы для поставщика
        if supplier.contact_email:
            # Скачиваем все файлы из папки с документами (без подпапок)
            from bot.services.google_drive import list_files_in_folder, download_file_from_drive
            
            logger.info(f"Получаем файлы из папки: {DOCUMENTS_FOLDER_ID}")
            files = await asyncio.to_thread(list_files_in_folder, DOCUMENTS_FOLDER_ID)
            
            # Фильтруем только файлы (не папки)
            document_files = [f for f in files if f.get("mimeType") != "application/vnd.google-apps.folder"]
            logger.info(f"Найдено файлов для вложения: {len(document_files)}")
            
            if document_files:
                for file_info in document_files:
                    file_id = file_info.get("id")
                    filename = file_info.get("name")
                    mime_type = file_info.get("mimeType", "")
                    
                    # Пропускаем Google Docs/Sheets (требуют экспорта)
                    if "google-apps" in mime_type:
                        logger.warning(f"Пропускаем Google документ: {filename}")
                        continue
                    
                    logger.debug(f"Скачиваем: {filename}")
                    result = await asyncio.to_thread(download_file_from_drive, file_id, filename)
                    
                    if result:
                        original_name, file_path = result
                        if file_path.exists():
                            downloaded_files.append((original_name, file_path))
                            logger.debug(f"Скачан: {original_name}, размер: {file_path.stat().st_size / 1024:.1f} KB")
                
                if downloaded_files:
                    total_size = sum(f[1].stat().st_size for f in downloaded_files) / 1024 / 1024
                    logger.info(f"Всего скачано {len(downloaded_files)} файлов, общий размер: {total_size:.2f} MB")
                    email_4 = create_email_4_documents(supplier, downloaded_files, tracking_code)
                    results["email_4_documents"] = await send_and_save(email_4, "documents")
                else:
                    logger.error("Не удалось скачать ни одного документа")
                    results["email_4_documents"] = False
            else:
                logger.error(f"Файлы не найдены в папке {DOCUMENTS_FOLDER_ID}")
                results["email_4_documents"] = False
        else:
            logger.warning("Email поставщика не указан, письмо 4 не отправлено")
            results["email_4_documents"] = False
        
        sent_count = sum(1 for v in results.values() if v)
        logger.info(f"Отправлено {sent_count}/4 писем для поставщика {supplier.name}")
        
    finally:
        # Удаляем временные файлы
        for item in downloaded_files:
            try:
                _, file_path = item if isinstance(item, tuple) else (None, item)
                if file_path.exists():
                    file_path.unlink()
                    logger.debug(f"Временный файл удалён: {file_path.name}")
            except Exception:
                pass
    
    return results
