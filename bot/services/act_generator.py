"""Генератор акта проработки через копирование Google Sheets шаблона."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from loguru import logger

from bot.services.google_drive import copy_file_to_folder, get_spreadsheet_link


# ID шаблона акта в Google Sheets
ACT_TEMPLATE_ID = "11bpOd7_vNNVkS7U63CDdz7VqewmAdxBVkimWJTpW7Rk"


@dataclass
class ActData:
    """Данные для заполнения акта проработки."""
    
    request_id: str  # ID заявки (REQ-XXXXX)
    date: str  # Дата проработки (ДД.ММ.ГГГГ)
    product_name: str  # Наименование товара поставщика
    supplier_name: str  # Поставщик (фирма, бренд, страна)
    iiko_product_name: str  # Наименование продукта из iiko
    
    # Новые поля
    user_name: str = ""  # Кто взял в работу
    certificate_link: str = ""  # Ссылка на сертификат
    ocr_link: str = ""  # Ссылка на этикетку (OCR)
    price_from_partner: float = 0.0  # Цена поставщика
    price_from_iiko: float = 0.0  # Цена из iiko
    period_from_iiko: str = ""  # Период расчёта цены
    
    # Опциональные поля
    production_date: Optional[str] = None  # Дата изготовления
    expiry_date: Optional[str] = None  # Срок годности


def _get_sheets_service():
    """Получить клиент Google Sheets."""
    from bot.config import BASE_DIR, GOOGLE_DRIVE_CREDENTIALS_FILE
    
    if not GOOGLE_DRIVE_CREDENTIALS_FILE:
        logger.warning("GOOGLE_DRIVE_CREDENTIALS_FILE не задан")
        return None
    
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        
        creds_path = BASE_DIR / GOOGLE_DRIVE_CREDENTIALS_FILE
        if not creds_path.exists():
            logger.error(f"Файл учётных данных не найден: {creds_path}")
            return None
        
        credentials = service_account.Credentials.from_service_account_file(
            str(creds_path),
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ],
        )
        service = build("sheets", "v4", credentials=credentials)
        logger.debug("Google Sheets сервис инициализирован")
        return service
    except Exception as e:
        logger.error(f"Ошибка инициализации Google Sheets: {e}", exc_info=True)
        return None


def fill_act_template(spreadsheet_id: str, data: ActData) -> bool:
    """
    Заполнить скопированный шаблон акта данными.
    
    Плейсхолдеры в шаблоне:
    - {{id_item}} - ID заявки
    - {{date}} - Дата
    - {{name_of_goods}} - Наименование товара
    - {{partner}} - Поставщик
    - Наименование из iiko - отдельная ячейка
    
    Returns:
        True если успешно, False при ошибке
    """
    logger.info(f"fill_act_template: spreadsheet_id={spreadsheet_id}, request_id={data.request_id}")
    
    service = _get_sheets_service()
    if not service:
        return False
    
    try:
        # Читаем все значения для поиска плейсхолдеров
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range="A1:Z55",
        ).execute()
        
        values = result.get("values", [])
        logger.debug(f"Прочитано строк: {len(values)}")
        
        # Ищем и заменяем плейсхолдеры
        replacements = {
            "{{id_item}}": data.request_id,
            "{{user_name}}": data.user_name,
            "{{date}}": data.date,
            "{{name_of_goods}}": data.product_name,
            "{{partner}}": data.supplier_name,
            "{{name_of_goods_from_iiko}}": data.iiko_product_name,
            "{{certificate}}": data.certificate_link,
            "{{OCR}}": data.ocr_link,
            "{{price_from_partner}}": str(data.price_from_partner) if data.price_from_partner else "",
            "{{price_from_iiko}}": str(data.price_from_iiko) if data.price_from_iiko else "",
            "{{period_from_iiko}}": data.period_from_iiko,
        }
        
        updates = []
        
        for row_idx, row in enumerate(values):
            for col_idx, cell_value in enumerate(row):
                if not isinstance(cell_value, str):
                    continue
                
                new_value = cell_value
                changed = False
                
                for placeholder, replacement in replacements.items():
                    if placeholder in new_value:
                        new_value = new_value.replace(placeholder, replacement)
                        changed = True
                        logger.debug(f"Заменён {placeholder} в ячейке ({row_idx+1}, {col_idx+1})")
                
                if changed:
                    # Конвертируем индексы в A1-нотацию
                    col_letter = chr(ord('A') + col_idx) if col_idx < 26 else f"A{chr(ord('A') + col_idx - 26)}"
                    cell_ref = f"{col_letter}{row_idx + 1}"
                    updates.append({
                        "range": cell_ref,
                        "values": [[new_value]],
                    })
        
        # Ищем ячейку для наименования из iiko (после строки "Наименование полуфабриката")
        iiko_cell = None
        for row_idx, row in enumerate(values):
            for col_idx, cell_value in enumerate(row):
                if isinstance(cell_value, str) and "полуфабриката" in cell_value.lower():
                    # Следующая строка, та же колонка или колонка C
                    iiko_cell = f"C{row_idx + 2}"
                    break
            if iiko_cell:
                break
        
        if iiko_cell:
            updates.append({
                "range": iiko_cell,
                "values": [[data.iiko_product_name]],
            })
            logger.debug(f"Наименование iiko будет записано в {iiko_cell}")
        
        # Выполняем обновления пакетом
        if updates:
            body = {
                "valueInputOption": "RAW",
                "data": updates,
            }
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body=body,
            ).execute()
            logger.info(f"Обновлено ячеек: {len(updates)}")
        
        return True
        
    except Exception as e:
        logger.error(f"Ошибка заполнения шаблона: {e}", exc_info=True)
        return False


def generate_act(data: ActData, folder_id: str) -> Optional[str]:
    """
    Сгенерировать акт проработки из Google Sheets шаблона.
    
    1. Копирует шаблон в указанную папку
    2. Заполняет копию данными
    
    Args:
        data: Данные для заполнения акта
        folder_id: ID папки Google Drive для сохранения
        
    Returns:
        ID созданного документа или None при ошибке
    """
    logger.info(f"generate_act: request_id={data.request_id}, folder_id={folder_id}")
    
    # Имя файла акта
    safe_name = data.product_name.replace("/", "_").replace("\\", "_")[:40]
    act_name = f"АКТ_ПРОРАБОТКИ_{data.request_id}_{safe_name}"
    
    # 1. Копируем шаблон
    new_file_id = copy_file_to_folder(ACT_TEMPLATE_ID, folder_id, act_name)
    if not new_file_id:
        logger.error("Не удалось скопировать шаблон акта")
        return None
    
    logger.debug(f"Шаблон скопирован, новый ID: {new_file_id}")
    
    # 2. Заполняем данными
    if not fill_act_template(new_file_id, data):
        logger.warning("Не удалось заполнить шаблон данными, но файл создан")
    
    logger.info(f"Акт создан: {get_spreadsheet_link(new_file_id)}")
    return new_file_id


def generate_act_for_request(
    request_id: str,
    product_name: str,
    supplier_name: str,
    iiko_product_name: str,
    folder_id: str,
    user_name: str = "",
    certificate_link: str = "",
    ocr_link: str = "",
    price_from_partner: float = 0.0,
    price_from_iiko: float = 0.0,
    period_from_iiko: str = "",
) -> Optional[str]:
    """
    Функция генерации акта проработки.
    
    Args:
        request_id: ID заявки
        product_name: Название товара поставщика
        supplier_name: Название поставщика
        iiko_product_name: Название продукта из iiko
        folder_id: ID папки для сохранения
        user_name: Кто взял в работу
        certificate_link: Ссылка на сертификат
        ocr_link: Ссылка на этикетку
        price_from_partner: Цена поставщика
        price_from_iiko: Цена из iiko
        period_from_iiko: Период расчёта цены
        
    Returns:
        ID созданного документа или None
    """
    data = ActData(
        request_id=request_id,
        date=datetime.now().strftime("%d.%m.%Y"),
        product_name=product_name,
        supplier_name=supplier_name,
        iiko_product_name=iiko_product_name,
        user_name=user_name,
        certificate_link=certificate_link,
        ocr_link=ocr_link,
        price_from_partner=price_from_partner,
        price_from_iiko=price_from_iiko,
        period_from_iiko=period_from_iiko,
    )
    
    return generate_act(data, folder_id)


def get_act_cell_value(spreadsheet_id: str, cell: str = "C24") -> str:
    """
    Получить значение из ячейки акта.
    
    Args:
        spreadsheet_id: ID Google Sheets документа
        cell: Адрес ячейки (например, "C24")
        
    Returns:
        Значение ячейки как строка
    """
    logger.debug(f"get_act_cell_value: spreadsheet_id={spreadsheet_id}, cell={cell}")
    
    service = _get_sheets_service()
    if not service:
        return ""
    
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=cell,
        ).execute()
        
        values = result.get("values", [])
        if values and values[0]:
            value = str(values[0][0])
            logger.debug(f"Значение ячейки {cell}: {value}")
            return value
        return ""
    except Exception as e:
        logger.error(f"Ошибка чтения ячейки {cell}: {e}", exc_info=True)
        return ""


def add_photos_to_act(spreadsheet_id: str, photo_links: list[tuple[str, str]]) -> bool:
    """
    Добавить фотографии в лист "Фото" акта.
    
    Формат: колонка A — ссылка, колонка B — миниатюра через =IMAGE(url)
    
    Args:
        spreadsheet_id: ID Google Sheets документа
        photo_links: Список кортежей (filename, drive_link)
        
    Returns:
        True если успешно
    """
    logger.debug(f"add_photos_to_act: spreadsheet_id={spreadsheet_id}, photos={len(photo_links)}")
    
    if not photo_links:
        return True
    
    service = _get_sheets_service()
    if not service:
        return False
    
    try:
        # Проверяем/создаём лист "Фото"
        spreadsheet = service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
        ).execute()
        
        sheets = spreadsheet.get("sheets", [])
        photo_sheet_id = None
        
        for sheet in sheets:
            if sheet.get("properties", {}).get("title") == "Фото":
                photo_sheet_id = sheet["properties"]["sheetId"]
                break
        
        # Если листа нет — создаём
        if photo_sheet_id is None:
            request_body = {
                "requests": [
                    {
                        "addSheet": {
                            "properties": {
                                "title": "Фото",
                            }
                        }
                    }
                ]
            }
            response = service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body=request_body,
            ).execute()
            photo_sheet_id = response["replies"][0]["addSheet"]["properties"]["sheetId"]
            logger.debug(f"Создан лист 'Фото' с ID={photo_sheet_id}")
        
        # Формируем данные для записи
        # Заголовки + данные
        values = [["Файл", "Ссылка", "Превью"]]
        for filename, link in photo_links:
            # Преобразуем ссылку для IMAGE()
            # Формат: https://drive.google.com/file/d/FILE_ID/view -> 
            #         https://drive.google.com/uc?id=FILE_ID
            file_id = _extract_file_id_from_link(link)
            if file_id:
                image_url = f"https://drive.google.com/uc?id={file_id}"
                image_formula = f'=IMAGE("{image_url}")'
            else:
                image_formula = ""
            
            values.append([filename, link, image_formula])
        
        # Записываем данные
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range="Фото!A1",
            valueInputOption="USER_ENTERED",
            body={"values": values},
        ).execute()
        
        logger.info(f"Добавлено {len(photo_links)} фото в лист 'Фото'")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка добавления фото в акт: {e}", exc_info=True)
        return False


def _extract_file_id_from_link(link: str) -> str | None:
    """Извлечь ID файла из ссылки Google Drive."""
    import re
    
    patterns = [
        r"/d/([a-zA-Z0-9_-]+)",  # /d/FILE_ID/
        r"id=([a-zA-Z0-9_-]+)",  # id=FILE_ID
    ]
    
    for pattern in patterns:
        match = re.search(pattern, link)
        if match:
            return match.group(1)
    
    return None


def export_act_to_pdf(spreadsheet_id: str) -> bytes | None:
    """
    Экспортировать Google Sheets акт в PDF.
    
    Args:
        spreadsheet_id: ID Google Sheets документа
        
    Returns:
        PDF как bytes или None
    """
    from bot.config import BASE_DIR, GOOGLE_DRIVE_CREDENTIALS_FILE
    
    logger.debug(f"export_act_to_pdf: spreadsheet_id={spreadsheet_id}")
    
    if not GOOGLE_DRIVE_CREDENTIALS_FILE:
        logger.warning("GOOGLE_DRIVE_CREDENTIALS_FILE не задан")
        return None
    
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseDownload
        import io
        
        creds_path = BASE_DIR / GOOGLE_DRIVE_CREDENTIALS_FILE
        credentials = service_account.Credentials.from_service_account_file(
            str(creds_path),
            scopes=["https://www.googleapis.com/auth/drive"],
        )
        drive_service = build("drive", "v3", credentials=credentials)
        
        # Экспорт в PDF
        request = drive_service.files().export_media(
            fileId=spreadsheet_id,
            mimeType="application/pdf",
        )
        
        file_buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(file_buffer, request)
        
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                logger.debug(f"PDF export: {int(status.progress() * 100)}%")
        
        pdf_bytes = file_buffer.getvalue()
        logger.info(f"Акт экспортирован в PDF: {len(pdf_bytes)} bytes")
        return pdf_bytes
        
    except Exception as e:
        logger.error(f"Ошибка экспорта в PDF: {e}", exc_info=True)
        return None
