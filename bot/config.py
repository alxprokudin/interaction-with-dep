"""Конфигурация бота."""
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# Базовые пути
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


def get_env(key: str, default: Optional[str] = None) -> str:
    """Получить переменную окружения."""
    import os

    value = os.getenv(key, default)
    if not value and key == "BOT_TOKEN":
        logger.warning("Переменная окружения BOT_TOKEN не задана")
    return value or (default or "")


# Telegram
BOT_TOKEN = get_env("BOT_TOKEN")

# Database
DATABASE_URL = get_env("DATABASE_URL", f"sqlite+aiosqlite:///{DATA_DIR}/bot.db")

# Google Drive
GOOGLE_DRIVE_CREDENTIALS_FILE = get_env("GOOGLE_DRIVE_CREDENTIALS_FILE")
GOOGLE_DRIVE_FOLDER_ID = get_env("GOOGLE_DRIVE_FOLDER_ID")


def get_superadmin_ids() -> list[int]:
    """Получить список ID суперадминов."""
    ids_str = get_env("SUPERADMIN_IDS", "")
    if not ids_str:
        return []
    try:
        return [int(id_.strip()) for id_ in ids_str.split(",") if id_.strip()]
    except ValueError:
        logger.error(f"Неверный формат SUPERADMIN_IDS: {ids_str}")
        return []


SUPERADMIN_IDS = get_superadmin_ids()
