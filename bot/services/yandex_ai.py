"""Сервис для работы с Yandex Vision OCR и YandexGPT."""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from bot.config import get_env


YANDEX_FOLDER_ID = get_env("YANDEX_FOLDER_ID", "")
YANDEX_API_KEY = get_env("YANDEX_API_KEY", "")

VISION_URL = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText"
GPT_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"


async def recognize_text_from_image(image_path: Path) -> Optional[str]:
    """
    Распознать текст на изображении через Yandex Vision OCR.
    
    Args:
        image_path: Путь к изображению
        
    Returns:
        Распознанный текст или None при ошибке
    """
    logger.debug(f"recognize_text_from_image: path={image_path}")
    
    if not YANDEX_API_KEY or not YANDEX_FOLDER_ID:
        logger.warning("Yandex API ключи не настроены")
        return None
    
    if not image_path.exists():
        logger.error(f"Файл не найден: {image_path}")
        return None
    
    # Читаем и кодируем изображение в base64
    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")
    
    # Определяем MIME-тип
    suffix = image_path.suffix.lower()
    mime_types = {
        ".jpg": "JPEG",
        ".jpeg": "JPEG",
        ".png": "PNG",
        ".gif": "GIF",
        ".pdf": "PDF",
    }
    mime_type = mime_types.get(suffix, "JPEG")
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "x-folder-id": YANDEX_FOLDER_ID,
    }
    
    payload = {
        "mimeType": mime_type,
        "languageCodes": ["ru", "en"],
        "model": "page",
        "content": image_data,
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(VISION_URL, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        
        # Извлекаем текст из результата
        result = data.get("result", {})
        text_annotation = result.get("textAnnotation", {})
        full_text = text_annotation.get("fullText", "")
        
        if full_text:
            logger.info(f"Текст распознан, длина: {len(full_text)} символов")
            logger.debug(f"Распознанный текст: {full_text[:200]}...")
            return full_text
        else:
            logger.warning("Текст не распознан (пустой результат)")
            return None
            
    except httpx.TimeoutException:
        logger.error("Таймаут запроса к Yandex Vision")
        return None
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP ошибка Yandex Vision: {e.response.status_code} - {e.response.text}")
        return None
    except Exception as e:
        logger.error(f"Ошибка Yandex Vision: {e}", exc_info=True)
        return None


async def extract_product_name_with_gpt(ocr_text: str) -> Optional[str]:
    """
    Извлечь название продукта из OCR-текста через YandexGPT.
    
    Args:
        ocr_text: Текст, распознанный с этикетки
        
    Returns:
        Название продукта или None при ошибке
    """
    logger.debug(f"extract_product_name_with_gpt: text_length={len(ocr_text)}")
    
    if not YANDEX_API_KEY or not YANDEX_FOLDER_ID:
        logger.warning("Yandex API ключи не настроены")
        return None
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Api-Key {YANDEX_API_KEY}",
        "x-folder-id": YANDEX_FOLDER_ID,
    }
    
    prompt = f"""Проанализируй текст с этикетки продукта и извлеки название продукта.

Текст с этикетки:
{ocr_text[:2000]}

Правила:
1. Верни ТОЛЬКО название продукта, без лишних слов
2. Название должно быть кратким и понятным (например: "Лосось филе с/с", "Молоко 3.2%", "Сыр Гауда")
3. Если не можешь определить название — верни слово "НЕОПРЕДЕЛЕНО"
4. Не добавляй пояснений, только название

Название продукта:"""

    payload = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
        "completionOptions": {
            "stream": False,
            "temperature": 0.1,
            "maxTokens": 100,
        },
        "messages": [
            {
                "role": "user",
                "text": prompt,
            }
        ],
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(GPT_URL, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        
        # Извлекаем ответ
        result = data.get("result", {})
        alternatives = result.get("alternatives", [])
        
        if alternatives:
            message = alternatives[0].get("message", {})
            product_name = message.get("text", "").strip()
            
            # Проверяем, что это не "НЕОПРЕДЕЛЕНО"
            if product_name and product_name.upper() != "НЕОПРЕДЕЛЕНО":
                logger.info(f"YandexGPT определил название: {product_name}")
                return product_name
            else:
                logger.warning("YandexGPT не смог определить название")
                return None
        else:
            logger.warning("YandexGPT вернул пустой ответ")
            return None
            
    except httpx.TimeoutException:
        logger.error("Таймаут запроса к YandexGPT")
        return None
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP ошибка YandexGPT: {e.response.status_code} - {e.response.text}")
        return None
    except Exception as e:
        logger.error(f"Ошибка YandexGPT: {e}", exc_info=True)
        return None


async def get_product_name_from_label(image_path: Path) -> tuple[Optional[str], Optional[str]]:
    """
    Получить название продукта из фото этикетки.
    
    Сначала распознаёт текст через Vision OCR,
    затем извлекает название через YandexGPT.
    
    Args:
        image_path: Путь к изображению этикетки
        
    Returns:
        (product_name, ocr_text) — название и распознанный текст
    """
    logger.info(f"get_product_name_from_label: {image_path}")
    
    # Шаг 1: OCR
    ocr_text = await recognize_text_from_image(image_path)
    
    if not ocr_text:
        logger.warning("OCR не вернул текст")
        return None, None
    
    # Шаг 2: GPT для извлечения названия
    product_name = await extract_product_name_with_gpt(ocr_text)
    
    return product_name, ocr_text


async def get_product_name_from_multiple_labels(image_paths: list[Path]) -> tuple[Optional[str], Optional[str]]:
    """
    Получить название продукта из нескольких фото этикетки.
    
    Распознаёт текст со ВСЕХ фото, объединяет результаты,
    затем извлекает название через YandexGPT.
    
    Args:
        image_paths: Список путей к изображениям этикеток
        
    Returns:
        (product_name, combined_ocr_text) — название и объединённый распознанный текст
    """
    logger.info(f"get_product_name_from_multiple_labels: {len(image_paths)} images")
    
    if not image_paths:
        return None, None
    
    # Шаг 1: OCR для всех фото
    all_texts = []
    for idx, image_path in enumerate(image_paths, 1):
        logger.debug(f"Processing image {idx}/{len(image_paths)}: {image_path}")
        ocr_text = await recognize_text_from_image(image_path)
        if ocr_text:
            all_texts.append(f"--- Фото {idx} ---\n{ocr_text}")
            logger.info(f"Image {idx}: OCR успешен, {len(ocr_text)} символов")
        else:
            logger.warning(f"Image {idx}: OCR не вернул текст")
    
    if not all_texts:
        logger.warning("OCR не вернул текст ни для одного фото")
        return None, None
    
    # Объединяем текст со всех фото
    combined_text = "\n\n".join(all_texts)
    logger.info(f"Объединённый текст: {len(combined_text)} символов из {len(all_texts)} фото")
    
    # Шаг 2: GPT для извлечения названия из объединённого текста
    product_name = await extract_product_name_with_gpt(combined_text)
    
    return product_name, combined_text
