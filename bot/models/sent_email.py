"""Модель для отслеживания отправленных писем."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from bot.models.base import Base


class EmailType(str, PyEnum):
    """Типы писем при заведении поставщика."""
    
    SB_CHECK = "sb_check"       # Проверка СБ
    DOCSINBOX = "docsinbox"     # DocsInBox
    ROAMING = "roaming"         # Роуминг
    DOCUMENTS = "documents"     # Документы поставщику


class SentEmail(Base):
    """Отправленное письмо для отслеживания ответов."""
    
    __tablename__ = "sent_emails"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    
    # Идентификация письма
    message_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    
    # Связь с поставщиком
    supplier_inn: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    supplier_name: Mapped[str] = mapped_column(String(255), nullable=True)
    
    # Тип письма
    email_type: Mapped[EmailType] = mapped_column(Enum(EmailType), nullable=False)
    
    # Получатели
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    cc_recipients: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Тема письма
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    
    # Время отправки
    sent_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    
    # Кто инициировал отправку (для уведомлений)
    telegram_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    
    # ID компании для контекста
    company_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sheet_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    
    # Статус ответа
    reply_received: Mapped[bool] = mapped_column(Boolean, default=False)
    reply_received_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    reply_message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    def __repr__(self) -> str:
        return f"<SentEmail {self.email_type.value} to={self.recipient} inn={self.supplier_inn}>"
