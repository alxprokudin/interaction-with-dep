# PDF Optimizer

Инструменты для оптимизации PDF файлов.

## Требования

```bash
# Ghostscript (для lossy сжатия)
brew install ghostscript

# pypdf (для lossless сжатия)
pip install pypdf
```

## Скрипты

### 1. optimize_pdf.py — Lossy сжатие (Ghostscript)

Уменьшает размер за счёт снижения разрешения изображений.

```bash
# Базовое использование (quality=medium, 120 dpi)
python optimize_pdf.py /путь/к/папке

# С выбором качества
python optimize_pdf.py /путь/к/папке --quality screen   # 72 dpi, макс. сжатие
python optimize_pdf.py /путь/к/папке --quality low      # 96 dpi
python optimize_pdf.py /путь/к/папке --quality medium   # 120 dpi (рекомендую)
python optimize_pdf.py /путь/к/папке --quality ebook    # 150 dpi
python optimize_pdf.py /путь/к/папке --quality printer  # 300 dpi

# Заменить оригиналы
python optimize_pdf.py /путь/к/папке --quality medium --replace
```

**Типичные результаты:**
| Качество | Экономия | Качество изображений |
|----------|----------|---------------------|
| screen | 70-80% | Низкое (размыто) |
| medium | 50-60% | Хорошее |
| ebook | 10-30% | Очень хорошее |

### 2. optimize_pdf_lossless.py — Lossless сжатие (pypdf)

Сжимает без потери качества (работает только если PDF не оптимизирован).

```bash
# Базовое использование
python optimize_pdf_lossless.py /путь/к/папке

# Заменить оригиналы
python optimize_pdf_lossless.py /путь/к/папке --replace
```

**Примечание:** Lossless сжатие эффективно только для PDF с несжатыми потоками. Для сканов даёт минимальный результат (0-5%).

## Рекомендации

1. **Для email-вложений:** используйте `medium` (120 dpi) — хороший баланс
2. **Для архивов:** используйте `ebook` (150 dpi) — высокое качество
3. **Для сканов документов:** проверьте качество перед заменой оригиналов
4. **Для важных документов:** сначала сделайте резервную копию
