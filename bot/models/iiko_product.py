"""Модель для кеша продуктов iiko."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.models.base import Base


class IikoProductCache(Base):
    """Кеш продуктов из iiko для быстрого поиска."""
    
    __tablename__ = "iiko_product_cache"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    
    # ID продукта в iiko
    iiko_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    
    # Артикул
    num: Mapped[str] = mapped_column(String(50), default="")
    
    # Название продукта (индексируем для поиска)
    name: Mapped[str] = mapped_column(String(500), index=True)
    
    # Тип продукта (GOODS, DISH, etc.)
    product_type: Mapped[str] = mapped_column(String(50), default="")
    
    # Тип места приготовления
    cooking_place_type: Mapped[str] = mapped_column(String(100), default="")
    
    # Единица измерения
    main_unit: Mapped[str] = mapped_column(String(50), default="")
    
    # Категория продукта
    product_category: Mapped[str] = mapped_column(String(200), default="")
    
    # Средневзвешенная цена (заполняется отдельно)
    avg_price: Mapped[float | None] = mapped_column(default=None)
    
    # Период цены
    price_period_start: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    price_period_end: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    
    # Время последнего обновления
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, 
        server_default=func.now(),
        onupdate=func.now(),
    )
    
    def __repr__(self) -> str:
        return f"<IikoProductCache(id={self.id}, name={self.name[:30]}...)>"
