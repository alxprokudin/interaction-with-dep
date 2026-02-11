"""Интеграция с Google Drive для загрузки файлов."""
import io
from pathlib import Path
from typing import Optional

from loguru import logger

from bot.config import BASE_DIR, GOOGLE_DRIVE_CREDENTIALS_FILE, GOOGLE_DRIVE_FOLDER_ID


def _get_drive_service():
    """Получить клиент Google Drive (lazy)."""
    if not GOOGLE_DRIVE_CREDENTIALS_FILE:
        logger.warning("GOOGLE_DRIVE_CREDENTIALS_FILE не задан, загрузка в Drive недоступна")
        return None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload

        creds_path = BASE_DIR / GOOGLE_DRIVE_CREDENTIALS_FILE
        if not creds_path.exists():
            logger.error(f"Файл учётных данных не найден: {creds_path}")
            return None
        credentials = service_account.Credentials.from_service_account_file(
            str(creds_path),
            scopes=["https://www.googleapis.com/auth/drive.file"],
        )
        service = build("drive", "v3", credentials=credentials)
        logger.debug("Google Drive сервис инициализирован")
        return service
    except Exception as e:
        logger.error(f"Ошибка инициализации Google Drive: {e}", exc_info=True)
        return None


def create_product_folder(product_name: str, supplier_name: str) -> Optional[str]:
    """
    Создать папку для продукта на Google Drive.
    Структура: {корневая папка}/{поставщик}/{номенклатура}/
    Возвращает ID папки или None при ошибке.
    """
    logger.info(f"create_product_folder called with: product_name={product_name}, supplier_name={supplier_name}")
    service = _get_drive_service()
    if not service:
        return None
    root_id = GOOGLE_DRIVE_FOLDER_ID
    if not root_id:
        logger.warning("GOOGLE_DRIVE_FOLDER_ID не задан")
        return None
    try:
        # Создаём папку поставщика, если её нет
        supplier_folder = _get_or_create_folder(service, root_id, supplier_name)
        # Создаём папку продукта
        product_folder = _get_or_create_folder(service, supplier_folder, product_name)
        logger.debug(f"Создана папка продукта: id={product_folder}")
        return product_folder
    except Exception as e:
        logger.error(f"Ошибка создания папки: {e}", exc_info=True)
        return None


def _get_or_create_folder(service, parent_id: str, name: str) -> str:
    """Получить или создать папку в родительской."""
    logger.debug(f"_get_or_create_folder: parent_id={parent_id}, name={name}")
    query = f"'{parent_id}' in parents and name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    result = service.files().list(q=query, fields="files(id, name)").execute()
    files = result.get("files", [])
    if files:
        return files[0]["id"]
    file_metadata = {"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}
    folder = service.files().create(body=file_metadata, fields="id").execute()
    return folder["id"]


def upload_file_to_drive(
    file_path: Path | io.BytesIO,
    folder_id: str,
    filename: str,
    mime_type: str = "application/octet-stream",
) -> Optional[str]:
    """
    Загрузить файл в папку Google Drive.
    Возвращает ID файла или None.
    """
    logger.info(f"upload_file_to_drive called: folder_id={folder_id}, filename={filename}")
    service = _get_drive_service()
    if not service:
        return None
    try:
        if isinstance(file_path, Path):
            media = MediaFileUpload(str(file_path), mimetype=mime_type, resumable=True)
        else:
            media = MediaIoBaseUpload(file_path, mimetype=mime_type, resumable=True)
        file_metadata = {"name": filename, "parents": [folder_id]}
        file = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
        logger.debug(f"Файл загружен: id={file['id']}")
        return file["id"]
    except Exception as e:
        logger.error(f"Ошибка загрузки файла: {e}", exc_info=True)
        return None


def create_subfolder(parent_folder_id: str, name: str) -> Optional[str]:
    """Создать подпапку (сертификаты, фото продукта, фото этикетки)."""
    logger.debug(f"create_subfolder: parent_id={parent_folder_id}, name={name}")
    service = _get_drive_service()
    if not service:
        return None
    try:
        return _get_or_create_folder(service, parent_folder_id, name)
    except Exception as e:
        logger.error(f"Ошибка создания подпапки: {e}", exc_info=True)
        return None
