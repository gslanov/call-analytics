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

_PROMPT_TEMPLATE = """Это запись телефонного звонка колл-центра доставки осетинских пирогов «Пироги №1».

ЗАДАЧА: транскрибируй ДОСЛОВНО то, что реально звучит в записи, и размети роли.

╔══════════════════════════════════════════════════════════════════════╗
║ ГЛАВНОЕ ПРАВИЛО: пиши ТОЛЬКО то, что слышишь.                        ║
║ НИЧЕГО не додумывай, не достраивай и не «восстанавливай по смыслу».  ║
║ Лучше вернуть две строки, чем придумать связный диалог.              ║
╚══════════════════════════════════════════════════════════════════════╝

Во многих записях разговора НЕТ вообще. Бывают: гудки, тишина, музыка,
автоответчик («продолжаем дозваниваться», «оставайтесь на линии», «абонент
не отвечает»), обрыв на первой секунде, случайный набор.

Если в записи нет живого диалога двух людей — верни РОВНО одну строку:
NO_DIALOG
и больше ничего. Не описывай запись, не комментируй, не пересказывай.

Если диалог есть, но короткий или оборванный — верни ровно те реплики,
которые звучат, и ни одной сверх того.

Роли определяй по содержанию. Оператор звонит первым, называет компанию и
ведёт разговор; клиент отвечает. {operators_line}
Если слово неразборчиво — пиши как расслышал, не заменяй «правдоподобным».

Верни ТОЛЬКО строки в формате, без пояснений и без JSON:
[M:SS] ОПЕРАТОР: текст
[M:SS] КЛИЕНТ: текст"""

# Модель, получив запись без речи, склонна сочинить «типичный звонок».
# Ловим по физике: живая русская речь идёт примерно 12-20 символов в секунду,
# выше 30 человек просто не говорит. Порог с запасом — душить нормальные
# быстрые диалоги нельзя.
MAX_CHARS_PER_SEC = 30

# Вторая подпись выдумки: модель не смогла разложить реплики по времени и
# свалила их в одну-две метки (наблюдали 15 реплик на двух таймкодах).
MIN_DISTINCT_STARTS = 3


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
    crosschecked: bool = False


