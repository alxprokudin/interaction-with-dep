"""Модель должности (настраиваемая для каждой компании)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base

if TYPE_CHECKING:
    from bot.models.company import Company


class Position(Base):
    """Должность — настраивается каждой компанией отдельно."""

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # "Технолог", "Закупщик"
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    
    # Порядок сортировки (для отображения в списках)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    
    # Активна ли должность (можно "удалить" не удаляя из БД)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    # Связи
    company: Mapped["Company"] = relationship("Company", back_populates="positions")
