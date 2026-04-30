"""Утилиты приложения."""

import os
import re
from datetime import datetime, time

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

    Returns: {call_date, call_time, caller_phone, operator_name}
    """
    stem = os.path.splitext(name)[0]
    parts = stem.split("__")
    result: dict[str, str | None] = {
        "call_date": None, "call_time": None,
        "caller_phone": None, "operator_name": None,
    }

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

    # Части 3+4: телефон и оператор
    for part in parts[2:]:
        cleaned = part.strip()
        if not cleaned:
            continue
        if cleaned[0].isdigit():
            # Телефон
            digits = re.sub(r"\D", "", cleaned)
            if len(digits) >= 4:
                result["caller_phone"] = f"**{digits[-4:]}"
        elif not cleaned.startswith("sip_"):
            # Имя оператора (не sip-адрес, не телефон)
            result["operator_name"] = cleaned

    return result


def parse_call_started_at(name: str) -> datetime | None:
    """Парсит фактическую дату+время звонка из имени файла Манго.

    Имена идут в виде `2026-04-04__19-51-19__...` — это локальное время МСК
    (так пишет Манго в callsrec). Возвращаем naive datetime в МСК.

    Замечание про TZ: `created_at` в БД хранится как `func.now()` (TZ контейнера
    Postgres, обычно UTC). Фильтр в reports/results использует
    `COALESCE(call_started_at, created_at)` — для записей с заполненным
    `call_started_at` (после бэкфилла — это все классические звонки) фильтр
    работает в МСК и совпадает с тем, что вводит РОП. Для редких записей с
    NULL (имена не от Манго) фолбэк через `created_at` даёт сдвиг в 3 часа,
    но это окраина выборки.
    """
    stem = os.path.splitext(name)[0]
    parts = stem.split("__")
    if len(parts) < 2:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", parts[0])
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    tm = re.match(r"^(\d{2})-(\d{2})-(\d{2})$", parts[1])
    if tm:
        hour, minute, second = int(tm.group(1)), int(tm.group(2)), int(tm.group(3))
    else:
        hour = minute = second = 0
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def normalize_date_to(d: datetime | None) -> datetime | None:
    """Если в `date_to` нет времени (полночь), расширяем до конца суток.

    FilterBar шлёт `2026-04-24` без времени → FastAPI парсит как 00:00:00 →
    `<= date_to` отсекает все звонки за выбранный день. ReportsPage уже сам
    шлёт `T23:59:59`, его не трогаем (microsecond=0 + time=23:59:59 пройдёт
    проверку и не перезапишется).
    """
    if d is None:
        return None
    if d.time() == time(0, 0, 0) and d.microsecond == 0:
        return d.replace(hour=23, minute=59, second=59, microsecond=999999)
    return d


def fix_encoding(text: str) -> str:
    """Исправить двойную кодировку latin-1 → UTF-8 (curl/multipart на Windows)."""
    try:
        return text.encode('latin-1').decode('utf-8')
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text


def sanitize_filename(raw: str) -> str:
    """Очистить имя файла: убрать путь, null-байты, скрытые точки.

    Защита от path traversal: из ``../../etc/passwd.mp3`` получится ``passwd.mp3``.
    Исправляет кодировку: latin-1 → UTF-8 (curl/multipart на Windows).
    """
    raw = fix_encoding(raw)
    # Берём только последний компонент пути (обрабатываем оба разделителя)
    name = os.path.basename(raw)
    # Убираем null-байты
    name = name.replace("\x00", "")
    # Убираем ведущие точки (скрытые файлы типа .htaccess)
    name = name.lstrip(".")
    # Схлопываем пробелы
    name = re.sub(r"\s+", " ", name).strip()
    return name or "unknown"
