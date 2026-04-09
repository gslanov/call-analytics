"""DiarizationService — speaker separation for audio files.

Two strategies (selected automatically by channel count):
  - Stereo (2 channels): L=Operator, R=Client. Confidence: 100%.
  - Mono (1 channel): pyannote/speaker-diarization-3.1 via HuggingFace.
    speaker_0 (first voice) = Operator, speaker_1 = Client.

Merge: combines Whisper word_timestamps with diarization segments to produce
       TranscriptSegment list with speaker labels (operator/client).
"""

from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from app.config import settings

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000

# Confidence thresholds
LOW_CONFIDENCE_THRESHOLD = 70.0


@dataclass
class DiarizationSegment:
    """Raw diarization segment (before merge with transcript)."""
    speaker: str          # "operator" | "client" | "unknown"
    start: float          # seconds
    end: float            # seconds


@dataclass
class TranscriptSegment:
    """Transcript segment after merging diarization + Whisper words."""
    speaker: str          # "operator" | "client" | "unknown"
    start: float
    end: float
    text: str


@dataclass
class DiarizationResult:
    segments: list[DiarizationSegment]
    transcript_segments: list[TranscriptSegment]
    method: str           # "channel_split" | "pyannote"
    confidence: float | None  # None = 100% for channel_split (exact)
    num_speakers: int
    warnings: list[str] = field(default_factory=list)


