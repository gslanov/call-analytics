"""AudioDialogService — транскрибация + диаризация ОДНИМ вызовом мультимодальной модели.

Зачем: исторический путь гонял каждый звонок через OpenAI ЧЕТЫРЕ раза
(gpt-4o-transcribe на микс + на левый канал + на правый канал, плюс whisper-1
ради таймкодов) и потом ещё склеивал результат LLM-мержем. Здесь то же самое
делается одним запросом: аудио на вход → готовый диалог с ролями и таймкодами.

Замер 18.08.2026 на 12 звонках, проверенных РОП (см. память
project_gemini_audio_test_2026_08_18):
  - роли не переворачиваются: 0/12 при обеих моделях, хотя Gemini схлопывает
    стерео в моно и определяет роли по смыслу;
  - текста больше на 7-15%, причём именно за счёт реплик, которые старый путь
    терял («Всё верно?», «тарелки, вилки, салфетки», прощание оператора) —
    а это чекбоксы РОП;
  - $0.0026-0.0050 за звонок против ~$0.034 у старого пути;
  - таймкоды грубее (медиана 2-3 сек), но сдвиг в сторону «раньше», поэтому
    плеер в UI не срезает начало реплики.

Fallback: если вся цепочка аудио-моделей не смогла, вызывающий код (pipeline)
откатывается на исторический путь OpenAI. Gemini за лето ложился трижды
(06.05 kie, 01.06 кредиты, 18.08 free tier), поэтому этот откат обязателен.
"""

from __future__ import annotations

import base64
import logging
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Ретраи внутри одного кандидата. Те же константы, что в whisper_service —
# поведение при 429/5xx должно быть одинаковым по всему пайплайну.
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0
RETRY_MAX_DELAY = 60.0
RATE_LIMIT_BASE_DELAY = 10.0

# base64 раздувает файл на ~33%, а вся туша летит в JSON одним куском.
# Наши звонки ~450 КБ, так что лимит с большим запасом; всё что крупнее
# уходит на исторический путь (там есть чанкинг).
MAX_AUDIO_BYTES = 20 * 1024 * 1024

# Ответ должен быть строками «[M:SS] РОЛЬ: текст». Если модель вернула меньше
# реплик — считаем ответ мусором и идём на следующего кандидата.
MIN_SEGMENTS = 2

_LINE_RE = re.compile(r"^\[?(\d{1,2}):(\d{2})\]?\s*([А-Яа-яA-Za-z]+)\s*:\s*(.+)$")

_PROMPT_TEMPLATE = """Это запись телефонного разговора колл-центра доставки осетинских пирогов «Пироги №1».
Оператор звонит клиенту подтвердить заказ.

ЗАДАЧА: транскрибируй разговор дословно по-русски и размети роли.

Признаки ОПЕРАТОРА: приветствует и называет компанию («Пироги №1»), представляется по имени,
подтверждает заказ, проговаривает адрес/состав/сумму/время, предлагает доп. товары, прощается.
Признаки КЛИЕНТА: короткие ответы («да», «хорошо», «угу»), отвечает на вопросы, спрашивает про цену и время.

{operators_line}
Доменные слова: осетинские пироги, хачапури, сулугуни, облепиха, чак-чак, самовывоз, курьер.

Верни ТОЛЬКО строки в формате, без пояснений и без JSON:
[M:SS] ОПЕРАТОР: текст
[M:SS] КЛИЕНТ: текст"""


@dataclass
class AudioDialogSegment:
    speaker: str  # "operator" | "client" | "unknown"
    start: float
    end: float
    text: str


@dataclass
class AudioDialogResult:
    full_text: str
    segments: list[AudioDialogSegment]
    model_used: str
    channel: str


