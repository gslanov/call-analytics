"""Утилиты приложения."""

import os
import re

# Паттерны российских телефонов в транскрипте
# Цифрами: +79031947793, 89031947793, 79031947793
# С разделителями: +7 903 194 77 93, 8-903-194-77-93
_PHONE_PATTERNS = [
    # +7/8 с 10 цифрами (с разделителями)
    re.compile(r'(?<!\d)[+]?[78][\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)'),
    # 11 цифр подряд начиная с 7 или 8
    re.compile(r'(?<!\d)[78]\d{10}(?!\d)'),
    # 10 цифр подряд (без кода страны) — 9031947793
    re.compile(r'(?<!\d)9\d{9}(?!\d)'),
]


def mask_phone_numbers(text: str) -> str:
    """Маскирует телефонные номера в тексте: +7903****793 → +7903***793."""
    for pattern in _PHONE_PATTERNS:
        text = pattern.sub(lambda m: _mask_phone(m.group()), text)
    return text


def _mask_phone(phone: str) -> str:
    """Оставляет первые 4 и последние 2 цифры, остальное заменяет на *."""
    digits = re.sub(r'\D', '', phone)
    if len(digits) < 7:
        return phone  # слишком короткое, не телефон
    masked = digits[:4] + '*' * (len(digits) - 6) + digits[-2:]
    return masked


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
