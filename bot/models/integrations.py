"""Модель интеграций компании (Google Drive, Sheets и др.)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base

if TYPE_CHECKING:
    from bot.models.company import Company


class CompanyIntegrations(Base):
    """Настройки интеграций для компании."""

    __tablename__ = "company_integrations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"), unique=True, nullable=False
    )

    # Google Drive
    google_drive_folder_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    google_drive_folder_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )

    # Google Sheets — основная таблица для заявок на проработку
    google_sheet_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    google_sheet_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    # Название листа внутри таблицы (по умолчанию первый лист)
    google_sheet_worksheet: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )

    # Статус проверки доступа
    google_drive_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    google_sheet_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    # Дополнительные заметки
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, onupdate=lambda: datetime.now(timezone.utc)
    )

    # Связи
    company: Mapped["Company"] = relationship("Company", back_populates="integrations")