class AudioDialogService:
    """Один вызов: аудио → диалог с ролями. Синглтон, как остальные сервисы."""

    _instance: "AudioDialogService | None" = None
    _candidates_cache: list[dict[str, Any]] | None = None

    @classmethod
    def get_instance(cls) -> "AudioDialogService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Цепочка кандидатов
    # ------------------------------------------------------------------

    def _build_candidates(self) -> list[dict[str, Any]]:
        """Разбирает audio_dialog_chain в список клиентов по порядку fallback.

        Формат элемента: "<канал>:<модель>", канал = gemini | openrouter.
        Кандидаты без соответствующего ключа в .env молча пропускаются —
        так одна и та же строка конфига работает и на тесте, и на проде.
        """
        if self._candidates_cache is not None:
            return self._candidates_cache

        from openai import OpenAI

        out: list[dict[str, Any]] = []
        for item in settings.audio_dialog_chain.split(","):
            item = item.strip()
            if not item or ":" not in item:
                continue
            channel, model = item.split(":", 1)
            channel, model = channel.strip(), model.strip()

            if channel == "gemini":
                if not settings.gemini_api_key:
                    continue
                client = OpenAI(
                    api_key=settings.gemini_api_key,
                    base_url=settings.gemini_base_url,
                )
            elif channel == "openrouter":
                if not settings.openrouter_api_key:
                    continue
                client = OpenAI(
                    api_key=settings.openrouter_api_key,
                    base_url=settings.openrouter_base_url,
                )
            else:
                logger.warning("AudioDialog: неизвестный канал '%s' — пропускаю", channel)
                continue

            out.append({"channel": channel, "model": model, "client": client})

        for c in out:
            logger.info("AudioDialog chain: %s → %s", c["channel"], c["model"])

        self._candidates_cache = out
        return out

    def is_configured(self) -> bool:
        return bool(self._build_candidates())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def transcribe_dialog(
        self,
        audio_path: str,
        *,
        duration_sec: float | None = None,
        operator_names: list[str] | None = None,
    ) -> AudioDialogResult:
        """Аудио → диалог с ролями. Блокирующий вызов (звать через to_thread).

        Raises:
            RuntimeError: если ни один кандидат не смог отдать валидный диалог.
                          Вызывающий код должен откатиться на исторический путь.
        """
        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        size = path.stat().st_size
        if size > MAX_AUDIO_BYTES:
            raise RuntimeError(
                f"Файл {size / 1024 / 1024:.1f} МБ больше лимита "
                f"{MAX_AUDIO_BYTES / 1024 / 1024:.0f} МБ для audio_dialog"
            )

        candidates = self._build_candidates()
        if not candidates:
            raise RuntimeError(
                "AudioDialog: не задан ни один канал "
                "(нужен GEMINI_API_KEY или OPENROUTER_API_KEY)"
            )

        audio_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        fmt = path.suffix.lstrip(".").lower() or "mp3"
        prompt = self._build_prompt(operator_names)

        last_exc: Exception | None = None
        for idx, cand in enumerate(candidates):
            try:
                text = self._call_with_retry(cand, prompt, audio_b64, fmt)
                segments = self._parse(text, duration_sec)
                if len(segments) < MIN_SEGMENTS:
                    raise RuntimeError(
                        f"разобрано {len(segments)} реплик — ответ не похож на диалог"
                    )
                if not any(s.speaker == "operator" for s in segments):
                    raise RuntimeError("в ответе нет ни одной реплики оператора")

                if idx > 0:
                    logger.warning(
                        "AudioDialog: сработал fallback-уровень %d (%s/%s)",
                        idx, cand["channel"], cand["model"],
                    )
                logger.info(
                    "AudioDialog: %s/%s → %d реплик",
                    cand["channel"], cand["model"], len(segments),
                )
                return AudioDialogResult(
                    full_text="\n".join(
                        f"[{_fmt_ts(s.start)}] {_role_ru(s.speaker)}: {s.text}"
                        for s in segments
                    ),
                    segments=segments,
                    model_used=cand["model"],
                    channel=cand["channel"],
                )
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "AudioDialog %s/%s failed: %s: %s",
                    cand["channel"], cand["model"], type(exc).__name__, exc,
                )
                continue

        raise RuntimeError(f"AudioDialog: вся цепочка не смогла — {last_exc}") from last_exc

    # ------------------------------------------------------------------
    # Внутреннее
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(operator_names: list[str] | None) -> str:
        """Промпт с актуальным списком операторов.

        Список тянется из таблицы operators, а не хардкодится: на 18.08 в
        зашитом списке не было Игнатовой Лады, и модель слышала «Влада»
        вместо «Лада».
        """
        if operator_names:
            names = ", ".join(operator_names)
            # «могут звучать» вместо «операторы такие» — чтобы модель
            # использовала список как подсказку при распознавании, а не
            # подставляла имена туда, где их не было.
            line = (
                "Имена и фамилии операторов, которые могут звучать в записи: "
                f"{names}.\n"
            )
        else:
            line = ""
        return _PROMPT_TEMPLATE.format(operators_line=line)

    def _call_with_retry(
        self,
        cand: dict[str, Any],
        prompt: str,
        audio_b64: str,
        fmt: str,
    ) -> str:
        """Один кандидат с exponential backoff + full jitter."""
        try:
            from openai import RateLimitError
        except Exception:  # pragma: no cover
            RateLimitError = ()  # type: ignore[assignment]

        client, model, channel = cand["client"], cand["model"], cand["channel"]
        last_exc: Exception | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                kwargs: dict[str, Any] = dict(
                    model=model,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "input_audio",
                                "input_audio": {"data": audio_b64, "format": fmt},
                            },
                        ],
                    }],
                    temperature=0,
                    timeout=settings.audio_dialog_timeout,
                )

                # Reasoning-токены тарифицируются как output и легко удваивают
                # цену. Gemini 3.7 при этом ВООБЩЕ не разрешает выключить
                # reasoning ("Reasoning is mandatory for this endpoint") —
                # ему ставим минимальный effort, остальным выключаем.
                if channel == "openrouter":
                    if _needs_reasoning(model):
                        kwargs["extra_body"] = {"reasoning": {"effort": "low"}}
                    else:
                        kwargs["extra_body"] = {"reasoning": {"enabled": False}}
                elif channel == "gemini":
                    kwargs["reasoning_effort"] = "low"

                response = client.chat.completions.create(**kwargs)

                if not response.choices:
                    raise RuntimeError(f"пустой choices: {response}")
                content = (response.choices[0].message.content or "").strip()
                if not content:
                    raise RuntimeError("пустой content")
                return content

            except Exception as exc:
                last_exc = exc
                is_rate_limit = bool(RateLimitError) and isinstance(exc, RateLimitError)
                # Квота кончилась — ретраить бессмысленно, сразу к следующему
                # кандидату (это ровно случай 18.08: 5 ретраев × N звонков
                # молотили в стену).
                if _is_quota_exhausted(exc):
                    logger.warning(
                        "AudioDialog %s/%s: баланс/квота исчерпаны — ретраи не помогут",
                        channel, model,
                    )
                    raise
                # Неверная модель, битый запрос, отозванный ключ — ретраить
                # нечего, ответ не изменится. Сразу к следующему кандидату.
                if _is_permanent(exc):
                    logger.warning(
                        "AudioDialog %s/%s: постоянная ошибка (%s) — ретраи не помогут",
                        channel, model, type(exc).__name__,
                    )
                    raise
                if attempt >= MAX_RETRIES:
                    raise
                base = RATE_LIMIT_BASE_DELAY if is_rate_limit else RETRY_BASE_DELAY
                cap = min(RETRY_MAX_DELAY, base * (2 ** (attempt - 1)))
                delay = random.uniform(0, cap)
                logger.warning(
                    "AudioDialog %s/%s attempt %d/%d failed (%s: %s). Retry in %.1fs",
                    channel, model, attempt, MAX_RETRIES,
                    type(exc).__name__, exc, delay,
                )
                time.sleep(delay)

        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _parse(text: str, duration_sec: float | None) -> list[AudioDialogSegment]:
        """Разбирает «[M:SS] РОЛЬ: текст» в сегменты.

        end каждой реплики = start следующей (модель отдаёт только начало).
        Немонотонные и убежавшие за длительность метки подтягиваются, иначе
        плеер в UI прыгает в пустоту.
        """
        raw: list[tuple[float, str, str]] = []
        for line in text.splitlines():
            m = _LINE_RE.match(line.strip())
            if not m:
                continue
            start = int(m.group(1)) * 60 + int(m.group(2))
            role = m.group(3).upper()
            body = m.group(4).strip()
            if not body:
                continue
            if role.startswith("ОПЕР") or role.startswith("OPER"):
                speaker = "operator"
            elif role.startswith("КЛИ") or role.startswith("CLI"):
                speaker = "client"
            else:
                speaker = "unknown"
            raw.append((float(start), speaker, body))

        if not raw:
            return []

        # монотонность
        fixed: list[tuple[float, str, str]] = []
        prev = 0.0
        for start, speaker, body in raw:
            if start < prev:
                start = prev
            if duration_sec and start > duration_sec:
                start = max(prev, float(duration_sec))
            fixed.append((start, speaker, body))
            prev = start

        segments: list[AudioDialogSegment] = []
        for i, (start, speaker, body) in enumerate(fixed):
            if i + 1 < len(fixed):
                end = max(start, fixed[i + 1][0])
            elif duration_sec:
                end = max(start, float(duration_sec))
            else:
                end = start
            segments.append(
                AudioDialogSegment(speaker=speaker, start=start, end=end, text=body)
            )
        return segments


def _needs_reasoning(model: str) -> bool:
    """Модели, которым OpenRouter не даёт выключить reasoning совсем."""
    return "3.7" in model


def _is_permanent(exc: Exception) -> bool:
    """Ошибки, которые от повтора не пройдут: битый запрос, модель, ключ.

    RateLimitError сюда НЕ попадает (он транзиентный и ретраится), а
    исчерпанную квоту ловит отдельный _is_quota_exhausted до этой проверки.
    """
    try:
        from openai import (
            AuthenticationError, BadRequestError, NotFoundError, PermissionDeniedError,
        )
    except Exception:  # pragma: no cover
        return False
    return isinstance(
        exc, (AuthenticationError, BadRequestError, NotFoundError, PermissionDeniedError)
    )


def _is_quota_exhausted(exc: Exception) -> bool:
    s = str(exc).lower()
    return any(k in s for k in (
        "insufficient_quota",
        "credit_balance_exhausted",
        "no credits remaining",
        "resource_exhausted",
        "free_tier",
        "quota exceeded",
    ))


def _fmt_ts(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


def _role_ru(speaker: str) -> str:
    return {"operator": "ОПЕРАТОР", "client": "КЛИЕНТ"}.get(speaker, "НЕЯСНО")
