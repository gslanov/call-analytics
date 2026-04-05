"""Утилиты приложения."""

import os
import re


def sanitize_filename(raw: str) -> str:
    """Очистить имя файла: убрать путь, null-байты, скрытые точки.

    Защита от path traversal: из ``../../etc/passwd.mp3`` получится ``passwd.mp3``.
    """
    # Берём только последний компонент пути (обрабатываем оба разделителя)
    name = os.path.basename(raw)
    # Убираем null-байты
    name = name.replace("\x00", "")
    # Убираем ведущие точки (скрытые файлы типа .htaccess)
    name = name.lstrip(".")
    # Схлопываем пробелы
    name = re.sub(r"\s+", " ", name).strip()
    return name or "unknown"
