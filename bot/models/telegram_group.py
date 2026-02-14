"""Модель Telegram-группы для уведомлений."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base

if TYPE_CHECKING:
    from bot.models.company import Company


class TelegramGroup(Base):
    """Telegram-группа, куда бот может отправлять уведомления."""

    __tablename__ = "telegram_groups"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    
    # Telegram chat_id группы (отрицательное число для групп/супергрупп)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    
    # Название группы (сохраняется при добавлении бота)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    
    # Включены ли уведомления для этой группы
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Дата добавления бота в группу
    added_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    # Связи
    company: Mapped["Company"] = relationship("Company", back_populates="telegram_groups")
