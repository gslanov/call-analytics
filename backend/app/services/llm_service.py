"""LLMService — call quality analysis via GPT-4 API.

Evaluates operator performance on three criteria (0-100):
  - standard:  protocol compliance (greeting, intro, clarification, farewell)
  - loyalty:   client-orientation (problem solving, retention, objection handling)
  - kindness:  tone (politeness, empathy, calmness)

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

SYSTEM_PROMPT = """Ты — эксперт по оценке качества обслуживания в контакт-центре.
Оцени оператора по трём критериям (0-100):
1. Стандарты — соблюдение протокола (приветствие, представление, уточнение проблемы, прощание)
2. Лояльность — клиентоориентированность (решение проблемы, удержание, работа с возражениями)
3. Доброжелательность — тон общения (вежливость, эмпатия, спокойствие)

Верни ТОЛЬКО JSON без пояснений в формате:
{
  "standard": <0-100>,
  "loyalty": <0-100>,
  "kindness": <0-100>,
  "overall": <средневзвешенное: standard*0.4 + loyalty*0.3 + kindness*0.3>,
  "summary": "<2-3 предложения на русском о качестве работы оператора>",
  "quotes": [
    {"text": "<цитата>", "criterion": "<standard|loyalty|kindness>", "sentiment": "<positive|negative|neutral>"}
  ]
}
Цитат: 2-5 штук. Никакого текста вне JSON."""

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
    quotes: list[dict[str, str]] = field(default_factory=list)
    llm_model: str = "gpt-4"
    partial: bool = False   # True if some fields were missing/clamped


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
            msg += f"\n\n=== Контекст клиента (для понимания ситуации) ===\n{client_context.strip()}"
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
            model="gpt-4",
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
            # Remove first and last fence lines
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

        required = {"standard", "loyalty", "kindness", "overall", "summary"}
        missing = required - data.keys()
        if missing:
            logger.warning("LLM response missing fields: %s", missing)
            return None

        partial = False

        # Validate and clamp numeric scores
        scores: dict[str, int] = {}
        for field_name in ("standard", "loyalty", "kindness", "overall"):
            try:
                val = int(data[field_name])
            except (TypeError, ValueError):
                logger.warning("LLM field %s is not numeric: %r", field_name, data[field_name])
                return None
            if not (0 <= val <= 100):
                logger.warning("LLM anomaly: %s=%d out of range, clamping", field_name, val)
                val = max(0, min(100, val))
                partial = True
            scores[field_name] = val

        summary = str(data.get("summary", "")).strip()
        if not summary:
            logger.warning("LLM response: empty summary")
            partial = True

        quotes = data.get("quotes", [])
        if not isinstance(quotes, list):
            quotes = []
            partial = True

        # Validate each quote
        valid_quotes: list[dict[str, str]] = []
        for q in quotes:
            if isinstance(q, dict) and "text" in q and "criterion" in q:
                valid_quotes.append({
                    "text": str(q["text"]),
                    "criterion": str(q["criterion"]),
                    "sentiment": str(q.get("sentiment", "neutral")),
                })

        # Recompute overall as weighted average (override LLM if significantly off)
        expected_overall = round(
            scores["standard"] * 0.4
            + scores["loyalty"] * 0.3
            + scores["kindness"] * 0.3
        )
        if abs(scores["overall"] - expected_overall) > 5:
            logger.info(
                "LLM overall=%d differs from computed=%d — using computed",
                scores["overall"], expected_overall,
            )
            scores["overall"] = expected_overall

        return AnalysisResult(
            standard=scores["standard"],
            loyalty=scores["loyalty"],
            kindness=scores["kindness"],
            overall=scores["overall"],
            summary=summary,
            quotes=valid_quotes,
            llm_model="gpt-4",
            partial=partial,
        )
