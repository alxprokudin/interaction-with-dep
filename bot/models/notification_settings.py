"""Модели настроек уведомлений."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base

if TYPE_CHECKING:
    from bot.models.company import Company
    from bot.models.position import Position


class NotificationPosition(Base):
    """Должности, которые получают уведомления о регулярных заявках."""

    __tablename__ = "notification_positions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), nullable=False)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    # Уникальность: одна должность не может быть добавлена дважды для одной компании
    __table_args__ = (
        UniqueConstraint("company_id", "position_id", name="uq_notification_company_position"),
    )

    # Связи
    company: Mapped["Company"] = relationship("Company", back_populates="notification_positions")
    position: Mapped["Position"] = relationship("Position")
