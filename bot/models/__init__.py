"""Модели данных."""
from bot.models.base import Base, get_async_session, init_db
from bot.models.company import Company, CompanySettings
from bot.models.integrations import CompanyIntegrations
from bot.models.join_request import JoinRequest, JoinRequestStatus
from bot.models.notification_settings import NotificationPosition
from bot.models.position import Position
from bot.models.sent_email import EmailType, SentEmail
from bot.models.telegram_group import TelegramGroup
from bot.models.user import User, UserRole
from bot.models.supplier import Supplier
from bot.models.product import Product, ProductDraft

# Импорты для регистрации в Base.metadata
__all__ = [
    "Base",
    "init_db",
    "get_async_session",
    "Company",
    "CompanyIntegrations",
    "CompanySettings",
    "EmailType",
    "JoinRequest",
    "JoinRequestStatus",
    "NotificationPosition",
    "Position",
    "SentEmail",
    "TelegramGroup",
    "User",
    "UserRole",
    "Supplier",
    "Product",
    "ProductDraft",
]
