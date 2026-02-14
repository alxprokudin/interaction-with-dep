"""Модель заявки на вступление в компанию."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base

if TYPE_CHECKING:
    from bot.models.company import Company
    from bot.models.user import User


class JoinRequestStatus(str, PyEnum):
    """Статусы заявки на вступление."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class JoinRequest(Base):
    """Заявка на вступление в компанию."""

    __tablename__ = "join_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(nullable=False)  # кто подал заявку
    telegram_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    telegram_full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    status: Mapped[JoinRequestStatus] = mapped_column(
        Enum(JoinRequestStatus), default=JoinRequestStatus.PENDING
    )
    reviewed_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    reject_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Связи
    company: Mapped["Company"] = relationship("Company", back_populates="join_requests")
    reviewed_by: Mapped[Optional["User"]] = relationship("User")
