"""Модели пользователей и ролей."""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models.base import Base


class UserRole(str, __import__("enum").Enum):
    """Роли пользователей в компании."""

    ADMIN = "admin"
    MANAGER = "manager"
    EMPLOYEE = "employee"
    VIEWER = "viewer"


class User(Base):
    """Пользователь бота (привязан к компании)."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("telegram_id", "company_id", name="uq_user_telegram_company"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(nullable=False)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.EMPLOYEE)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Связи
    company: Mapped["Company"] = relationship("Company", back_populates="users")
