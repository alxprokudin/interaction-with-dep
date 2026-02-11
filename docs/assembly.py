#!/usr/bin/env python3
"""
Скрипт для сборки одного MD-файла из нескольких частей.

Использование:
    python assembly.py                    # собирает docs/guide-assembled.md
    python assembly.py outputs/custom.md  # собирает в указанный файл
"""

import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def assemble_markdown(parts_dir: Path, output_path: Path) -> None:
    """
    Собирает все .md файлы из parts_dir в один документ.
    Файлы объединяются в алфавитном порядке (по префиксу 00-, 01-, 02-...).
    """
    logger.debug("assemble_markdown called with: parts_dir=%s, output_path=%s", parts_dir, output_path)

    if not parts_dir.exists():
        raise FileNotFoundError(f"Директория не найдена: {parts_dir}")

    md_files = sorted(parts_dir.glob("*.md"))
    logger.info("Найдено файлов для сборки: %d", len(md_files))

    if not md_files:
        logger.warning("Нет .md файлов в директории parts")
        output_path.write_text("", encoding="utf-8")
        return

    parts_content = []
    for f in md_files:
        content = f.read_text(encoding="utf-8")
        parts_content.append(content)
        logger.debug("Добавлен: %s, размер: %d символов", f.name, len(content))

    full_content = "\n\n".join(parts_content)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(full_content, encoding="utf-8")

    logger.info("Собран документ: %s, размер: %d символов", output_path, len(full_content))


def main() -> None:
    base_dir = Path(__file__).parent
    parts_dir = base_dir / "parts"
    output_path = base_dir / "guide-assembled.md"

    if len(sys.argv) > 1:
        output_path = Path(sys.argv[1])

    logger.info("Сборка Markdown из %s -> %s", parts_dir, output_path)
    assemble_markdown(parts_dir, output_path)
    logger.info("Сборка завершена")


if __name__ == "__main__":
    main()
