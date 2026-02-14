"""Сервис для работы с Google Sheets."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from bot.config import GOOGLE_DRIVE_CREDENTIALS_FILE, BASE_DIR


class GoogleSheetsService:
    """Сервис для работы с Google Sheets API."""

    def __init__(self) -> None:
        self._credentials = None
        self._gc = None  # gspread client

    async def _get_client(self):
        """Получить клиент gspread (lazy initialization)."""
        if self._gc is not None:
            return self._gc

        import asyncio

        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError:
            logger.error("Не установлены пакеты gspread и google-auth. Установите: pip install gspread google-auth")
            return None

        credentials_path = Path(GOOGLE_DRIVE_CREDENTIALS_FILE) if GOOGLE_DRIVE_CREDENTIALS_FILE else None

        if not credentials_path or not credentials_path.exists():
            # Пробуем найти в корне проекта
            credentials_path = BASE_DIR / "credentials.json"

        if not credentials_path.exists():
            logger.warning(f"Файл credentials.json не найден: {credentials_path}")
            return None

        try:
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ]
            self._credentials = Credentials.from_service_account_file(
                str(credentials_path), scopes=scopes
            )
            # gspread синхронный, используем asyncio.to_thread для операций
            self._gc = gspread.authorize(self._credentials)
            logger.info("Google Sheets клиент инициализирован")
            return self._gc
        except Exception as e:
            logger.error(f"Ошибка инициализации Google Sheets: {e}", exc_info=True)
            return None

    async def verify_sheet_access(self, sheet_id: str) -> tuple[bool, str]:
        """
        Проверить доступ к таблице.

        Returns:
            (success, message) — успех и название таблицы или сообщение об ошибке
        """
        import asyncio

        gc = await self._get_client()
        if not gc:
            return False, "Google API не настроен"

        try:
            def _open_sheet():
                sheet = gc.open_by_key(sheet_id)
                return sheet.title

            title = await asyncio.to_thread(_open_sheet)
            logger.info(f"Доступ к таблице подтверждён: {title}")
            return True, title
        except Exception as e:
            error_msg = str(e)
            if "not found" in error_msg.lower():
                return False, "Таблица не найдена"
            elif "permission" in error_msg.lower() or "403" in error_msg:
                return False, "Нет доступа. Предоставьте доступ сервисному аккаунту."
            else:
                logger.error(f"Ошибка проверки таблицы: {e}")
                return False, f"Ошибка: {error_msg[:100]}"

    async def verify_drive_folder_access(self, folder_id: str) -> tuple[bool, str]:
        """
        Проверить доступ к папке Google Drive.

        Returns:
            (success, message) — успех и название папки или сообщение об ошибке
        """
        import asyncio

        # Инициализируем клиент для получения credentials
        await self._get_client()
        if not self._credentials:
            return False, "Google API не настроен"

        try:
            from googleapiclient.discovery import build

            def _check_folder():
                drive_service = build("drive", "v3", credentials=self._credentials)
                # supportsAllDrives=True нужен для Shared Drives
                folder = drive_service.files().get(
                    fileId=folder_id,
                    fields="name",
                    supportsAllDrives=True,
                ).execute()
                return folder.get("name", "Папка")

            name = await asyncio.to_thread(_check_folder)
            logger.info(f"Доступ к папке подтверждён: {name}")
            return True, name
        except Exception as e:
            error_msg = str(e)
            if "not found" in error_msg.lower() or "404" in error_msg:
                return False, "Папка не найдена"
            elif "permission" in error_msg.lower() or "403" in error_msg:
                return False, "Нет доступа. Предоставьте доступ сервисному аккаунту."
            else:
                logger.error(f"Ошибка проверки папки: {e}")
                return False, f"Ошибка: {error_msg[:100]}"

    async def append_row(
        self,
        sheet_id: str,
        worksheet_name: Optional[str],
        row_data: list[Any],
    ) -> bool:
        """
        Добавить строку в таблицу.

        Args:
            sheet_id: ID таблицы
            worksheet_name: Название листа (None = первый лист)
            row_data: Данные строки

        Returns:
            Успешность операции
        """
        import asyncio

        gc = await self._get_client()
        if not gc:
            logger.error("Google API не настроен")
            return False

        try:
            def _append():
                spreadsheet = gc.open_by_key(sheet_id)
                if worksheet_name:
                    worksheet = spreadsheet.worksheet(worksheet_name)
                else:
                    worksheet = spreadsheet.sheet1
                worksheet.append_row(row_data, value_input_option="USER_ENTERED")

            await asyncio.to_thread(_append)
            logger.info(f"Строка добавлена в таблицу {sheet_id}")
            return True
        except Exception as e:
            logger.error(f"Ошибка добавления строки: {e}", exc_info=True)
            return False

    async def get_service_account_email(self) -> Optional[str]:
        """Получить email сервисного аккаунта для отображения пользователю."""
        credentials_path = Path(GOOGLE_DRIVE_CREDENTIALS_FILE) if GOOGLE_DRIVE_CREDENTIALS_FILE else None

        if not credentials_path or not credentials_path.exists():
            credentials_path = BASE_DIR / "credentials.json"

        if not credentials_path.exists():
            return None

        try:
            with open(credentials_path) as f:
                data = json.load(f)
            return data.get("client_email")
        except Exception as e:
            logger.error(f"Ошибка чтения credentials: {e}")
            return None

    async def get_all_rows(
        self,
        sheet_id: str,
        worksheet_name: str,
        skip_header: bool = True,
    ) -> list[list[Any]]:
        """
        Получить все строки с листа.
        
        Args:
            sheet_id: ID таблицы
            worksheet_name: Название листа
            skip_header: Пропустить первую строку (заголовок)
            
        Returns:
            Список строк (каждая строка — список значений)
        """
        import asyncio

        gc = await self._get_client()
        if not gc:
            logger.error("Google API не настроен")
            return []

        try:
            def _get_rows():
                spreadsheet = gc.open_by_key(sheet_id)
                worksheet = spreadsheet.worksheet(worksheet_name)
                rows = worksheet.get_all_values()
                if skip_header and rows:
                    return rows[1:]
                return rows

            rows = await asyncio.to_thread(_get_rows)
            logger.debug(f"Получено строк из {worksheet_name}: {len(rows)}")
            return rows
        except Exception as e:
            logger.error(f"Ошибка чтения листа {worksheet_name}: {e}", exc_info=True)
            return []

    async def search_suppliers(
        self,
        sheet_id: str,
        query: str,
        worksheet_name: str = "Реестр_Поставщики",
        name_column: int = 3,  # Колонка D (0-indexed = 3) — "Наименование"
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Поиск поставщиков по части названия.
        
        Args:
            sheet_id: ID таблицы компании
            query: Строка поиска (часть названия)
            worksheet_name: Название листа с поставщиками
            name_column: Индекс колонки с названием (0-based)
            limit: Максимум результатов
            
        Returns:
            Список словарей с данными поставщиков
        """
        logger.debug(f"search_suppliers: query='{query}', sheet_id={sheet_id[:20]}...")
        
        rows = await self.get_all_rows(sheet_id, worksheet_name, skip_header=True)
        if not rows:
            return []
        
        query_lower = query.lower().strip()
        results = []
        
        for row_idx, row in enumerate(rows):
            if len(row) <= name_column:
                continue
            
            name = row[name_column] if row[name_column] else ""
            if query_lower in name.lower():
                # Формируем словарь поставщика
                # Структура таблицы (из скриншота):
                # A: Дата добавления, B: ИНН, C: КПП, D: Наименование, E: Email,
                # F: Телефон, G: ФИО, H: Предмет, I: Точки, J: Ответственный, ...
                supplier = {
                    "row_number": row_idx + 2,  # +1 для 1-based, +1 для заголовка
                    "date_added": row[0] if len(row) > 0 else "",
                    "inn": row[1] if len(row) > 1 else "",
                    "kpp": row[2] if len(row) > 2 else "",
                    "name": row[3] if len(row) > 3 else "",
                    "email": row[4] if len(row) > 4 else "",
                    "phone": row[5] if len(row) > 5 else "",
                    "contact_name": row[6] if len(row) > 6 else "",
                    "subject": row[7] if len(row) > 7 else "",
                    "locations": row[8] if len(row) > 8 else "",
                    "responsible": row[9] if len(row) > 9 else "",
                }
                results.append(supplier)
                
                if len(results) >= limit:
                    break
        
        logger.info(f"Найдено поставщиков: {len(results)} (query='{query}')")
        return results

    async def add_supplier(
        self,
        sheet_id: str,
        supplier_data: dict[str, Any],
        worksheet_name: str = "Реестр_Поставщики",
    ) -> bool:
        """
        Добавить нового поставщика в таблицу.
        
        Args:
            sheet_id: ID таблицы
            supplier_data: Данные поставщика
            worksheet_name: Название листа
            
        Колонки таблицы:
            A: Дата добавления
            B: ИНН
            C: КПП
            D: Название
            E: Email
            F: Телефон
            G: Контактное лицо
            H: Предмет
            I: Точки поставки
            J: Ответственный
            K: Ссылка на папку
            L: Ссылка на карточку
            M: Ответ СБ
            N: Ответ DocsInBox
            O: Ответ Роуминг
            P: Ответ Документы
            Q: Telegram ID
            
        Returns:
            Успешность операции
        """
        from datetime import datetime
        
        logger.info(f"add_supplier: name={supplier_data.get('name')}")
        
        # Формируем строку в порядке колонок таблицы
        row = [
            datetime.now().strftime("%d.%m.%Y %H:%M:%S"),  # A: Дата добавления
            supplier_data.get("inn", ""),                  # B: ИНН
            supplier_data.get("kpp", ""),                  # C: КПП
            supplier_data.get("name", ""),                 # D: Название
            supplier_data.get("email", ""),                # E: Email
            supplier_data.get("phone", ""),                # F: Телефон
            supplier_data.get("contact_name", ""),         # G: Контактное лицо
            supplier_data.get("subject", ""),              # H: Предмет
            supplier_data.get("locations", ""),            # I: Точки поставки
            supplier_data.get("responsible", ""),          # J: Ответственный
            supplier_data.get("folder_link", ""),          # K: Ссылка на папку
            supplier_data.get("card_link", ""),            # L: Ссылка на карточку
            "",                                            # M: Ответ СБ (пусто)
            "",                                            # N: Ответ DocsInBox (пусто)
            "",                                            # O: Ответ Роуминг (пусто)
            "",                                            # P: Ответ Документы (пусто)
            str(supplier_data.get("telegram_user_id", "")),# Q: Telegram ID
        ]
        
        return await self.append_row(sheet_id, worksheet_name, row)
    
    async def update_supplier_reply_status(
        self,
        sheet_id: str,
        supplier_inn: str,
        email_type: str,
        worksheet_name: str = "Реестр_Поставщики",
    ) -> bool:
        """
        Обновить статус ответа на письмо для поставщика.
        
        Args:
            sheet_id: ID таблицы
            supplier_inn: ИНН поставщика для поиска строки
            email_type: Тип письма ("sb_check", "docsinbox", "roaming", "documents")
            worksheet_name: Название листа
            
        Returns:
            Успешность операции
        """
        import asyncio
        from datetime import datetime
        
        logger.info(f"update_supplier_reply_status: inn={supplier_inn}, type={email_type}")
        
        # Колонки для разных типов писем
        column_map = {
            "sb_check": "M",     # Ответ СБ
            "docsinbox": "N",   # Ответ DocsInBox
            "roaming": "O",     # Ответ Роуминг
            "documents": "P",   # Ответ Документы
        }
        
        column = column_map.get(email_type)
        if not column:
            logger.error(f"Неизвестный тип письма: {email_type}")
            return False
        
        gc = await self._get_client()
        if not gc:
            logger.error("Google API не настроен")
            return False
        
        try:
            def _update():
                spreadsheet = gc.open_by_key(sheet_id)
                worksheet = spreadsheet.worksheet(worksheet_name)
                
                # Ищем строку по ИНН (колонка B)
                cell = worksheet.find(supplier_inn, in_column=2)
                if not cell:
                    logger.warning(f"Поставщик с ИНН {supplier_inn} не найден")
                    return False
                
                # Обновляем ячейку с датой ответа
                cell_address = f"{column}{cell.row}"
                worksheet.update_acell(
                    cell_address, 
                    datetime.now().strftime("%d.%m.%Y %H:%M")
                )
                logger.info(f"Обновлена ячейка {cell_address} для ИНН {supplier_inn}")
                return True
            
            return await asyncio.to_thread(_update)
        except Exception as e:
            logger.error(f"Ошибка обновления статуса: {e}", exc_info=True)
            return False


# Singleton instance
google_sheets_service = GoogleSheetsService()
