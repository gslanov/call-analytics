"""LLMService — call quality analysis via GPT-4 API.

Evaluates operator performance on 22 binary criteria across three groups:
  - standard (13):  protocol compliance checklist
  - loyalty (6):    client-orientation & tone
  - kindness (3):   politeness & professionalism

Each criterion is true/false/null (null = not applicable).
Scores are computed mathematically: % of passed items among applicable ones.

Graceful degradation: returns None when API is unavailable / key not set.
Retry: 3x with exponential backoff.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Retry
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0  # seconds

# Criteria version — increment when criteria change
CRITERIA_VERSION = "v2"

# Expected keys per group for validation
CRITERIA_SCHEMA: dict[str, list[str]] = {
    "standard": [
        "introduced_self",
        "named_company",
        "clarified_delivery_date",
        "stated_delivery_time",
        "stated_full_address",
        "named_metro",
        "stated_order_contents",
        "offered_upsell",
        "explained_upsell_benefit",
        "named_order_total",
        "clarified_courier_comment",
        "clarified_portion_sufficiency",
        "clarified_cash_change",
    ],
    "loyalty": [
        "addressed_by_name",
        "did_not_raise_voice",
        "friendly_calm_confident_tone",
        "did_not_interrupt",
        "calm_in_conflict",
        "answered_all_questions",
    ],
    "kindness": [
        "no_profanity_filler_words",
        "polite_goodbye",
        "no_sarcasm_irony_aggression",
    ],
}

# Russian labels for each criterion (used by frontend too via API)
CRITERIA_LABELS: dict[str, dict[str, str]] = {
    "standard": {
        "introduced_self": "Представился",
        "named_company": "Назвал компанию",
        "clarified_delivery_date": "Уточнил дату доставки",
        "stated_delivery_time": "Проговорил время доставки",
        "stated_full_address": "Проговорил адрес полностью",
        "named_metro": "Назвал метро",
        "stated_order_contents": "Проговорил состав заказа",
        "offered_upsell": "Предложил апсейл",
        "explained_upsell_benefit": "Рассказал про выгоду апсейла",
        "named_order_total": "Назвал сумму заказа",
        "clarified_courier_comment": "Уточнил комментарий для курьера",
        "clarified_portion_sufficiency": "Уточнил количество человек / хватит ли пирогов",
        "clarified_cash_change": "Уточнил сдачу при оплате наличными",
    },
    "loyalty": {
        "addressed_by_name": "Обращался к клиенту по имени",
        "did_not_raise_voice": "Не повышал тон",
        "friendly_calm_confident_tone": "Дружелюбный, спокойный и уверенный тон",
        "did_not_interrupt": "Не перебивал клиента",
        "calm_in_conflict": "Спокойствие в конфликтной ситуации",
        "answered_all_questions": "Ответил на все вопросы клиента",
    },
    "kindness": {
        "no_profanity_filler_words": "Нет мата и слов-паразитов",
        "polite_goodbye": "Вежливо попрощался",
        "no_sarcasm_irony_aggression": "Нет сарказма, иронии и агрессии",
    },
}

SYSTEM_PROMPT = """Ты — эксперт по оценке качества обслуживания в контакт-центре доставки осетинских пирогов.
Операторы обрабатывают входящие звонки и подтверждают заказы.

Проверь оператора по каждому из 22 критериев ниже. По каждому верни:
- true — оператор выполнил
- false — оператор НЕ выполнил
- null — критерий неприменим к данному звонку

## 1. СТАНДАРТЫ (standard)
1. introduced_self — Оператор представился (назвал своё имя)
2. named_company — Оператор произнёс название компании
3. clarified_delivery_date — Оператор уточнил дату доставки
4. stated_delivery_time — Оператор проговорил время доставки
5. stated_full_address — Оператор полностью проговорил адрес доставки
6. named_metro — Оператор назвал станцию метро (null если метро неприменимо — например, доставка за МКАД или в области)
7. stated_order_contents — Оператор проговорил состав заказа (перечислил что заказано)
8. offered_upsell — Оператор предложил дополнительный продукт (апсейл)
9. explained_upsell_benefit — Оператор рассказал про выгоду апсейла (null если апсейл не предлагался, т.е. offered_upsell=false)
10. named_order_total — Оператор назвал итоговую сумму заказа
11. clarified_courier_comment — Оператор уточнил, нужно ли оставить комментарий для курьера
12. clarified_portion_sufficiency — Оператор уточнил количество человек и хватит ли пирогов на компанию (null если клиент сам чётко указал на сколько человек)
13. clarified_cash_change — Оператор уточнил нужна ли сдача при оплате наличными (null если оплата не наличными)

