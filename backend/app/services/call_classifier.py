"""call_classifier — определяет тип звонка по сегментам диалога.

Используется для автоматического отсева неклассических звонков (недозвон,
автоответчик, внутренний разговор курьер↔диспетчер, обрыв) — РОП их не оценивает.

Возвращает один из:
  - "classical"  — обычный клиентский разговор по подтверждению заказа (НЕ удаляется)
  - "no_answer"  — IVR Манго: «Продолжаем дозваниваться», «Абонент не берёт трубку»
  - "voicemail"  — попадание на автоответчик/секретаря клиента
  - "internal"   — звонок между сотрудниками (диспетчер ↔ курьер)
  - "short"      — слишком короткий, чтобы оценивать (< 30 сек живой речи и < 6 реплик)

Подход: regex по объединённому тексту + эвристики по длине. GPT-5.4 не вызываем,
чтобы не платить за классификацию — паттерны достаточно стабильны.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Фразы Манго в IVR-сообщениях (недозвон, оставайтесь на линии и т.п.)
_NO_ANSWER_PATTERNS = [
    r"продолжаем дозванив",
    r"абонент не бер[её]т трубку",
    r"абонент не отвечает",
    r"оставайтесь на линии",
    r"попробуйте перезвонить (поз[же]|позднее)",
    r"абонент временно недоступен",
    r"вызываемый абонент не отвечает",
]

# Фразы автоответчика клиента / секретаря, принимающего звонки за абонента
_VOICEMAIL_PATTERNS = [
    r"принимаю звонки.*пока",
    r"абонент установил меня",
    r"меня зовут\s+\w+.*что нужно сообщить абоненту",
    r"перезвоните абоненту",
    r"автоответчик",
    r"оставьте сообщение после сигнала",
    r"оставьте.*ваше сообщение",
    r"запишу.*передам.*абоненту",
]

# Признаки внутреннего звонка между сотрудниками: курьер ↔ диспетчер
# Триггерим только при сочетании нескольких признаков (см. _is_internal)
_INTERNAL_HINTS = [
    r"я уже на\s",
    r"подъезжаю",
    r"опоздание\s+\d+\s*минут",
    r"курьер[ау]?\s+(сейчас|уже|подъехал)",
    r"через\s+\d+\s*минут.*буду",
    r"скажите.*через сколько.*будете",
]

_compiled_no_answer = [re.compile(p, re.IGNORECASE) for p in _NO_ANSWER_PATTERNS]
_compiled_voicemail = [re.compile(p, re.IGNORECASE) for p in _VOICEMAIL_PATTERNS]
_compiled_internal = [re.compile(p, re.IGNORECASE) for p in _INTERNAL_HINTS]


def classify_call(segments: list[dict[str, Any]]) -> str:
    """Классифицирует звонок по сегментам диалога.

    Args:
        segments: список dict с ключами speaker/start/end/text (как в БД diarizations.segments)

    Returns:
        "classical" | "no_answer" | "voicemail" | "internal" | "short"
    """
    if not segments:
        return "short"

    full_text = " ".join((s.get("text") or "").lower() for s in segments)
    duration = max((float(s.get("end") or 0) for s in segments), default=0.0)
    seg_count = len(segments)

    # 1. IVR-недозвон Манго — самый строгий критерий: содержит ключевую фразу
    if any(p.search(full_text) for p in _compiled_no_answer):
        # Дополнительно: должно быть мало контента (не разговор, а служебка)
        if duration < 60 or seg_count <= 5:
            return "no_answer"

    # 2. Автоответчик — секретарь говорит, что принимает звонки
    if any(p.search(full_text) for p in _compiled_voicemail):
        return "voicemail"

    # 3. Внутренний звонок: 2+ признака И отсутствие классических маркеров.
    #    Проверяем ПЕРЕД short, т.к. курьер↔диспетчер часто короткий.
    internal_hits = sum(1 for p in _compiled_internal if p.search(full_text))
    has_classical_marker = any(
        marker in full_text
        for marker in (
            "пироги номер один",
            "пироги №1",
            "компания пироги",
            "подтверждаю заказ",
            "звоню по поводу",
            "звоню для подтверждения",
            "сумма заказа",
            "адрес доставки",
        )
    )
    if internal_hits >= 2 and not has_classical_marker:
        return "internal"

    # 4. Слишком короткий — нечего оценивать
    if duration < 30 and seg_count <= 6:
        return "short"

    return "classical"
