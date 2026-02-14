"""Интеграция с Google Drive для загрузки файлов."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Optional, Union

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
            scopes=["https://www.googleapis.com/auth/drive"],  # Полный доступ для Shared Drives
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
    """Получить или создать папку в родительской (поддержка Shared Drives)."""
    logger.debug(f"_get_or_create_folder: parent_id={parent_id}, name={name}")
    query = f"'{parent_id}' in parents and name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    result = service.files().list(
        q=query,
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = result.get("files", [])
    if files:
        return files[0]["id"]
    file_metadata = {"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}
    folder = service.files().create(
        body=file_metadata,
        fields="id",
        supportsAllDrives=True,
    ).execute()
    return folder["id"]


def upload_file_to_drive(
    file_path: Union[Path, io.BytesIO],
    folder_id: str,
    filename: str,
    mime_type: str = "application/octet-stream",
) -> Optional[str]:
    """
    Загрузить файл в папку Google Drive.
    Возвращает ID файла или None.
    """
    from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload
    
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
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id",
            supportsAllDrives=True,
        ).execute()
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


def create_supplier_folder(supplier_name: str, root_folder_id: str) -> Optional[str]:
    """
    Создать папку поставщика на Google Drive.
    Структура: {корневая папка}/Поставщики/{supplier_name}/
    Возвращает ID папки поставщика или None при ошибке.
    """
    logger.info(f"create_supplier_folder called with: supplier_name={supplier_name}")
    service = _get_drive_service()
    if not service:
        return None
    if not root_folder_id:
        logger.warning("root_folder_id не задан")
        return None
    try:
        # Создаём папку "Поставщики", если её нет
        suppliers_folder = _get_or_create_folder(service, root_folder_id, "Поставщики")
        # Создаём папку поставщика
        supplier_folder = _get_or_create_folder(service, suppliers_folder, supplier_name)
        logger.debug(f"Создана папка поставщика: id={supplier_folder}")
        return supplier_folder
    except Exception as e:
        logger.error(f"Ошибка создания папки поставщика: {e}", exc_info=True)
        return None


def upload_supplier_card(
    file_path: Union[Path, io.BytesIO],
    supplier_folder_id: str,
    filename: str,
    mime_type: str = "application/octet-stream",
) -> Optional[str]:
    """
    Загрузить карточку поставщика в его папку.
    Возвращает ID файла или None.
    """
    logger.info(f"upload_supplier_card: folder_id={supplier_folder_id}, filename={filename}")
    return upload_file_to_drive(file_path, supplier_folder_id, filename, mime_type)


def get_file_link(file_id: str) -> str:
    """Получить ссылку на файл в Google Drive."""
    return f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"


def get_folder_link(folder_id: str) -> str:
    """Получить ссылку на папку в Google Drive."""
    return f"https://drive.google.com/drive/folders/{folder_id}"


def list_files_in_folder(folder_id: str, include_folders: bool = False) -> list[dict]:
    """
    Получить список файлов в папке Google Drive.
    Возвращает список словарей с id, name, mimeType.
    
    Args:
        folder_id: ID папки
        include_folders: Если True, включает папки в результат
    """
    logger.debug(f"list_files_in_folder: folder_id={folder_id}, include_folders={include_folders}")
    service = _get_drive_service()
    if not service:
        return []
    
    try:
        query = f"'{folder_id}' in parents and trashed=false"
        results = service.files().list(
            q=query,
            fields="files(id, name, mimeType)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        
        files = results.get("files", [])
        
        if not include_folders:
            # Фильтруем только файлы (не папки)
            files = [f for f in files if f.get("mimeType") != "application/vnd.google-apps.folder"]
        
        logger.debug(f"Найдено элементов: {len(files)}")
        return files
    except Exception as e:
        logger.error(f"Ошибка получения списка файлов: {e}", exc_info=True)
        return []


def list_files_recursive(folder_id: str) -> list[dict]:
    """
    Рекурсивно получить все файлы из папки и вложенных папок.
    Возвращает список словарей с id, name, mimeType.
    """
    logger.info(f"list_files_recursive: folder_id={folder_id}")
    
    all_files = []
    items = list_files_in_folder(folder_id, include_folders=True)
    
    for item in items:
        mime_type = item.get("mimeType", "")
        
        if mime_type == "application/vnd.google-apps.folder":
            # Рекурсивно обходим вложенную папку
            subfolder_id = item.get("id")
            subfolder_name = item.get("name")
            logger.debug(f"Обходим вложенную папку: {subfolder_name}")
            
            sub_files = list_files_recursive(subfolder_id)
            all_files.extend(sub_files)
        else:
            # Это файл
            all_files.append(item)
    
    logger.debug(f"Всего файлов в папке и подпапках: {len(all_files)}")
    return all_files


def download_file_from_drive(file_id: str, filename: str) -> Optional[tuple[str, Path]]:
    """
    Скачать файл из Google Drive во временный файл.
    
    Returns:
        Кортеж (original_filename, temp_path) или None при ошибке.
    """
    import tempfile
    
    logger.info(f"download_file_from_drive: file_id={file_id}, filename={filename}")
    service = _get_drive_service()
    if not service:
        return None
    
    try:
        # Получаем содержимое файла
        request = service.files().get_media(
            fileId=file_id,
            supportsAllDrives=True,
        )
        
        # Создаём временный файл
        suffix = Path(filename).suffix or ""
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="drive_") as tmp:
            tmp_path = Path(tmp.name)
        
        # Скачиваем
        from googleapiclient.http import MediaIoBaseDownload
        
        with open(tmp_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
        
        logger.debug(f"Файл скачан: {tmp_path}, size={tmp_path.stat().st_size}")
        return (filename, tmp_path)
        
    except Exception as e:
        logger.error(f"Ошибка скачивания файла: {e}", exc_info=True)
        return None


def download_all_files_from_folder(folder_id: str, recursive: bool = True) -> list[tuple[str, Path]]:
    """
    Скачать все файлы из папки Google Drive.
    
    Args:
        folder_id: ID папки
        recursive: Если True, скачивает файлы из вложенных папок тоже
    
    Returns:
        Список кортежей (filename, path).
    """
    logger.info(f"download_all_files_from_folder: folder_id={folder_id}, recursive={recursive}")
    
    if recursive:
        files = list_files_recursive(folder_id)
    else:
        files = list_files_in_folder(folder_id)
    
    downloaded = []
    
    for file_info in files:
        file_id = file_info.get("id")
        filename = file_info.get("name")
        mime_type = file_info.get("mimeType", "")
        
        if not file_id or not filename:
            continue
        
        # Пропускаем Google Docs/Sheets/Slides — они требуют экспорта
        if mime_type.startswith("application/vnd.google-apps."):
            logger.warning(f"Пропускаем Google-документ (требует экспорта): {filename}")
            continue
        
        path = download_file_from_drive(file_id, filename)
        if path:
            downloaded.append((filename, path))
    
    logger.info(f"Скачано файлов: {len(downloaded)}/{len(files)}")
    return downloaded
