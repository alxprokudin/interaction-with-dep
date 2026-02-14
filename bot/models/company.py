"""Модели компании и настроек."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base

if TYPE_CHECKING:
    from bot.models.integrations import CompanyIntegrations
    from bot.models.join_request import JoinRequest
    from bot.models.notification_settings import NotificationPosition
    from bot.models.position import Position
    from bot.models.telegram_group import TelegramGroup
    from bot.models.user import User


def _generate_invite_code() -> str:
    """Генерация уникального кода приглашения."""
    import secrets

    return secrets.token_urlsafe(8)


class Company(Base):
    """Компания — организация, использующая бота."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    invite_code: Mapped[str] = mapped_column(
        String(20), unique=True, default=_generate_invite_code
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    # Связи
    settings: Mapped["CompanySettings | None"] = relationship(
        "CompanySettings", back_populates="company", uselist=False
    )
    integrations: Mapped["CompanyIntegrations | None"] = relationship(
        "CompanyIntegrations", back_populates="company", uselist=False
    )
    users: Mapped[list["User"]] = relationship("User", back_populates="company")
    positions: Mapped[list["Position"]] = relationship(
        "Position", back_populates="company", order_by="Position.sort_order"
    )
    join_requests: Mapped[list["JoinRequest"]] = relationship(
        "JoinRequest", back_populates="company"
    )
    telegram_groups: Mapped[list["TelegramGroup"]] = relationship(
        "TelegramGroup", back_populates="company"
    )
    notification_positions: Mapped[list["NotificationPosition"]] = relationship(
        "NotificationPosition", back_populates="company"
    )


class CompanySettings(Base):
    """Настройки компании: ссылки на Google Drive, таблицы справочников."""

    __tablename__ = "company_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)

    # Google Drive
    drive_products_folder_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    drive_products_folder_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # Ссылки на справочники (таблицы)
    suppliers_table_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    reference_tables_config: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON

    # Связи
    company: Mapped["Company"] = relationship("Company", back_populates="settings")