## 2. ЛОЯЛЬНОСТЬ (loyalty)
1. addressed_by_name — Оператор обращался к клиенту по имени хотя бы раз
2. did_not_raise_voice — Оператор не разговаривал на повышенных тонах (оцени по лексике и характеру реплик)
3. friendly_calm_confident_tone — Оператор поддерживал дружелюбный, спокойный и уверенный тон
4. did_not_interrupt — Оператор не перебивал клиента (оцени по структуре диалога)
5. calm_in_conflict — Оператор сохранял спокойствие в конфликтной ситуации (null если конфликта не было)
6. answered_all_questions — Оператор ответил на все вопросы клиента

## 3. ДОБРОЖЕЛАТЕЛЬНОСТЬ (kindness)
1. no_profanity_filler_words — Оператор не использовал ненормативную лексику и слова-паразиты (ну, типа, как бы, э-э)
2. polite_goodbye — Оператор вежливо попрощался с клиентом
3. no_sarcasm_irony_aggression — Оператор избегал сарказма, иронии и агрессивных формулировок

## ФОРМАТ ОТВЕТА
Каждый критерий — объект: {"value": true/false/null, "reason": "пояснение", "timestamp": "M:SS"}.
- value: true (выполнено), false (не выполнено), null (неприменимо)
- reason: ОБЯЗАТЕЛЬНО для false — точная цитата оператора или описание проблемы. Для true/null — краткое пояснение.
- timestamp: время в формате "M:SS" — момент в записи, где это видно. ОБЯЗАТЕЛЬНО для false (когда это должно было быть). Для true — момент, где оператор это сделал. Для null — не указывать.
  Бери таймстемпы из меток [M:SS] в тексте реплик.

Верни ТОЛЬКО JSON без пояснений:
{
  "details": {
    "standard": {
      "introduced_self": {"value": true, "reason": "«Меня зовут Анастасия»", "timestamp": "0:02"},
      "named_company": {"value": true, "reason": "«Компания Пироги номер один»", "timestamp": "0:01"},
      "clarified_delivery_date": {"value": false, "reason": "Дата доставки не была озвучена", "timestamp": "0:30"},
      "stated_delivery_time": {"value": true, "reason": "«с 20:45 до 21:45»", "timestamp": "1:15"},
      "stated_full_address": {"value": true, "reason": "«улица Виненосская, дом 13»", "timestamp": "0:45"},
      "named_metro": {"value": null, "reason": "Метро не упоминалось, адрес в области"},
      "stated_order_contents": {"value": true, "reason": "«хачаны с картофельным сыром и мясом»", "timestamp": "0:35"},
      "offered_upsell": {"value": false, "reason": "Доп. продукт не предложен", "timestamp": "2:10"},
      "explained_upsell_benefit": {"value": null, "reason": "Апсейл не предлагался"},
      "named_order_total": {"value": true, "reason": "«4300 общая сумма заказа»", "timestamp": "1:50"},
      "clarified_courier_comment": {"value": false, "reason": "Комментарий для курьера не обсуждался", "timestamp": "2:30"},
      "clarified_portion_sufficiency": {"value": null, "reason": "Клиент сам указал количество"},
      "clarified_cash_change": {"value": null, "reason": "Оплата картой"}
    },
    "loyalty": {
      "addressed_by_name": {"value": true, "reason": "«Андрей, правильно?»", "timestamp": "0:10"},
      "did_not_raise_voice": {"value": true, "reason": "Ровный спокойный тон"},
      "friendly_calm_confident_tone": {"value": true, "reason": "Дружелюбное общение"},
      "did_not_interrupt": {"value": true, "reason": "Не перебивал"},
      "calm_in_conflict": {"value": null, "reason": "Конфликта не было"},
      "answered_all_questions": {"value": true, "reason": "На все вопросы ответил"}
    },
    "kindness": {
      "no_profanity_filler_words": {"value": false, "reason": "Слова-паразиты: «ну», «как бы», «типа»", "timestamp": "1:30"},
      "polite_goodbye": {"value": true, "reason": "«Спасибо большое, хорошего дня!»", "timestamp": "3:15"},
      "no_sarcasm_irony_aggression": {"value": true, "reason": "Без сарказма и агрессии"}
    }
  },
  "summary": "<2-3 предложения на русском: что хорошо, что улучшить>",
  "quotes": [
    {"text": "<точная цитата ОПЕРАТОРА>", "criterion": "<standard|loyalty|kindness>", "sentiment": "<positive|negative>"}
  ]
}