_CROSSCHECK_PROMPT = """Ты сверяешь две расшифровки ОДНОЙ И ТОЙ ЖЕ телефонной записи.

ЭТАЛОН — дословная расшифровка от другой модели. Ролей и таймкодов в ней нет,
зато она НЕ склонна выдумывать: что услышала, то и записала.

РАЗМЕТКА — диалог с ролями и таймкодами. Роли и время в ней надёжные, но она
иногда ДОДУМЫВАЕТ реплики, которых в записи не было.

ЗАДАЧА: собрать финальную версию.

Правила:
1. Таймкоды и роли бери из РАЗМЕТКИ.
2. Текст сверяй с ЭТАЛОНОМ. Реплику, которой в ЭТАЛОНЕ нет даже близко по
   смыслу, УДАЛИ целиком — это выдумка. Мелкие расхождения в словах не
   считаются: там, где ЭТАЛОН точнее, бери формулировку из него.
3. Если в ЭТАЛОНЕ есть слова, которых нет в РАЗМЕТКЕ, добавь их в подходящую
   по смыслу реплику. Роль определи по содержанию.
4. НИЧЕГО не придумывай сам. Не дополняй разговор «как обычно бывает».
5. Если ЭТАЛОН пуст или в нём нет человеческой речи (гудки, тишина, фразы
   автоответчика вроде «продолжаем дозваниваться», «абонент не отвечает»),
   верни РОВНО одну строку: NO_DIALOG

ЭТАЛОН:
{reference}

РАЗМЕТКА:
{dialog}

Верни ТОЛЬКО строки формата, без пояснений:
[M:SS] ОПЕРАТОР: текст
[M:SS] КЛИЕНТ: текст"""


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

                # «Диалога нет» — это ВАЛИДНЫЙ ответ, а не сбой: гудки,
                # автоответчик, тишина. Пустая транскрипция уйдёт классификатору,
                # тот пометит звонок no_answer/short, и LLM-анализ не запустится.
                # На fallback НЕ уходим — иначе за каждый недозвон платили бы
                # четыре прохода OpenAI ради того же пустого результата.
                if _is_no_dialog(text):
                    logger.info(
                        "AudioDialog: %s/%s — живого диалога в записи нет",
                        cand["channel"], cand["model"],
                    )
                    return AudioDialogResult(
                        full_text="", segments=[],
                        model_used=cand["model"], channel=cand["channel"],
                    )

                segments = self._parse(text, duration_sec)
                if len(segments) < MIN_SEGMENTS:
                    raise RuntimeError(
                        f"разобрано {len(segments)} реплик — ответ не похож на диалог"
                    )
                if not any(s.speaker == "operator" for s in segments):
                    raise RuntimeError("в ответе нет ни одной реплики оператора")
                self._reject_if_hallucinated(segments, duration_sec)

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

    def crosscheck(
        self,
        dialog: AudioDialogResult,
        reference_text: str,
        *,
        duration_sec: float | None = None,
    ) -> AudioDialogResult:
        """Сверяет диалог с дословной расшифровкой другой модели.

        Зачем: Gemini слышит роли и время, но на записях без речи склонен
        сочинить «типичный звонок» (инцидент 19.08). gpt-4o-transcribe ролей не
        даёт, зато не выдумывает. Сверка берёт у каждого сильную сторону:
        роли и таймкоды из Gemini, факт сказанного — из эталона.

        Ловит обе ошибки:
          - выдуманные реплики (в эталоне их нет) — вычищаются;
          - пропущенный разговор (Gemini вернул пусто, а речь была) — эталон
            заставляет собрать диалог.

        При любой неудаче возвращает исходный dialog: сверка — страховка, она
        не должна ронять обработку.
        """
        ref = (reference_text or "").strip()

        # Эталон пуст — речи в записи не было, что бы там ни «услышал» Gemini.
        if not ref:
            if dialog.segments:
                logger.warning(
                    "Сверка: эталон пуст, а в разметке %d реплик — считаем выдумкой",
                    len(dialog.segments),
                )
            return AudioDialogResult(
                full_text="", segments=[],
                model_used=dialog.model_used, channel=dialog.channel,
                crosschecked=True,
            )

        try:
            text, model = self._chat_text(
                _CROSSCHECK_PROMPT.format(reference=ref, dialog=dialog.full_text or "(пусто)")
            )
        except Exception as exc:
            logger.warning("Сверка не удалась (%s) — оставляем разметку как есть", exc)
            return dialog

        if _is_no_dialog(text):
            logger.info("Сверка: живой речи в записи нет")
            return AudioDialogResult(
                full_text="", segments=[],
                model_used=dialog.model_used, channel=dialog.channel,
                crosschecked=True,
            )

        segments = self._parse(text, duration_sec)
        if len(segments) < MIN_SEGMENTS:
            logger.warning(
                "Сверка вернула %d реплик — не доверяем, оставляем исходную разметку",
                len(segments),
            )
            return dialog

        try:
            self._reject_if_hallucinated(segments, duration_sec)
        except RuntimeError as exc:
            logger.warning("Результат сверки сам похож на выдумку (%s) — откат", exc)
            return dialog

        removed = len(dialog.segments) - len(segments)
        if removed > 0:
            logger.info("Сверка убрала %d недостоверных реплик", removed)
        elif removed < 0:
            logger.info("Сверка добавила %d реплик из эталона", -removed)

        return AudioDialogResult(
            full_text="\n".join(
                f"[{_fmt_ts(s.start)}] {_role_ru(s.speaker)}: {s.text}" for s in segments
            ),
            segments=segments,
            model_used=f"{dialog.model_used}+crosscheck",
            channel=dialog.channel,
            crosschecked=True,
        )

    def _chat_text(self, prompt: str) -> tuple[str, str]:
        """Текстовый запрос по той же цепочке кандидатов, что и аудио."""
        candidates = self._build_candidates()
        if not candidates:
            raise RuntimeError("нет доступных каналов для сверки")

        last_exc: Exception | None = None
        for cand in candidates:
            try:
                kwargs: dict[str, Any] = dict(
                    model=cand["model"],
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    timeout=settings.audio_dialog_timeout,
                )
                if cand["channel"] == "openrouter":
                    kwargs["extra_body"] = (
                        {"reasoning": {"effort": "low"}} if _needs_reasoning(cand["model"])
                        else {"reasoning": {"enabled": False}}
                    )
                else:
                    kwargs["reasoning_effort"] = "low"

                resp = cand["client"].chat.completions.create(**kwargs)
                if not resp.choices:
                    raise RuntimeError("пустой choices")
                content = (resp.choices[0].message.content or "").strip()
                if not content:
                    raise RuntimeError("пустой content")
                return content, cand["model"]
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Сверка %s/%s failed: %s", cand["channel"], cand["model"], exc,
                )
                continue

        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _reject_if_hallucinated(
        segments: list[AudioDialogSegment], duration_sec: float | None
    ) -> None:
        """Отбраковывает сочинённый диалог.

        19.08 на 27-секундной записи, где звучал только автоответчик, модель
        выдала полный разговор с адресом, составом заказа и суммой — всё
        придумано по описанию из промпта. Ловим по двум физическим признакам.

        Raises:
            RuntimeError: ответ похож на выдумку → уходим на следующего
            кандидата, а если и там не выйдет — на исторический путь.
        """
        if not duration_sec or duration_sec <= 0:
            return

        chars = sum(len(s.text) for s in segments)
        density = chars / duration_sec
        if density > MAX_CHARS_PER_SEC:
            raise RuntimeError(
                f"плотность речи {density:.0f} симв/сек при длительности "
                f"{duration_sec:.0f} с — столько за это время не выговорить, "
                f"похоже на выдуманный диалог"
            )

        distinct_starts = len({s.start for s in segments})
        if len(segments) >= 6 and distinct_starts < MIN_DISTINCT_STARTS:
            raise RuntimeError(
                f"{len(segments)} реплик всего на {distinct_starts} метках "
                f"времени — модель не привязала их к записи, похоже на выдумку"
            )

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


def _is_no_dialog(text: str) -> bool:
    """Модель сообщила, что живого разговора в записи нет.

    Терпимо к оформлению: модель может обернуть маркер в кавычки, точку или
    выдать его одной строкой среди пустых.
    """
    stripped = text.strip().strip('`"\'*. ').upper()
    if stripped == "NO_DIALOG":
        return True
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    return len(lines) == 1 and lines[0].strip('`"\'*. ').upper() == "NO_DIALOG"


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
