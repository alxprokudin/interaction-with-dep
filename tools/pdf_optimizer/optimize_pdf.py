#!/usr/bin/env python3
"""
Скрипт оптимизации PDF файлов с помощью Ghostscript.

Использование:
    python scripts/optimize_pdf.py /путь/к/папке [--quality ebook|screen|printer|prepress]
    
Примеры:
    python scripts/optimize_pdf.py ./files
    python scripts/optimize_pdf.py ./files --quality screen  # максимальное сжатие
    python scripts/optimize_pdf.py ./files --quality ebook   # баланс (по умолчанию)
    python scripts/optimize_pdf.py ./files --quality printer # высокое качество
"""

import subprocess
import sys
import argparse
from pathlib import Path
from typing import Optional


# Настройки качества Ghostscript
QUALITY_SETTINGS = {
    "screen": "/screen",        # 72 dpi, максимальное сжатие
    "low": "custom:96",         # 96 dpi, кастомное
    "medium": "custom:120",     # 120 dpi, кастомное (рекомендую)
    "ebook": "/ebook",          # 150 dpi, хороший баланс
    "printer": "/printer",      # 300 dpi, высокое качество
    "prepress": "/prepress",    # 300 dpi, максимальное качество
}


def get_file_size_mb(path: Path) -> float:
    """Получить размер файла в МБ."""
    return path.stat().st_size / 1024 / 1024


def optimize_pdf(input_path: Path, output_path: Path, quality: str = "ebook") -> bool:
    """
    Оптимизировать PDF файл с помощью Ghostscript.
    
    Args:
        input_path: Путь к исходному PDF
        output_path: Путь для сохранения оптимизированного PDF
        quality: Качество (screen, low, medium, ebook, printer, prepress)
    
    Returns:
        True если успешно, False при ошибке
    """
    quality_setting = QUALITY_SETTINGS.get(quality, "/ebook")
    
    # Базовые параметры
    cmd = [
        "gs",
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4",
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
    ]
    
    # Кастомные настройки DPI или встроенные пресеты
    if quality_setting.startswith("custom:"):
        dpi = int(quality_setting.split(":")[1])
        cmd.extend([
            f"-dColorImageResolution={dpi}",
            f"-dGrayImageResolution={dpi}",
            f"-dMonoImageResolution={dpi}",
            "-dColorImageDownsampleType=/Bicubic",
            "-dGrayImageDownsampleType=/Bicubic",
            "-dMonoImageDownsampleType=/Subsample",
            "-dDownsampleColorImages=true",
            "-dDownsampleGrayImages=true",
            "-dDownsampleMonoImages=true",
            "-dColorConversionStrategy=/sRGB",
            "-dAutoRotatePages=/None",
        ])
    else:
        cmd.append(f"-dPDFSETTINGS={quality_setting}")
        cmd.extend([
            "-dColorImageDownsampleType=/Bicubic",
            "-dGrayImageDownsampleType=/Bicubic",
            "-dMonoImageDownsampleType=/Bicubic",
        ])
    
    cmd.extend([
        f"-sOutputFile={output_path}",
        str(input_path),
    ])
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  ⚠️ Таймаут при обработке {input_path.name}")
        return False
    except Exception as e:
        print(f"  ❌ Ошибка: {e}")
        return False


def process_folder(
    folder_path: Path, 
    quality: str = "ebook",
    replace: bool = False,
    output_folder: Optional[Path] = None,
) -> dict:
    """
    Обработать все PDF файлы в папке рекурсивно.
    
    Args:
        folder_path: Путь к папке
        quality: Качество сжатия
        replace: Заменить оригинальные файлы
        output_folder: Папка для сохранения (если не replace)
    
    Returns:
        Статистика обработки
    """
    if not folder_path.exists():
        print(f"❌ Папка не найдена: {folder_path}")
        return {}
    
    # Находим все PDF файлы
    pdf_files = list(folder_path.rglob("*.pdf")) + list(folder_path.rglob("*.PDF"))
    
    if not pdf_files:
        print(f"📂 PDF файлы не найдены в {folder_path}")
        return {}
    
    print(f"📂 Найдено PDF файлов: {len(pdf_files)}")
    print(f"🔧 Качество: {quality}")
    print("-" * 60)
    
    stats = {
        "total": len(pdf_files),
        "success": 0,
        "failed": 0,
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
        elif output_folder:
            relative_path = pdf_file.relative_to(folder_path)
            final_output = output_folder / relative_path
            final_output.parent.mkdir(parents=True, exist_ok=True)
            temp_output = final_output
        else:
            temp_output = pdf_file.with_name(f"{pdf_file.stem}_optimized.pdf")
            final_output = temp_output
        
        print(f"📄 {pdf_file.name} ({original_size:.2f} MB) ... ", end="", flush=True)
        
        success = optimize_pdf(pdf_file, temp_output, quality)
        
        if success and temp_output.exists():
            new_size = get_file_size_mb(temp_output)
            
            # Если оптимизированный файл больше или такой же — пропускаем
            if new_size >= original_size:
                print(f"⏭️ Пропущен (не уменьшился)")
                if replace and temp_output.exists():
                    temp_output.unlink()
                stats["optimized_size"] += original_size
            else:
                reduction = (1 - new_size / original_size) * 100
                
                if replace:
                    # Заменяем оригинал
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
        description="Оптимизация PDF файлов с помощью Ghostscript",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python scripts/optimize_pdf.py ./files
  python scripts/optimize_pdf.py ./files --quality screen --replace
  python scripts/optimize_pdf.py ./files --output ./files_optimized
        """,
    )
    parser.add_argument("folder", help="Путь к папке с PDF файлами")
    parser.add_argument(
        "--quality", 
        choices=["screen", "low", "medium", "ebook", "printer", "prepress"],
        default="medium",
        help="Качество: screen(72dpi), low(96dpi), medium(120dpi), ebook(150dpi), printer/prepress(300dpi)"
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Заменить оригинальные файлы"
    )
    parser.add_argument(
        "--output",
        help="Папка для сохранения оптимизированных файлов"
    )
    
    args = parser.parse_args()
    
    folder_path = Path(args.folder).resolve()
    output_folder = Path(args.output).resolve() if args.output else None
    
    print("=" * 60)
    print("🗜️  PDF OPTIMIZER (Ghostscript)")
    print("=" * 60)
    
    stats = process_folder(
        folder_path,
        quality=args.quality,
        replace=args.replace,
        output_folder=output_folder,
    )
    
    if stats:
        print("-" * 60)
        print("📊 ИТОГО:")
        print(f"   Файлов обработано: {stats['success']}/{stats['total']}")
        print(f"   Исходный размер:   {stats['original_size']:.2f} MB")
        print(f"   После оптимизации: {stats['optimized_size']:.2f} MB")
        
        if stats['original_size'] > 0:
            total_reduction = (1 - stats['optimized_size'] / stats['original_size']) * 100
            saved = stats['original_size'] - stats['optimized_size']
            print(f"   Сэкономлено:       {saved:.2f} MB ({total_reduction:.1f}%)")
        
        if stats['failed'] > 0:
            print(f"   ⚠️ Ошибок: {stats['failed']}")
    
    print("=" * 60)


if __name__ == "__main__":
    main()
