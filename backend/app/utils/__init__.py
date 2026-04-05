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


def parse_call_filename(name: str) -> dict[str, str | None]:
    """Парсит информацию о звонке из имени файла.

    Форматы:
      2026-04-04__19-51-19__79252463351__Менеджер Галина.mp3
      2026-04-04__09-49-23__Менеджер Анастасия__79160870602.mp3

    Returns: {call_date: "04.04", call_time: "19:51", caller_phone: "**3351"}
    """
    stem = os.path.splitext(name)[0]
    parts = stem.split("__")
    result: dict[str, str | None] = {"call_date": None, "call_time": None, "caller_phone": None}

    if len(parts) < 3:
        return result

    # Часть 1: дата (2026-04-04 → 04.04)
    date_match = re.match(r"(\d{4})-(\d{2})-(\d{2})", parts[0])
    if date_match:
        result["call_date"] = f"{date_match.group(3)}.{date_match.group(2)}"

    # Часть 2: время (19-51-19 → 19:51)
    time_match = re.match(r"(\d{2})-(\d{2})", parts[1])
    if time_match:
        result["call_time"] = f"{time_match.group(1)}:{time_match.group(2)}"

    # Части 3+4: телефон — тот, что начинается с цифры
    for part in parts[2:]:
        cleaned = part.strip()
        if cleaned and cleaned[0].isdigit():
            # Убираем нецифровые символы, берём последние 4 цифры
            digits = re.sub(r"\D", "", cleaned)
            if len(digits) >= 4:
                result["caller_phone"] = f"**{digits[-4:]}"
            break

    return result


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
