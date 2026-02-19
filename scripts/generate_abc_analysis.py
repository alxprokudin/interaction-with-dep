"""
Скрипт для генерации ABC-анализа продуктов из iiko.

Логика ABC:
- Категория A = (топ-20% по сумме) ИЛИ (топ-20% по объёму)
- Категория B = следующие 30%
- Категория C = остальные 50%

Запуск:
    cd interaction-with-dep
    source venv/bin/activate
    python scripts/generate_abc_analysis.py
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
import pandas as pd
from loguru import logger

# Конфигурация
IIKO_BASE_URL = os.getenv("IIKO_BASE_URL", "https://mnogo-lososya-centralniy-of-co.iiko.it:443/resto/api")
IIKO_LOGIN = os.getenv("IIKO_LOGIN", "api_reader")
IIKO_PASSWORD = os.getenv("IIKO_PASSWORD", "")

# Пороги ABC
TOP_A_PERCENT = 20  # Топ 20% = категория A
TOP_B_PERCENT = 50  # Следующие 30% (20-50%) = категория B
# Остальные 50% = категория C


async def login_iiko(client: httpx.AsyncClient) -> str:
    """Авторизация в iiko API."""
    url = f"{IIKO_BASE_URL}/auth"
    params = {"login": IIKO_LOGIN, "pass": IIKO_PASSWORD}
    
    response = await client.get(url, params=params)
    response.raise_for_status()
    
    token = response.text.strip()
    logger.info(f"iiko login successful, token length: {len(token)}")
    return token


async def logout_iiko(client: httpx.AsyncClient, token: str):
    """Выход из iiko API."""
    url = f"{IIKO_BASE_URL}/logout"
    params = {"key": token}
    
    try:
        await client.get(url, params=params)
        logger.info("iiko logout successful")
    except Exception as e:
        logger.warning(f"iiko logout failed: {e}")


async def get_department_codes(client: httpx.AsyncClient, token: str) -> list[str]:
    """Получить коды активных департаментов МЛ МСК."""
    import xml.etree.ElementTree as ET
    
    url = f"{IIKO_BASE_URL}/corporation/departments"
    params = {"key": token}
    
    response = await client.get(url, params=params)
    response.raise_for_status()
    
    codes = []
    root = ET.fromstring(response.text)
    
    for dept_dto in root.findall("corporateItemDto"):
        name_elem = dept_dto.find("name")
        code_elem = dept_dto.find("code")
        
        if name_elem is not None and code_elem is not None:
            name = name_elem.text or ""
            code = code_elem.text or ""
            
            # Фильтруем: МЛ МСК, не закрытые
            if "МЛ МСК" in name and "(закрыто)" not in name.lower() and "(закрыта)" not in name.lower():
                if code:
                    codes.append(code)
    
    logger.info(f"Found {len(codes)} active ML MSK departments")
    return codes


async def fetch_all_products_olap(
    client: httpx.AsyncClient,
    token: str,
    department_codes: list[str],
    date_from: datetime,
    date_to: datetime,
) -> list[dict]:
    """Получить OLAP данные по ВСЕМ продуктам (без фильтра по названиям)."""
    
    url = f"{IIKO_BASE_URL}/v2/reports/olap"
    
    filters = {
        "DateTime.DateTyped": {
            "filterType": "DateRange",
            "periodType": "CUSTOM",
            "from": date_from.strftime("%Y-%m-%d"),
            "to": date_to.strftime("%Y-%m-%d"),
            "includeLow": True,
            "includeHigh": True,
        },
        "Account.Name": {
            "filterType": "IncludeValues",
            "values": ["Задолженность перед поставщиками"],
        },
        "Account.CounteragentType": {
            "filterType": "IncludeValues",
            "values": ["SUPPLIER", "INTERNAL_SUPPLIER"],
        },
        "TransactionType": {
            "filterType": "IncludeValues",
            "values": ["INVOICE"],
        },
        "Contr-Product.Type": {
            "filterType": "IncludeValues",
            "values": ["GOODS"],
        },
        "Department.Code": {
            "filterType": "IncludeValues",
            "values": department_codes,
        },
    }
    
    params = {
        "reportType": "TRANSACTIONS",
        "buildSummary": False,
        "groupByColFields": [
            "Contr-Product.Name",
            "Contr-Product.Num",
            "Contr-Product.MeasureUnit",
            "Contr-Product.TopParent",
            "Contr-Product.SecondParent",
        ],
        "aggregateFields": ["Contr-Amount", "Sum.ResignedSum"],
        "filters": filters,
    }
    
    logger.info(f"Fetching OLAP data from {date_from.date()} to {date_to.date()}...")
    
    response = await client.post(
        url=url,
        params={"key": token},
        json=params,
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=120.0,
    )
    response.raise_for_status()
    
    data = response.json()
    result = data if isinstance(data, list) else data.get("data", [])
    
    logger.info(f"Received {len(result)} rows from OLAP")
    return result


def calculate_abc_categories(df: pd.DataFrame) -> pd.DataFrame:
    """
    Рассчитать ABC категории.
    
    Логика:
    - A = топ-20% по сумме ИЛИ топ-20% по объёму
    - B = следующие 30% (кто не попал в A)
    - C = остальные
    """
    # Сортируем по сумме и присваиваем ранг
    df = df.sort_values("total_sum", ascending=False).reset_index(drop=True)
    df["rank_by_sum"] = range(1, len(df) + 1)
    df["percentile_sum"] = df["rank_by_sum"] / len(df) * 100
    
    # Сортируем по объёму и присваиваем ранг
    df = df.sort_values("total_amount", ascending=False).reset_index(drop=True)
    df["rank_by_amount"] = range(1, len(df) + 1)
    df["percentile_amount"] = df["rank_by_amount"] / len(df) * 100
    
    # Определяем категорию
    def get_category(row):
        # A = топ-20% по сумме ИЛИ топ-20% по объёму
        if row["percentile_sum"] <= TOP_A_PERCENT or row["percentile_amount"] <= TOP_A_PERCENT:
            return "A"
        # B = следующие 30% (20-50%)
        elif row["percentile_sum"] <= TOP_B_PERCENT or row["percentile_amount"] <= TOP_B_PERCENT:
            return "B"
        # C = остальные
        else:
            return "C"
    
    df["category"] = df.apply(get_category, axis=1)
    
    # Причина попадания в категорию A
    def get_reason(row):
        if row["category"] != "A":
            return ""
        reasons = []
        if row["percentile_sum"] <= TOP_A_PERCENT:
            reasons.append(f"Топ-{TOP_A_PERCENT}% по сумме")
        if row["percentile_amount"] <= TOP_A_PERCENT:
            reasons.append(f"Топ-{TOP_A_PERCENT}% по объёму")
        return "; ".join(reasons)
    
    df["reason_a"] = df.apply(get_reason, axis=1)
    
    # Сортируем финально: A, B, C, внутри по сумме
    df["category_order"] = df["category"].map({"A": 1, "B": 2, "C": 3})
    df = df.sort_values(["category_order", "total_sum"], ascending=[True, False])
    df = df.drop(columns=["category_order"])
    
    return df


async def main():
    """Основная функция."""
    global IIKO_BASE_URL, IIKO_LOGIN, IIKO_PASSWORD
    
    # Пробуем загрузить из .env
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
    
    IIKO_BASE_URL = os.getenv("IIKO_BASE_URL", IIKO_BASE_URL)
    IIKO_LOGIN = os.getenv("IIKO_LOGIN", IIKO_LOGIN)
    IIKO_PASSWORD = os.getenv("IIKO_PASSWORD", "")
    
    if not IIKO_PASSWORD:
        logger.error("IIKO_PASSWORD не задан!")
        return
    
    # Период: последняя неделя
    date_to = datetime.now()
    date_from = date_to - timedelta(days=7)
    
    async with httpx.AsyncClient(timeout=120.0, verify=False) as client:
        # Авторизация
        token = await login_iiko(client)
        
        try:
            # Получаем коды департаментов
            dept_codes = await get_department_codes(client, token)
            
            if not dept_codes:
                logger.error("Не найдены департаменты МЛ МСК!")
                return
            
            # Получаем OLAP данные
            olap_data = await fetch_all_products_olap(
                client, token, dept_codes, date_from, date_to
            )
            
            if not olap_data:
                logger.error("Нет данных из OLAP!")
                return
            
        finally:
            await logout_iiko(client, token)
    
    # Преобразуем в DataFrame
    logger.info("Processing data...")
    
    records = []
    for row in olap_data:
        name = row.get("Contr-Product.Name", "")
        num = row.get("Contr-Product.Num", "")
        unit = row.get("Contr-Product.MeasureUnit", "")
        category1 = row.get("Contr-Product.TopParent", "")
        category2 = row.get("Contr-Product.SecondParent", "")
        amount = row.get("Contr-Amount", 0) or 0
        total = row.get("Sum.ResignedSum", 0) or 0
        
        if name:
            records.append({
                "product_name": name,
                "product_num": num,
                "unit": unit,
                "category1": category1,
                "category2": category2,
                "total_amount": float(amount),
                "total_sum": float(total),
            })
    
    df = pd.DataFrame(records)
    
    # Агрегируем по продуктам (могут быть дубли из разных департаментов)
    df_agg = df.groupby(["product_name", "product_num", "unit", "category1", "category2"]).agg({
        "total_amount": "sum",
        "total_sum": "sum",
    }).reset_index()
    
    logger.info(f"Unique products: {len(df_agg)}")
    
    # Рассчитываем ABC
    df_abc = calculate_abc_categories(df_agg)
    
    # Статистика
    stats = df_abc["category"].value_counts()
    logger.info(f"ABC distribution: A={stats.get('A', 0)}, B={stats.get('B', 0)}, C={stats.get('C', 0)}")
    
    # Экспортируем в Excel
    output_path = Path(__file__).parent.parent / "files" / "ABC_analysis.xlsx"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Переименовываем колонки для читаемости
    df_export = df_abc.rename(columns={
        "product_name": "Наименование",
        "product_num": "Артикул",
        "unit": "Ед. изм.",
        "category1": "Категория 1",
        "category2": "Категория 2",
        "total_amount": "Объём (ед.)",
        "total_sum": "Сумма (руб.)",
        "rank_by_sum": "Ранг по сумме",
        "percentile_sum": "Процентиль по сумме",
        "rank_by_amount": "Ранг по объёму",
        "percentile_amount": "Процентиль по объёму",
        "category": "Категория ABC",
        "reason_a": "Причина категории A",
    })
    
    # Сохраняем
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        # Основной лист
        df_export.to_excel(writer, sheet_name="ABC Analysis", index=False)
        
        # Лист со статистикой
        stats_df = pd.DataFrame({
            "Категория": ["A", "B", "C", "Всего"],
            "Количество продуктов": [
                stats.get("A", 0),
                stats.get("B", 0),
                stats.get("C", 0),
                len(df_abc),
            ],
            "Процент": [
                f"{stats.get('A', 0) / len(df_abc) * 100:.1f}%",
                f"{stats.get('B', 0) / len(df_abc) * 100:.1f}%",
                f"{stats.get('C', 0) / len(df_abc) * 100:.1f}%",
                "100%",
            ],
            "Сумма закупок": [
                df_abc[df_abc["category"] == "A"]["total_sum"].sum(),
                df_abc[df_abc["category"] == "B"]["total_sum"].sum(),
                df_abc[df_abc["category"] == "C"]["total_sum"].sum(),
                df_abc["total_sum"].sum(),
            ],
        })
        stats_df.to_excel(writer, sheet_name="Статистика", index=False)
        
        # Только категория A
        df_a = df_export[df_export["Категория ABC"] == "A"]
        df_a.to_excel(writer, sheet_name="Категория A", index=False)
    
    logger.info(f"Excel saved to: {output_path}")
    print(f"\n✅ Файл сохранён: {output_path}")
    print(f"\nСтатистика:")
    print(f"  Категория A: {stats.get('A', 0)} продуктов")
    print(f"  Категория B: {stats.get('B', 0)} продуктов")
    print(f"  Категория C: {stats.get('C', 0)} продуктов")
    print(f"  Всего: {len(df_abc)} продуктов")


if __name__ == "__main__":
    asyncio.run(main())
