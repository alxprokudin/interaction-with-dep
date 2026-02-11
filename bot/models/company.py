"""Модели компании и настроек."""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base


class Company(Base):
    """Компания — организация, использующая бота."""

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Связи
    settings: Mapped["CompanySettings | None"] = relationship(
        "CompanySettings", back_populates="company", uselist=False
    )
    users: Mapped[list["User"]] = relationship("User", back_populates="company")


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