ВАЖНО:
- reason для false-пунктов: ТОЧНАЯ цитата оператора или конкретное описание. НЕ «не выполнено».
- Цитаты (quotes) — ТОЛЬКО из реплик ОПЕРАТОРА. Мы оцениваем работу оператора, НЕ клиента.
- Цитат: 3-6 штук (и положительные, и отрицательные).
- Никакого текста вне JSON. Никакого Markdown. Только фигурные скобки."""

STRICT_SYSTEM_PROMPT = SYSTEM_PROMPT + (
    "\n\nОТВЕЧАЙ СТРОГО JSON. Никакого Markdown, никакого ```json. Только фигурные скобки."
)


@dataclass
class AnalysisResult:
    standard: int
    loyalty: int
    kindness: int
    overall: int
    summary: str
    details: dict[str, dict[str, bool | None]] | None = None
    criteria_version: str = CRITERIA_VERSION
    quotes: list[dict[str, str]] = field(default_factory=list)
    llm_model: str = "gpt-4o"
    partial: bool = False   # True if some fields were missing/clamped


def _parse_timestamp(ts: str) -> float:
    """Parse 'M:SS' or 'MM:SS' to seconds."""
    import re
    m = re.match(r"(\d+):(\d{2})", ts.strip())
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    return 0.0


def _compute_group_score(items: dict[str, bool | None]) -> int:
    """Compute score as percentage of true items among non-null items."""
    applicable = {k: v for k, v in items.items() if v is not None}
    if not applicable:
        return 100  # all items N/A → no penalty
    passed = sum(1 for v in applicable.values() if v is True)
    return round(passed / len(applicable) * 100)


def _apply_dependencies(details: dict[str, dict[str, bool | None]]) -> None:
    """Apply business logic dependencies between criteria (mutates in place).

    This ensures consistency: if a prerequisite is false, the dependent
    criterion becomes null (not applicable) rather than false.
    """
    std = details.get("standard", {})

    # If upsell not offered → benefit explanation is N/A
    if std.get("offered_upsell") is False:
        std["explained_upsell_benefit"] = None

    loy = details.get("loyalty", {})
    # calm_in_conflict is already null if no conflict — just validate
    # (GPT handles this, we don't override)


class LLMService:
    """GPT-4 analysis service (singleton)."""

    _instance: "LLMService | None" = None
    _client: Any = None

    @classmethod
    def get_instance(cls) -> "LLMService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _get_client(self) -> Any:
        """Lazy-init OpenAI client. Returns None if API key not set."""
        if self._client is not None:
            return self._client
        if not settings.openai_api_key:
            return None
        from openai import OpenAI
        self._client = OpenAI(api_key=settings.openai_api_key)
        return self._client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        operator_text: str,
        client_context: str = "",
    ) -> AnalysisResult | None:
        """Analyse operator speech via GPT-4.

        Args:
            operator_text: Operator's utterances from diarization.
            client_context: Client's utterances (for context, not scored).

        Returns:
            AnalysisResult or None if GPT-4 is unavailable.
        """
        client = self._get_client()
        if client is None:
            logger.warning(
                "OPENAI_API_KEY not set — LLM analysis unavailable (graceful degradation)"
            )
            return None

        if not operator_text.strip():
            logger.warning("LLM: operator_text is empty — skipping analysis")
            return None

        user_message = self._build_user_message(operator_text, client_context)

        # Try with strict prompt on retry
        result = self._call_with_retry(client, user_message, strict=False)
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _build_user_message(operator_text: str, client_context: str) -> str:
        msg = f"=== Реплики оператора ===\n{operator_text.strip()}"
        if client_context.strip():
            msg += f"\n\n=== Реплики клиента (для контекста, не оценивается) ===\n{client_context.strip()}"
        return msg

    def _call_with_retry(
        self,
        client: Any,
        user_message: str,
        *,
        strict: bool = False,
    ) -> AnalysisResult | None:
        """Call GPT-4 with retry on failure or invalid JSON."""
        last_exc: Exception | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            sys_prompt = STRICT_SYSTEM_PROMPT if (strict or attempt > 1) else SYSTEM_PROMPT
            try:
                raw = self._call_api(client, sys_prompt, user_message)
                result = self._parse_and_validate(raw)
                if result is not None:
                    logger.info("LLM analysis done on attempt %d", attempt)
                    return result
                # Invalid JSON → retry with strict prompt
                logger.warning(
                    "LLM attempt %d: invalid JSON response, retrying…", attempt
                )
            except Exception as exc:
                last_exc = exc
                err_type = type(exc).__name__
                if attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "LLM attempt %d/%d failed (%s: %s). Retrying in %.1fs…",
                        attempt, MAX_RETRIES, err_type, exc, delay,
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        "LLM failed after %d attempts (%s: %s) — graceful degradation",
                        MAX_RETRIES, err_type, exc,
                    )

        return None  # graceful degradation

    def _call_api(self, client: Any, system_prompt: str, user_message: str) -> str:
        """Single GPT-4 API call. Returns raw response text."""
        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
            timeout=60,
        )
        return response.choices[0].message.content or ""

    def _parse_and_validate(self, raw: str) -> AnalysisResult | None:
        """Parse GPT-4 response and validate all required fields.

        Returns AnalysisResult or None if JSON is invalid / unparseable.
        """
        # Strip possible markdown code fences
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                l for l in lines if not l.strip().startswith("```")
            ).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("LLM JSON parse error: %s | raw=%r", exc, raw[:200])
            return None

        if not isinstance(data, dict):
            logger.warning("LLM response is not a dict: %r", data)
            return None

        # --- Validate details ---
        details = data.get("details")
        if not isinstance(details, dict):
            logger.warning("LLM response missing 'details' dict")
            return None

        partial = False

        # Validate each group
        # New format: each criterion is {value: bool|null, reason: str}
        # Also support old format (plain bool/null) for backward compat
        validated_details: dict[str, dict[str, bool | None]] = {}
        reasons: dict[str, dict[str, str]] = {}
        for group, expected_keys in CRITERIA_SCHEMA.items():
            group_data = details.get(group)
            if not isinstance(group_data, dict):
                logger.warning("LLM details missing group '%s'", group)
                return None

            validated_group: dict[str, bool | None] = {}
            group_reasons: dict[str, str] = {}
            group_timestamps: dict[str, float] = {}
            for key in expected_keys:
                raw_val = group_data.get(key)

                # New format: {"value": ..., "reason": ..., "timestamp": ...}
                if isinstance(raw_val, dict):
                    val = raw_val.get("value")
                    reason = str(raw_val.get("reason", ""))
                    if reason:
                        group_reasons[key] = reason
                    ts = raw_val.get("timestamp")
                    if ts:
                        group_timestamps[key] = _parse_timestamp(str(ts))
                else:
                    val = raw_val

                if val is None:
                    validated_group[key] = None
                elif isinstance(val, bool):
                    validated_group[key] = val
                else:
                    if isinstance(val, str):
                        if val.lower() == "true":
                            validated_group[key] = True
                        elif val.lower() == "false":
                            validated_group[key] = False
                        else:
                            validated_group[key] = None
                            partial = True
                    else:
                        validated_group[key] = None
                        partial = True
                        logger.warning(
                            "LLM details[%s][%s] unexpected type: %r", group, key, raw_val
                        )

            validated_details[group] = validated_group
            reasons[group] = group_reasons
            if group_timestamps:
                reasons[f"{group}_timestamps"] = group_timestamps

        # Apply business logic dependencies
        _apply_dependencies(validated_details)

        # --- Compute scores mathematically ---
        standard_score = _compute_group_score(validated_details["standard"])
        loyalty_score = _compute_group_score(validated_details["loyalty"])
        kindness_score = _compute_group_score(validated_details["kindness"])
        overall_score = round(
            standard_score * 0.4 + loyalty_score * 0.3 + kindness_score * 0.3
        )

        # --- Validate summary ---
        summary = str(data.get("summary", "")).strip()
        if not summary:
            logger.warning("LLM response: empty summary")
            partial = True

        # --- Validate quotes ---
        quotes = data.get("quotes", [])
        if not isinstance(quotes, list):
            quotes = []
            partial = True

        valid_quotes: list[dict[str, str]] = []
        for q in quotes:
            if isinstance(q, dict) and "text" in q and "criterion" in q:
                valid_quotes.append({
                    "text": str(q["text"]),
                    "criterion": str(q["criterion"]),
                    "sentiment": str(q.get("sentiment", "neutral")),
                })

        # Merge reasons into details for frontend
        full_details: dict[str, Any] = {
            **validated_details,
            "reasons": reasons,
        }

        return AnalysisResult(
            standard=standard_score,
            loyalty=loyalty_score,
            kindness=kindness_score,
            overall=overall_score,
            summary=summary,
            details=full_details,
            criteria_version=CRITERIA_VERSION,
            quotes=valid_quotes,
            llm_model="gpt-4o",
            partial=partial,
        )