class DiarizationService:
    """Speaker diarization service."""

    _instance: "DiarizationService | None" = None
    _pipeline: Any = None

    @classmethod
    def get_instance(cls) -> "DiarizationService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def diarize(
        self,
        audio_path: str,
        word_timestamps: list[dict[str, Any]],
    ) -> DiarizationResult:
        """Main entry: choose strategy by channel count, then merge with transcript.

        Args:
            audio_path: Path to audio file.
            word_timestamps: List of {word, start, end} from WhisperService.

        Returns:
            DiarizationResult with segments, transcript_segments, confidence, warnings.
        """
        path = Path(audio_path)
        num_channels = self._get_channel_count(path)
        logger.info("Diarizing %s (%d channel(s))", path.name, num_channels)

        if num_channels == 2:
            return self._diarize_stereo(path, word_timestamps)
        else:
            return self._diarize_mono(path, word_timestamps)

    # ------------------------------------------------------------------
    # Strategy 1: Stereo channel split
    # ------------------------------------------------------------------

    # IVR detection patterns
    _IVR_PHRASES = [
        "ожидайте ответа",
        "вы позвонили",
        "контроля качества",
        "записываться",
        "нажмите",
        "пожалуйста ответа оператора",
        "для оформления",
    ]

    def _is_ivr_channel(self, phrases: list[TranscriptSegment]) -> bool:
        """Detect if channel contains only IVR/robot speech."""
        if not phrases:
            return True
        text = " ".join(p.text.lower() for p in phrases)
        ivr_hits = sum(1 for pat in self._IVR_PHRASES if pat in text)
        # IVR if: few phrases AND contains IVR patterns
        return len(phrases) <= 4 and ivr_hits >= 1

    def _diarize_stereo(
        self,
        path: Path,
        word_timestamps: list[dict[str, Any]],
    ) -> DiarizationResult:
        """Stereo diarization with IVR detection.

        1. Split words by channel energy (L=operator, R=client)
        2. If one channel looks like IVR (robot), use GPT-4o to diarize
           the live channel into operator/client
        """
        transcript_segments = self._merge_stereo(path, word_timestamps, SAMPLE_RATE)

        # Split into L-channel (operator) and R-channel (client) segments
        l_segs = [s for s in transcript_segments if s.speaker == "operator"]
        r_segs = [s for s in transcript_segments if s.speaker == "client"]

        l_is_ivr = self._is_ivr_channel(l_segs)
        r_is_ivr = self._is_ivr_channel(r_segs)

        if l_is_ivr and not r_is_ivr:
            # L = IVR robot, R = live conversation (both speakers)
            logger.info("Stereo: L-channel is IVR, using GPT-4o for R-channel diarization")
            _, r_words = self._classify_words_by_channel(word_timestamps, path)
            if r_words and settings.openai_api_key:
                result = self._diarize_mono_llm(path, r_words)
                result.method = "channel_split+llm"
                result.warnings = ["L-канал = IVR (робот). Живой разговор из R-канала размечен GPT-4o."]
                return result

        if r_is_ivr and not l_is_ivr:
            logger.info("Stereo: R-channel is IVR, using GPT-4o for L-channel diarization")
            l_words, _ = self._classify_words_by_channel(word_timestamps, path)
            if l_words and settings.openai_api_key:
                result = self._diarize_mono_llm(path, l_words)
                result.method = "channel_split+llm"
                result.warnings = ["R-канал = IVR (робот). Живой разговор из L-канала размечен GPT-4o."]
                return result

        # Normal stereo — both channels are live
        audio, sr = self._load_stereo(path)
        duration = audio.shape[1] / sr
        segments = [
            DiarizationSegment(speaker="operator", start=0.0, end=duration),
            DiarizationSegment(speaker="client",   start=0.0, end=duration),
        ]

        logger.info(
            "Stereo split: %.1f sec, %d transcript segments",
            duration,
            len(transcript_segments),
        )
        return DiarizationResult(
            segments=segments,
            transcript_segments=transcript_segments,
            method="channel_split",
            confidence=None,
            num_speakers=2,
        )

    def _classify_words_by_channel(
        self, word_timestamps: list[dict[str, Any]], path: Path
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split words into L-channel and R-channel lists by energy."""
        audio, sr = self._load_stereo(path)
        n_samples = audio.shape[1]
        l_words: list[dict[str, Any]] = []
        r_words: list[dict[str, Any]] = []
        for w in word_timestamps:
            s = max(0, int(float(w["start"]) * sr))
            e = min(n_samples, int(float(w["end"]) * sr))
            if s >= e:
                l_words.append(w)
                continue
            energy_l = float(np.sum(audio[0, s:e] ** 2))
            energy_r = float(np.sum(audio[1, s:e] ** 2))
            if energy_l >= energy_r:
                l_words.append(w)
            else:
                r_words.append(w)
        return l_words, r_words

    def _load_stereo(self, path: Path) -> tuple[np.ndarray, int]:
        """Load stereo audio via ffmpeg → numpy shape (2, N)."""
        cmd = [
            "ffmpeg", "-i", str(path),
            "-ar", str(SAMPLE_RATE),
            "-ac", "2",
            "-f", "f32le",
            "-loglevel", "quiet",
            "pipe:1",
        ]
        import subprocess
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg stereo decode failed: {result.stderr.decode(errors='replace')[:200]}")
        raw = np.frombuffer(result.stdout, dtype=np.float32)
        if raw.size == 0:
            raise RuntimeError(f"ffmpeg produced no audio data for {path.name}")
        # interleaved stereo: [L0, R0, L1, R1, ...]
        audio = raw.reshape(-1, 2).T   # shape (2, N)
        return audio, SAMPLE_RATE

    # Пауза между словами, после которой начинается новая фраза
    PHRASE_GAP_SEC = 0.8

    def _merge_stereo(
        self,
        path: Path,
        word_timestamps: list[dict[str, Any]],
        sr: int,
    ) -> list[TranscriptSegment]:
        """Группировка по фразам → определение спикера для всей фразы.

        1. Слова группируются в фразы по паузам (>0.8 сек = новая фраза)
        2. Для каждой фразы вычисляем долю энергии L-канала: ratio = L / (L + R)
        3. Глобальный медианный ratio разделяет спикеров (адаптивный порог)
        4. Результат — целые фразы с правильным спикером
        """
        audio, _ = self._load_stereo(path)
        n_samples = audio.shape[1]

        # Шаг 1: группируем слова в фразы по паузам
        phrases: list[list[dict[str, Any]]] = []
        current_phrase: list[dict[str, Any]] = []

        for w in word_timestamps:
            if current_phrase:
                gap = float(w["start"]) - float(current_phrase[-1]["end"])
                if gap > self.PHRASE_GAP_SEC:
                    phrases.append(current_phrase)
                    current_phrase = []
            current_phrase.append(w)
        if current_phrase:
            phrases.append(current_phrase)

        # Шаг 2: для каждой фразы считаем долю L-канала
        phrase_ratios: list[float] = []
        phrase_texts: list[str] = []
        phrase_times: list[tuple[float, float]] = []

        for phrase_words in phrases:
            total_energy_l = 0.0
            total_energy_r = 0.0
            for w in phrase_words:
                s = max(0, int(float(w["start"]) * sr))
                e = min(n_samples, int(float(w["end"]) * sr))
                if s >= e:
                    continue
                total_energy_l += float(np.sum(audio[0, s:e] ** 2))
                total_energy_r += float(np.sum(audio[1, s:e] ** 2))

            total = total_energy_l + total_energy_r
            ratio = total_energy_l / total if total > 0 else 0.5
            phrase_ratios.append(ratio)
            phrase_texts.append(" ".join(w["word"] for w in phrase_words))
            phrase_times.append((
                float(phrase_words[0]["start"]),
                float(phrase_words[-1]["end"]),
            ))

        # Шаг 3: определяем какой канал — оператор
        # Ищем intro-фразу (первая длинная реплика с характерными словами)
        _OPERATOR_INTRO = re.compile(
            r"(компани|пирог|меня зовут|здравствуйте.*компани|добрый день.*компани)",
            re.IGNORECASE,
        )
        operator_is_left = True  # default: L = operator
        for ratio, text in zip(phrase_ratios, phrase_texts):
            if _OPERATOR_INTRO.search(text):
                operator_is_left = ratio > 0.5
                logger.info(
                    "Operator channel detected from intro: %s (ratio=%.3f, text=%s)",
                    "LEFT" if operator_is_left else "RIGHT", ratio, text[:60],
                )
                break

        # Адаптивный порог — медиана ratio
        if phrase_ratios:
            sorted_ratios = sorted(phrase_ratios)
            threshold = sorted_ratios[len(sorted_ratios) // 2]
            if abs(threshold - 0.5) < 0.02:
                threshold = 0.5
        else:
            threshold = 0.5

        # Если оператор в правом канале — инвертируем логику
        if not operator_is_left:
            threshold = 1.0 - threshold

        logger.info(
            "Stereo adaptive threshold: %.3f (phrases: %d, op_left: %s)",
            threshold, len(phrases), operator_is_left,
        )

        # Шаг 4: назначаем спикеров
        # Контекстные маркеры оператора — фразы, которые говорит только оператор
        _OPERATOR_MARKERS = [
            re.compile(r"спасибо за заказ", re.IGNORECASE),
            re.compile(r"хорошего.*дня", re.IGNORECASE),
            re.compile(r"до свидания", re.IGNORECASE),
            re.compile(r"всего доброго", re.IGNORECASE),
            re.compile(r"итого.*\d", re.IGNORECASE),
            re.compile(r"сумма заказа", re.IGNORECASE),
            re.compile(r"будьте.*на связи", re.IGNORECASE),
            re.compile(r"курьер.*позвонит", re.IGNORECASE),
            re.compile(r"могу предложить", re.IGNORECASE),
            re.compile(r"подскажите", re.IGNORECASE),
            re.compile(r"компани.*пирог", re.IGNORECASE),
            re.compile(r"меня зовут", re.IGNORECASE),
        ]

        transcript_segments: list[TranscriptSegment] = []
        for i, (ratio, text, (start, end)) in enumerate(
            zip(phrase_ratios, phrase_texts, phrase_times)
        ):
            if operator_is_left:
                is_operator = ratio >= threshold
            else:
                is_operator = ratio <= threshold

            # Контекстная коррекция: если фраза содержит 2+ оператор-маркера
            # и помечена как client — переопределяем на operator
            if not is_operator:
                hits = sum(1 for m in _OPERATOR_MARKERS if m.search(text))
                if hits >= 2:
                    logger.info(
                        "Context override: phrase '%s' (ratio=%.3f) -> operator (%d markers)",
                        text[:50], ratio, hits,
                    )
                    is_operator = True

            speaker = "operator" if is_operator else "client"
            transcript_segments.append(
                TranscriptSegment(speaker=speaker, start=start, end=end, text=text)
            )

        return transcript_segments

    # ------------------------------------------------------------------
    # Strategy 2: Mono — pyannote diarization
    # ------------------------------------------------------------------

    def _diarize_mono(
        self,
        path: Path,
        word_timestamps: list[dict[str, Any]],
    ) -> DiarizationResult:
        """pyannote/speaker-diarization-3.1 on mono audio.

        Fallback без HF_TOKEN: GPT-4o размечает роли по тексту.
        """
        warnings: list[str] = []

        # GPT-4o диаризация — основной метод для моно (быстрее и точнее на CPU/8kHz)
        if settings.openai_api_key:
            logger.info("Using GPT-4o for mono diarization (primary method)")
            return self._diarize_mono_llm(path, word_timestamps)

        # pyannote — fallback если нет OpenAI ключа
        if not settings.hf_token:
            return self._fallback_single_speaker(path, word_timestamps,
                ["Диаризация недоступна: нет ни OPENAI_API_KEY, ни HF_TOKEN."])

        try:
            self._load_pipeline()
        except Exception as exc:
            logger.warning("pyannote unavailable (%s)", exc)
            return self._fallback_single_speaker(path, word_timestamps,
                [f"pyannote недоступен: {exc}"])

        # Загружаем аудио как tensor и передаём напрямую (обход torchcodec)
        import torch
        audio_np = self._load_as_16k_mono(path)
        waveform = torch.from_numpy(audio_np).unsqueeze(0)  # shape (1, N)
        audio_input = {"waveform": waveform, "sample_rate": SAMPLE_RATE}

        try:
            result = self._pipeline(audio_input)
            # pyannote 4.x returns DiarizeOutput, extract Annotation
            diarization = getattr(result, "speaker_diarization", result)
        except Exception as exc:
            logger.warning("pyannote processing failed (%s) — falling back to GPT-4o", exc)
            return self._diarize_mono_llm(path, word_timestamps)

        # Parse pyannote output (Annotation object)
        raw_segments: list[tuple[float, float, str]] = []  # (start, end, label)
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            raw_segments.append((turn.start, turn.end, speaker))

        num_speakers = len({s[2] for s in raw_segments})

        if num_speakers > 2:
            warnings.append(
                f"Обнаружено {num_speakers} говорящих. "
                "Оценка может быть неточной."
            )

        # Map pyannote labels → operator/client
        # First-appearing speaker = operator
        speaker_map = self._build_speaker_map(raw_segments)
        segments = [
            DiarizationSegment(
                speaker=speaker_map.get(lbl, "unknown"),
                start=start,
                end=end,
            )
            for start, end, lbl in raw_segments
        ]

        # Estimate confidence from segment overlap quality
        confidence = self._estimate_confidence(raw_segments)
        if confidence < LOW_CONFIDENCE_THRESHOLD:
            warnings.append(
                f"Разделение неуверенное ({confidence:.0f}%). "
                "Рекомендуем проверить вручную."
            )

        transcript_segments = self._merge_words_with_diarization(
            word_timestamps, segments
        )

        # Группируем мелкие сегменты в фразы по паузам (как в стерео)
        transcript_segments = self._group_into_phrases(transcript_segments)

        logger.info(
            "pyannote: %d speakers, confidence=%.1f%%, %d phrases",
            num_speakers,
            confidence,
            len(transcript_segments),
        )
        return DiarizationResult(
            segments=segments,
            transcript_segments=transcript_segments,
            method="pyannote",
            confidence=confidence,
            num_speakers=num_speakers,
            warnings=warnings,
        )

    def _diarize_mono_llm(
        self,
        path: Path,
        word_timestamps: list[dict[str, Any]],
    ) -> DiarizationResult:
        """Разметка ролей моно-записи через GPT-4o.

        Группируем слова в фразы по паузам, отправляем текст в GPT-4o
        с просьбой разметить operator/client для каждой фразы.
        """
        import json

        # Шаг 1: группируем слова в фразы по паузам
        phrases: list[dict[str, Any]] = []
        current_words: list[dict[str, Any]] = []

        for w in word_timestamps:
            if current_words:
                gap = float(w["start"]) - float(current_words[-1]["end"])
                if gap > self.PHRASE_GAP_SEC:
                    phrases.append({
                        "text": " ".join(ww["word"] for ww in current_words),
                        "start": float(current_words[0]["start"]),
                        "end": float(current_words[-1]["end"]),
                    })
                    current_words = []
            current_words.append(w)
        if current_words:
            phrases.append({
                "text": " ".join(ww["word"] for ww in current_words),
                "start": float(current_words[0]["start"]),
                "end": float(current_words[-1]["end"]),
            })

        if not phrases:
            return self._fallback_single_speaker(path, word_timestamps, [])

        # Шаг 2: формируем запрос для GPT-4o
        numbered_lines = "\n".join(
            f"{i+1}. {p['text']}" for i, p in enumerate(phrases)
        )

        prompt = (
            "Это транскрипт телефонного звонка в контакт-центр доставки осетинских пирогов. "
            "Разговаривают ДВА человека: оператор (operator) и клиент (client).\n\n"
            "КЛЮЧЕВЫЕ ПРИЗНАКИ ОПЕРАТОРА (высокая уверенность):\n"
            "- Строка 1 почти всегда оператор: приветствует, называет компанию (\"Пироги №1\", \"Компания Пироги\", \"Пироги\")\n"
            "- Называет своё имя: \"Меня зовут\", \"Это Александра\", \"Анастасия слушает\"\n"
            "- Задаёт уточняющие вопросы: \"Уточните адрес\", \"На какое время\", \"Как вас зовут\", \"Будете оплачивать\"\n"
            "- Подтверждает заказ: \"Записала\", \"Поставила\", \"Итого\", \"Сумма заказа\"\n"
            "- Предлагает доп. товары: \"Могу предложить\", \"Также берут\", \"У нас есть\"\n"
            "- Произносит точные суммы, время доставки, адрес целиком\n\n"
            "КЛЮЧЕВЫЕ ПРИЗНАКИ КЛИЕНТА:\n"
            "- Называет адрес, имя, количество человек\n"
            "- Короткие ответы: \"Да\", \"Нет\", \"Ага\", \"Хорошо\", \"Спасибо\"\n"
            "- Задаёт вопросы о цене, составе, времени доставки\n"
            "- После вопроса оператора — следующая реплика обычно клиент\n\n"
            "ВАЖНО: используй контекст разговора. Смотри на чередование реплик — обычно оператор и клиент чередуются. "
            "Если не уверен — смотри на предыдущую и следующую строки.\n\n"
            "Для КАЖДОЙ строки укажи роль: operator или client.\n"
            "Верни ТОЛЬКО JSON-массив строк, например: "
            '[\"operator\",\"client\",\"operator\",...]\n'
            f"Строк ровно {len(phrases)}. Никакого текста кроме JSON.\n\n"
            f"{numbered_lines}"
        )

        try:
            from openai import OpenAI
            client = OpenAI(api_key=settings.openai_api_key)
            response = client.chat.completions.create(
                model=settings.llm_model,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
                timeout=30,
            )
            raw = (response.choices[0].message.content or "").strip()
            # Убираем markdown fence если есть
            if raw.startswith("```"):
                raw = "\n".join(
                    l for l in raw.splitlines() if not l.strip().startswith("```")
                ).strip()
            roles = json.loads(raw)
        except Exception as exc:
            logger.warning("GPT-4o diarization failed: %s — falling back to single speaker", exc)
            return self._fallback_single_speaker(path, word_timestamps, [
                f"LLM-диаризация не удалась: {exc}. Весь текст помечен как оператор."
            ])

        # Шаг 3: применяем роли к фразам
        transcript_segments: list[TranscriptSegment] = []
        for i, phrase in enumerate(phrases):
            role = "operator"
            if i < len(roles) and roles[i] in ("operator", "client"):
                role = roles[i]
            transcript_segments.append(
                TranscriptSegment(
                    speaker=role,
                    start=phrase["start"],
                    end=phrase["end"],
                    text=phrase["text"],
                )
            )

        # Формируем DiarizationSegment (для БД)
        diarization_segments = [
            DiarizationSegment(speaker=seg.speaker, start=seg.start, end=seg.end)
            for seg in transcript_segments
        ]

        logger.info(
            "GPT-4o mono diarization: %d phrases, %d speakers",
            len(phrases),
            len({s.speaker for s in transcript_segments}),
        )

        return DiarizationResult(
            segments=diarization_segments,
            transcript_segments=transcript_segments,
            method="llm_diarization",
            confidence=85.0,
            num_speakers=2,
            warnings=["Разметка ролей выполнена GPT-4o (моно-запись)."],
        )

    def _load_pipeline(self) -> None:
        """Lazy-load pyannote pipeline (GPU if available)."""
        if self._pipeline is not None:
            return
        import torch
        from pyannote.audio import Pipeline

        logger.info("Loading pyannote/speaker-diarization-3.1 …")
        try:
            # pyannote 4.x
            self._pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                token=settings.hf_token,
            )
        except TypeError:
            # pyannote 3.x fallback
            self._pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                use_auth_token=settings.hf_token,
            )
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self._pipeline.to(torch.device(device))
        logger.info("pyannote pipeline loaded on %s", device)

    @staticmethod
    def _build_speaker_map(
        raw_segments: list[tuple[float, float, str]]
    ) -> dict[str, str]:
        """Map pyannote speaker labels to 'operator'/'client'.

        First voice heard = operator. Second = client. Rest = unknown.
        """
        seen: list[str] = []
        for _, _, lbl in sorted(raw_segments, key=lambda x: x[0]):
            if lbl not in seen:
                seen.append(lbl)
        mapping: dict[str, str] = {}
        roles = ["operator", "client"]
        for i, lbl in enumerate(seen):
            mapping[lbl] = roles[i] if i < len(roles) else "unknown"
        return mapping

    @staticmethod
    def _estimate_confidence(
        raw_segments: list[tuple[float, float, str]]
    ) -> float:
        """Estimate confidence score (0-100) based on segment characteristics.

        Heuristic: ratio of non-overlapping time / total time.
        Short segments and many overlaps → lower confidence.
        """
        if not raw_segments:
            return 0.0

        durations = [e - s for s, e, _ in raw_segments]
        total = sum(durations)
        if total == 0:
            return 0.0

        # Penalise very short segments (< 0.5 sec) — sign of low confidence
        short = sum(1 for d in durations if d < 0.5)
        short_penalty = short / max(len(durations), 1) * 30  # up to 30% penalty

        base = 90.0 - short_penalty
        return max(0.0, min(100.0, base))

    # ------------------------------------------------------------------
    # Merge: words → speaker segments
    # ------------------------------------------------------------------

    def _merge_words_with_diarization(
        self,
        word_timestamps: list[dict[str, Any]],
        diarization_segments: list[DiarizationSegment],
    ) -> list[TranscriptSegment]:
        """Assign each Whisper word to a speaker using diarization segments.

        Strategy: find the diarization segment with maximum overlap with the word.
        If no overlap → assign 'unknown'.
        """
        result: list[TranscriptSegment] = []
        for w in word_timestamps:
            w_start = float(w["start"])
            w_end   = float(w["end"])
            speaker = self._find_speaker(w_start, w_end, diarization_segments)
            result.append(
                TranscriptSegment(
                    speaker=speaker,
                    start=w_start,
                    end=w_end,
                    text=w["word"],
                )
            )
        return self._merge_adjacent_segments(result)

    @staticmethod
    def _find_speaker(
        word_start: float,
        word_end: float,
        segments: list[DiarizationSegment],
    ) -> str:
        """Find speaker with maximum overlap with word window."""
        best_speaker = "unknown"
        best_overlap = 0.0
        for seg in segments:
            overlap = max(0.0, min(word_end, seg.end) - max(word_start, seg.start))
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = seg.speaker
        return best_speaker

    @staticmethod
    def _merge_adjacent_segments(
        words: list[TranscriptSegment],
    ) -> list[TranscriptSegment]:
        """Merge consecutive word-segments with the same speaker into utterances."""
        if not words:
            return []
        merged: list[TranscriptSegment] = []
        current = TranscriptSegment(
            speaker=words[0].speaker,
            start=words[0].start,
            end=words[0].end,
            text=words[0].text,
        )
        for w in words[1:]:
            if w.speaker == current.speaker:
                current.end = w.end
                current.text = current.text + " " + w.text
            else:
                merged.append(current)
                current = TranscriptSegment(
                    speaker=w.speaker,
                    start=w.start,
                    end=w.end,
                    text=w.text,
                )
        merged.append(current)
        return merged

    # ------------------------------------------------------------------
    # Phrase grouping
    # ------------------------------------------------------------------

    def _group_into_phrases(
        self, segments: list[TranscriptSegment]
    ) -> list[TranscriptSegment]:
        """Группирует мелкие сегменты в фразы по паузам.

        Сегменты одного спикера с паузой <PHRASE_GAP_SEC склеиваются.
        При смене спикера — всегда новая фраза.
        """
        if not segments:
            return []

        result: list[TranscriptSegment] = []
        current = TranscriptSegment(
            speaker=segments[0].speaker,
            start=segments[0].start,
            end=segments[0].end,
            text=segments[0].text,
        )

        for seg in segments[1:]:
            gap = seg.start - current.end
            if seg.speaker == current.speaker and gap < self.PHRASE_GAP_SEC:
                # Тот же спикер, маленькая пауза — склеиваем
                current.end = seg.end
                current.text = current.text + " " + seg.text
            else:
                result.append(current)
                current = TranscriptSegment(
                    speaker=seg.speaker,
                    start=seg.start,
                    end=seg.end,
                    text=seg.text,
                )
        result.append(current)
        return result

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _get_channel_count(path: Path) -> int:
        """Get channel count via ffprobe."""
        import subprocess, json
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", str(path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            data = json.loads(result.stdout)
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "audio":
                    return int(stream.get("channels", 1))
        except Exception as exc:
            logger.warning("Could not detect channels for %s: %s", path.name, exc)
        return 1  # safe fallback

    @staticmethod
    def _load_as_16k_mono(path: Path) -> np.ndarray:
        import subprocess
        cmd = [
            "ffmpeg", "-i", str(path),
            "-ar", str(SAMPLE_RATE), "-ac", "1",
            "-f", "f32le", "-loglevel", "quiet", "pipe:1",
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg mono decode failed: {result.stderr.decode(errors='replace')[:200]}")
        return np.frombuffer(result.stdout, dtype=np.float32)

    @staticmethod
    def _fallback_single_speaker(
        path: Path,
        word_timestamps: list[dict[str, Any]],
        warnings: list[str],
    ) -> DiarizationResult:
        """All words → operator when diarization is unavailable."""
        import subprocess, json

        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
               "-show_format", str(path)]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            duration = float(json.loads(res.stdout)["format"]["duration"])
        except Exception:
            duration = 0.0

        segments = [DiarizationSegment(speaker="operator", start=0.0, end=duration)]
        transcript_segments = [
            TranscriptSegment(
                speaker="operator",
                start=float(w["start"]),
                end=float(w["end"]),
                text=w["word"],
            )
            for w in word_timestamps
        ]
        merged = DiarizationService._merge_adjacent_segments(transcript_segments)
        return DiarizationResult(
            segments=segments,
            transcript_segments=merged,
            method="fallback",
            confidence=None,
            num_speakers=1,
            warnings=warnings,
        )
