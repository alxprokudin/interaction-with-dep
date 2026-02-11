"""Модели данных."""
from bot.models.base import Base, get_async_session, init_db
from bot.models.company import Company, CompanySettings
from bot.models.user import User, UserRole
from bot.models.supplier import Supplier
from bot.models.product import Product, ProductDraft

# Импорты для регистрации в Base.metadata
__all__ = [
    "Base",
    "init_db",
    "get_async_session",
    "Company",
    "CompanySettings",
    "User",
    "UserRole",
    "Supplier",
    "Product",
    "ProductDraft",
]
