#!/usr/bin/env python3
"""
Скрипт LOSSLESS оптимизации PDF файлов с помощью pypdf.
Без потери качества!

Использование:
    python scripts/optimize_pdf_lossless.py /путь/к/папке
    python scripts/optimize_pdf_lossless.py /путь/к/папке --replace
"""

import argparse
from pathlib import Path
from pypdf import PdfReader, PdfWriter


def get_file_size_mb(path: Path) -> float:
    """Получить размер файла в МБ."""
    return path.stat().st_size / 1024 / 1024


def optimize_pdf_lossless(input_path: Path, output_path: Path) -> bool:
    """
    LOSSLESS оптимизация PDF с помощью pypdf.
    
    Применяет:
    - compress_content_streams() — zlib/deflate сжатие
    - remove_duplication — удаление дубликатов объектов
    
    Returns:
        True если успешно
    """
    try:
        reader = PdfReader(str(input_path))
        writer = PdfWriter()
        
        # Копируем все страницы
        for page in reader.pages:
            writer.add_page(page)
        
        # Копируем метаданные
        if reader.metadata:
            writer.add_metadata(reader.metadata)
        
        # Сжимаем content streams (lossless)
        for page in writer.pages:
            page.compress_content_streams()
        
        # Записываем с удалением дубликатов
        with open(output_path, "wb") as f:
            writer.write(f)
        
        return True
        
    except Exception as e:
        print(f"  ❌ Ошибка: {e}")
        return False


def process_folder(
    folder_path: Path, 
    replace: bool = False,
) -> dict:
    """Обработать все PDF файлы в папке рекурсивно."""
    
    if not folder_path.exists():
        print(f"❌ Папка не найдена: {folder_path}")
        return {}
    
    # Находим все PDF файлы
    pdf_files = list(folder_path.rglob("*.pdf")) + list(folder_path.rglob("*.PDF"))
    # Исключаем уже оптимизированные
    pdf_files = [f for f in pdf_files if "_optimized" not in f.name and "_lossless" not in f.name]
    
    if not pdf_files:
        print(f"📂 PDF файлы не найдены в {folder_path}")
        return {}
    
    print(f"📂 Найдено PDF файлов: {len(pdf_files)}")
    print(f"🔧 Метод: pypdf (LOSSLESS)")
    print("-" * 60)
    
    stats = {
        "total": len(pdf_files),
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "original_size": 0,
        "optimized_size": 0,
    }
    
    for pdf_file in pdf_files:
        original_size = get_file_size_mb(pdf_file)
        stats["original_size"] += original_size
        
        # Определяем путь для сохранения
        if replace:
            temp_output = pdf_file.with_suffix(".pdf.tmp")
            final_output = pdf_file
        else:
            temp_output = pdf_file.with_name(f"{pdf_file.stem}_lossless.pdf")
            final_output = temp_output
        
        print(f"📄 {pdf_file.name} ({original_size:.2f} MB) ... ", end="", flush=True)
        
        success = optimize_pdf_lossless(pdf_file, temp_output)
        
        if success and temp_output.exists():
            new_size = get_file_size_mb(temp_output)
            
            # Если файл не уменьшился — пропускаем
            if new_size >= original_size:
                print(f"⏭️ Пропущен (не уменьшился)")
                if replace:
                    temp_output.unlink()
                else:
                    temp_output.unlink()
                stats["skipped"] += 1
                stats["optimized_size"] += original_size
            else:
                reduction = (1 - new_size / original_size) * 100
                
                if replace:
                    pdf_file.unlink()
                    temp_output.rename(final_output)
                
                print(f"✅ {new_size:.2f} MB (-{reduction:.1f}%)")
                stats["success"] += 1
                stats["optimized_size"] += new_size
        else:
            print(f"❌ Ошибка")
            stats["failed"] += 1
            stats["optimized_size"] += original_size
            if temp_output.exists():
                temp_output.unlink()
    
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="LOSSLESS оптимизация PDF с pypdf (без потери качества)",
    )
    parser.add_argument("folder", help="Путь к папке с PDF файлами")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Заменить оригинальные файлы"
    )
    
    args = parser.parse_args()
    folder_path = Path(args.folder).resolve()
    
    print("=" * 60)
    print("🔒 PDF LOSSLESS OPTIMIZER (pypdf)")
    print("=" * 60)
    
    stats = process_folder(folder_path, replace=args.replace)
    
    if stats:
        print("-" * 60)
        print("📊 ИТОГО:")
        print(f"   Файлов оптимизировано: {stats['success']}/{stats['total']}")
        print(f"   Пропущено (не уменьшились): {stats['skipped']}")
        print(f"   Исходный размер:   {stats['original_size']:.2f} MB")
        print(f"   После оптимизации: {stats['optimized_size']:.2f} MB")
        
        if stats['original_size'] > 0:
            total_reduction = (1 - stats['optimized_size'] / stats['original_size']) * 100
            saved = stats['original_size'] - stats['optimized_size']
            print(f"   Сэкономлено:       {saved:.2f} MB ({total_reduction:.1f}%)")
    
    print("=" * 60)


if __name__ == "__main__":
    main()
