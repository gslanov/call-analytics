"""PipelineOrchestrator — audio processing pipeline with checkpoints.

Checkpoints (files.stage):
  0 — uploaded (file on disk, record in DB)
  1 — transcribed (Whisper → saved to transcriptions table)
  2 — diarized (pyannote/channel-split → saved to diarizations table)
  3 — analyzed (GPT-4 → saved to analyses table)
  4 — done (all complete)

Checkpoint recovery: if stage >= N, skip stage N (resume from last checkpoint).
`stage` is bumped to N only AFTER stage N's result is persisted — never at the
start of a stage. This makes the checkpoint safe to resume after a mid-stage
server restart (see PipelineOrchestrator.process_file).
Graceful degradation:
  LLM fails  → status=done, analysis=None (shows transcript+diarization)
  Diarize fails → status=failed
  Whisper fails → status=failed
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import File, Transcription, Diarization, Analysis

logger = logging.getLogger(__name__)

# Progress milestones after each stage completes
STAGE_PROGRESS = {
    0: 0,
    1: 40,    # transcription done
    2: 70,    # diarization done
    3: 90,    # analysis done
    4: 100,   # all done
}

STAGE_STATUS = {
    1: "transcribing",
    2: "diarizing",
    3: "analyzing",
}


class PipelineOrchestrator:
    """Processes a single file through all pipeline stages."""

    def __init__(self, db: Session) -> None:
        self.db = db

    async def process_file(self, file_id: uuid.UUID) -> None:
        """Run the full pipeline for a file, resuming from last checkpoint."""
        # SELECT FOR UPDATE SKIP LOCKED — если другой воркер уже обрабатывает,
        # строка залочена и запрос вернёт None вместо блокировки
        db_file = self.db.scalar(
            sa_select(File)
            .where(File.id == file_id)
            .with_for_update(skip_locked=True)
        )
        if db_file is None:
            logger.warning("Pipeline: file %s not found or locked by another worker — skipping", file_id)
            return

        # Защита: если статус уже не подходит для обработки — пропускаем
        if db_file.status not in ("queued", "transcribing", "diarizing", "analyzing"):
            logger.info("Pipeline: file %s has status '%s' — skipping", file_id, db_file.status)
            return

        logger.info(
            "Pipeline: starting file %s (stage=%d, status=%s)",
            file_id, db_file.stage, db_file.status,
        )

        # --- Основной путь: audio_dialog (один вызов, аудио → диалог с ролями) ---
        # Закрывает СРАЗУ оба чекпоинта (stage 1 и 2): один запрос отдаёт и
        # текст, и роли, и таймкоды, так что делить его на два этапа нечего.
        # Если не смог — stage остаётся прежним, и ниже штатно отрабатывает
        # исторический путь OpenAI (gpt-4o-transcribe ×3 + whisper-1 + мерж).
        from app.config import settings as _settings
        if _settings.transcription_mode == "audio_dialog" and db_file.stage < 2:
            await self._try_audio_dialog(db_file)

        # --- Stage 1: Transcription ---
        # stage подтверждаем ТОЛЬКО после _save_transcription — иначе рестарт
        # сервера посреди транскрибации оставляет stage=1 без записи в БД,
        # и при резюме checkpoint считается "потерянным" (файл падает в failed).
        transcription_result = None
        if db_file.stage < 1:
            self._set_status(db_file, "transcribing", stage=db_file.stage, progress=5)
            try:
                transcription_result = await self._run_transcription(db_file)
                self._save_transcription(db_file, transcription_result)
                self._set_status(db_file, "transcribing", stage=1, progress=STAGE_PROGRESS[1])
            except Exception as exc:
                self._fail(db_file, f"Транскрибация: {exc}")
                logger.error("Stage 1 failed for %s: %s", file_id, exc, exc_info=True)
                return
        else:
            # Load from DB checkpoint
            transcription_result = self._load_transcription(db_file)
            if transcription_result is None:
                self._fail(db_file, "Checkpoint потерян: транскрипция отсутствует в БД. Перезапустите обработку.")
                logger.error("Stage 1 checkpoint missing for %s", file_id)
                return
            logger.info("Stage 1 skipped (checkpoint): %s", file_id)

        # --- Stage 2: Diarization ---
        # Тот же принцип: stage=2 подтверждаем после _save_diarization.
        diarization_result = None
        if db_file.stage < 2:
            self._set_status(db_file, "diarizing", stage=db_file.stage, progress=STAGE_PROGRESS[1] + 5)
            try:
                word_timestamps = transcription_result.word_timestamps if transcription_result else []
                full_text = transcription_result.full_text if transcription_result else ""
                diarization_result = await self._run_diarization(db_file, word_timestamps, full_text)
                self._save_diarization(db_file, diarization_result)
                self._set_status(db_file, "diarizing", stage=2, progress=STAGE_PROGRESS[2])
            except Exception as exc:
                self._fail(db_file, f"Диаризация: {exc}")
                logger.error("Stage 2 failed for %s: %s", file_id, exc, exc_info=True)
                return
        else:
            diarization_result = self._load_diarization(db_file)
            if diarization_result is None:
                self._fail(db_file, "Checkpoint потерян: диаризация отсутствует в БД. Перезапустите обработку.")
                logger.error("Stage 2 checkpoint missing for %s", file_id)
                return
            logger.info("Stage 2 skipped (checkpoint): %s", file_id)

        # --- Stage 2.5: классификация типа звонка ---
        # Если не classical (недозвон/автоответчик/курьер/обрыв) — пропускаем
        # дорогой LLM-анализ. Запись остаётся в БД, но скрывается из API/UI.
        from app.services.call_classifier import classify_call
        diar_segments_dicts = [
            {
                "speaker": s.speaker,
                "start": s.start,
                "end": s.end,
                "text": s.text,
            }
            for s in diarization_result.transcript_segments
        ]
        call_type = classify_call(diar_segments_dicts)
        if db_file.call_type != call_type:
            db_file.call_type = call_type
            self.db.commit()

        if call_type != "classical":
            logger.info(
                "Pipeline %s classified as '%s' — skipping LLM analysis (will be hidden from UI)",
                file_id, call_type,
            )
            self._set_status(db_file, "done", stage=4, progress=STAGE_PROGRESS[4])
            return

        # --- Stage 3: LLM Analysis (non-fatal) ---
        # stage=3 подтверждаем в finally, ПОСЛЕ попытки анализа (успешной,
        # неуспешной или "LLM недоступен") — раньше stage ставился до вызова
        # LLM, и рестарт сервера посреди анализа оставлял stage=3 без записи
        # в analyses; при резюме этап молча пропускался, файл уходил в done
        # без оценки. Дополнительно проверяем наличие записи в analyses (а не
        # только stage), чтобы самовосстановиться на уже испорченных таким
        # образом файлах.
        existing_analysis = self.db.scalar(
            sa_select(Analysis).where(Analysis.file_id == db_file.id)
        )
        if db_file.stage < 3 or existing_analysis is None:
            self._set_status(db_file, "analyzing", stage=db_file.stage, progress=STAGE_PROGRESS[2] + 5)
            try:
                analysis_result = await self._run_analysis(db_file, diarization_result)
                if analysis_result is not None:
                    self._save_analysis(db_file, analysis_result)
                    logger.info(
                        "LLM analysis for %s: overall=%d", file_id, analysis_result.overall
                    )
                else:
                    logger.warning(
                        "LLM unavailable for %s — graceful degradation (no analysis)", file_id
                    )
            except Exception as exc:
                # Non-fatal: log but continue to done. rollback() ПЕРЕД логом:
                # если exc пришёл из сбоя self.db.commit() внутри
                # _save_analysis/_run_analysis, сессия остаётся в "pending
                # rollback" состоянии — без явного rollback commit в finally
                # ниже сам упадёт с PendingRollbackError, замаскировав
                # исходную ошибку и оставив файл висеть в status=analyzing.
                self.db.rollback()
                logger.error(
                    "Stage 3 (LLM) failed for %s: %s — continuing without analysis",
                    file_id, exc,
                )
            finally:
                self._set_status(db_file, "analyzing", stage=3, progress=STAGE_PROGRESS[2] + 5)
        else:
            logger.info("Stage 3 skipped (checkpoint + analysis exists): %s", file_id)

        # --- Stage 4: Done ---
        self._set_status(db_file, "done", stage=4, progress=STAGE_PROGRESS[4])
        logger.info("Pipeline complete for %s", file_id)

    # ------------------------------------------------------------------
    # Stage runners
    # ------------------------------------------------------------------

    async def _try_audio_dialog(self, db_file: File) -> bool:
        """Один вызов мультимодальной модели вместо этапов 1 и 2.

        Пишет обе таблицы (transcriptions + diarizations) и выставляет stage=2,
        поэтому дальше оба этапа штатно пропускаются по чекпоинту.

        Returns:
            True — получилось, этапы 1-2 закрыты.
            False — не получилось. stage не тронут, вызывающий код продолжает
            историческим путём (если audio_dialog_fallback_to_openai=True),
            иначе файл падает в failed.
        """
        import asyncio
        from app.config import settings
        from app.services.audio_dialog_service import AudioDialogService
        from app.services.diarization import (
            DiarizationResult, DiarizationSegment, TranscriptSegment,
        )

        if not db_file.audio_path:
            raise ValueError("audio_path is None — file not on disk?")

        svc = AudioDialogService.get_instance()
        if not svc.is_configured():
            logger.warning(
                "audio_dialog включён, но нет ни одного канала с ключом — "
                "работаем историческим путём"
            )
            return False

        self._set_status(db_file, "transcribing", stage=db_file.stage, progress=5)
        names = self._operator_name_hints()

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: svc.transcribe_dialog(
                    db_file.audio_path,
                    duration_sec=db_file.duration_sec,
                    operator_names=names,
                ),
            )
        except Exception as exc:
            # rollback: транзакция могла остаться грязной после _set_status
            self.db.rollback()
            if settings.audio_dialog_fallback_to_openai:
                logger.warning(
                    "audio_dialog не смог для %s (%s) — откатываюсь на "
                    "исторический путь OpenAI", db_file.id, exc,
                )
                return False
            self._fail(db_file, f"Транскрибация (audio_dialog): {exc}")
            logger.error("audio_dialog failed for %s: %s", db_file.id, exc, exc_info=True)
            raise

        # Сверка с независимой расшифровкой: Gemini даёт роли и время, но на
        # записях без речи умеет сочинить разговор (инцидент 19.08).
        # gpt-4o-transcribe ролей не даёт, зато не выдумывает — один проход
        # по файлу, и сверка вычищает то, чего в записи не было.
        # Не смогли получить эталон (нет баланса OpenAI, сбой) — не падаем,
        # работаем на одном Gemini, как раньше.
        if settings.audio_dialog_crosscheck:
            reference = await self._get_reference_text(db_file)
            if reference is not None:
                result = await loop.run_in_executor(
                    None,
                    lambda: svc.crosscheck(
                        result, reference, duration_sec=db_file.duration_sec
                    ),
                )
            else:
                logger.warning(
                    "Сверка пропущена для %s — эталонная расшифровка недоступна",
                    db_file.id,
                )

        # Этап 1: транскрипция. word_timestamps пустые — модель отдаёт метки на
        # уровне реплик, а не слов; ниже они лежат в сегментах диаризации.
        from app.services.whisper_service import TranscriptionResult
        self._save_transcription(
            db_file, TranscriptionResult(full_text=result.full_text, word_timestamps=[])
        )
        self._set_status(db_file, "transcribing", stage=1, progress=STAGE_PROGRESS[1])

        # Этап 2: диаризация из тех же сегментов, без единого доп. запроса.
        # method="audio_dialog" важен: по нему этап 3 НЕ запускает тройной мерж
        # (текст уже размечен и с пунктуацией), а идёт обычной веткой с
        # IVR-фильтром.
        transcript_segments = [
            TranscriptSegment(
                speaker=s.speaker, start=s.start, end=s.end, text=s.text,
            )
            for s in result.segments
        ]
        speakers = {s.speaker for s in result.segments if s.speaker != "unknown"}
        diar = DiarizationResult(
            segments=[
                DiarizationSegment(speaker=s.speaker, start=s.start, end=s.end)
                for s in result.segments
            ],
            transcript_segments=transcript_segments,
            method="audio_dialog",
            confidence=None,
            num_speakers=len(speakers) or 1,
            warnings=[f"audio_dialog via {result.channel}/{result.model_used}"],
        )
        self._save_diarization(db_file, diar)
        self._set_status(db_file, "diarizing", stage=2, progress=STAGE_PROGRESS[2])

        logger.info(
            "audio_dialog: %s обработан за один вызов (%s/%s, %d реплик)",
            db_file.id, result.channel, result.model_used, len(transcript_segments),
        )
        return True

    async def _get_reference_text(self, db_file: File) -> str | None:
        """Дословная расшифровка от gpt-4o-transcribe — эталон для сверки.

        ОДИН проход по файлу (исторический путь делал четыре). Ролей и
        таймкодов не даёт, но не выдумывает — именно это и нужно, чтобы
        поймать сочинённые реплики.

        Returns:
            Текст, либо None если получить не удалось (пустой баланс OpenAI,
            сбой сети). None означает «сверку пропускаем», а не «звонок плохой».
        """
        import asyncio
        from app.services.whisper_service import WhisperService

        whisper = WhisperService.get_instance()
        if whisper._get_client() is None:
            return None
        try:
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(
                None, whisper.transcribe, db_file.audio_path
            )
            return (res.full_text or "").strip()
        except Exception as exc:
            logger.warning(
                "Эталонная расшифровка для %s недоступна: %s", db_file.id, exc
            )
            return None

    def _operator_name_hints(self) -> list[str]:
        """Имена и фамилии операторов из БД — подсказка модели.

        Раньше список был зашит в промпт и успел устареть: 18.08 в нём не было
        Игнатовой Лады, и модель услышала «Влада» вместо «Лада». Тянем из
        таблицы, служебные записи отбрасываем.
        """
        from app.models import Operator

        _SKIP = {
            "менеджер", "неизвестный", "оператор",
            "postdeploy_smoke", "postdeploy", "smoke", "test",
        }
        try:
            rows = self.db.scalars(sa_select(Operator.name)).all()
        except Exception as exc:
            logger.warning("Не смог прочитать operators для подсказки: %s", exc)
            return []

        out: list[str] = []
        for full in rows:
            for token in (full or "").replace("_", " ").split():
                t = token.strip()
                if len(t) < 3 or t.lower() in _SKIP or not t[0].isalpha():
                    continue
                if t not in out:
                    out.append(t)
        return out[:40]

    async def _run_transcription(self, db_file: File) -> Any:
        """Run Whisper transcription (sync, runs in thread)."""
        import asyncio
        from app.services.whisper_service import WhisperService

        if not db_file.audio_path:
            raise ValueError("audio_path is None — file not on disk?")

        whisper = WhisperService.get_instance()
        loop = asyncio.get_running_loop()
        # Run blocking call in thread pool to not block event loop
        result = await loop.run_in_executor(
            None, whisper.transcribe, db_file.audio_path
        )
        return result

    async def _run_diarization(self, db_file: File, word_timestamps: list[dict], full_text: str = "") -> Any:
        import asyncio
        from app.services.diarization import DiarizationService

        if not db_file.audio_path:
            raise ValueError("audio_path is None")

        diarizer = DiarizationService.get_instance()
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, diarizer.diarize, db_file.audio_path, word_timestamps, full_text
        )
        return result

    async def _run_analysis(self, db_file: File, diarization_result: Any) -> Any:
        import asyncio
        from app.services.llm_service import LLMService

        llm = LLMService.get_instance()

        operator_text = ""
        client_text = ""

        # Triple merge for stereo files with channel_transcription method:
        # 1. gpt-4o-transcribe (DB) = accurate text, no speakers/timestamps
        # 2. whisper-1 (fresh) = timestamps + channel energy speakers, less accurate text
        # 3. GPT-5.4 merges both into best version
        use_triple_merge = (
            diarization_result is not None
            and getattr(diarization_result, "method", "") == "channel_transcription"
        )
        if use_triple_merge:
            merged = await self._run_triple_merge(db_file)
            if merged:
                operator_text = merged
                client_text = ""
                # Update diarization segments with punctuated text from merge
                self._update_segments_from_merge(db_file, merged)
                logger.info("Triple merge complete for %s", db_file.id)
            else:
                # Fallback: use mixed text without speakers
                tr = self.db.scalar(
                    sa_select(Transcription).where(Transcription.file_id == db_file.id)
                )
                if tr and tr.full_text:
                    operator_text = tr.full_text
                logger.warning("Triple merge failed, using mixed text fallback")
        elif diarization_result is not None:
            # Include timestamps so GPT can reference specific moments
            def _fmt(sec: float) -> str:
                m, s = divmod(int(sec), 60)
                return f"{m}:{s:02d}"

            # Filter out IVR text from segments
            # IVR phrases appear at the start: "нажмите цифру", "дождитесь ответа" etc.
            # They can be in a separate segment or mixed with live operator intro.
            # Strategy: find the last IVR marker, cut everything before it.
            import re
            _IVR_MARKERS = [
                "нажмите цифру",
                "дождитесь ответа оператора",
                "контроля качества",
                "все разговоры записываются",
                "для оформления нового заказа",
                "для вопросов связанных",
                "пожалуйста ответа оператора",
            ]

            live_segments = []
            for seg in diarization_result.transcript_segments:
                text = seg.text
                if seg.speaker == "operator":
                    # Check if segment contains IVR text
                    text_lower = text.lower()
                    last_ivr_end = -1
                    for marker in _IVR_MARKERS:
                        pos = text_lower.rfind(marker)
                        if pos >= 0:
                            # Find end of sentence containing this marker
                            end_pos = pos + len(marker)
                            last_ivr_end = max(last_ivr_end, end_pos)

                    if last_ivr_end > 0:
                        # Cut out IVR prefix, keep the rest
                        remaining = text[last_ivr_end:].strip()
                        logger.info(
                            "IVR stripped from segment [%s]: removed %d chars, kept %d",
                            _fmt(seg.start), last_ivr_end, len(remaining),
                        )
                        if not remaining:
                            continue  # entire segment was IVR
                        # Replace segment text with cleaned version
                        from dataclasses import replace
                        seg = replace(seg, text=remaining)

                live_segments.append(seg)

            operator_text = "\n".join(
                f"[{_fmt(seg.start)}] {seg.text}"
                for seg in live_segments
                if seg.speaker == "operator"
            )
            client_text = "\n".join(
                f"[{_fmt(seg.start)}] {seg.text}"
                for seg in live_segments
                if seg.speaker == "client"
            )
        else:
            # Load transcription text as fallback
            tr = self.db.scalar(
                sa_select(Transcription).where(
                    Transcription.file_id == db_file.id
                )
            )
            if tr:
                operator_text = tr.full_text

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, llm.analyze, operator_text, client_text
        )
        return result

    # ------------------------------------------------------------------
    # Triple merge: whisper-1 + gpt-4o-transcribe → GPT-5.4 merge
    # ------------------------------------------------------------------

    _MERGE_PROMPT = """Ты получаешь ДВЕ транскрибации одного и того же телефонного разговора, сделанные разными моделями.

ТРАНСКРИБАЦИЯ A (whisper-1):
- Есть таймстемпы [M:SS]
- Есть разметка спикеров (ОПЕРАТОР / КЛИЕНТ / НЕЯСНО) — это ПОДСКАЗКА, полученная из энергии каналов аудио. Она НЕ всегда правильная: на коротких репликах и при перекрытии голосов может перевернуться, а «НЕЯСНО» означает, что энергия не позволила определить.
- Текст МЕНЕЕ точный: могут быть ошибки в словах

ТРАНСКРИБАЦИЯ B (gpt-4o-transcribe):
- НЕТ таймстемпов
- НЕТ разметки спикеров
- Текст БОЛЕЕ точный в большинстве случаев, но тоже бывают ошибки

ТВОЯ ЗАДАЧА: Собрать ЛУЧШУЮ ИТОГОВУЮ версию диалога с правильной разметкой ролей.

Правила:
1. ТАЙМСТЕМПЫ — бери из транскрибации A.
2. СПИКЕРЫ — разметка из A это ТОЛЬКО ПОДСКАЗКА. Используй её как стартовую гипотезу, но обязательно проверяй по СОДЕРЖАНИЮ. Если по смыслу реплики очевидно, что роль другая — переопредели её САМ. «НЕЯСНО» — обязательно реши сам, кто говорит.

   Признаки ОПЕРАТОРА:
   - Приветствует, называет компанию: «Добрый день, компания "Пироги №1"», «Это Пироги»
   - Представляется: «Меня зовут …», «Это Анастасия»
   - Звонит для подтверждения: «звоню по поводу вашего заказа», «подтверждаю заказ»
   - Задаёт УТОЧНЯЮЩИЕ вопросы про заказ: «Адрес такой?», «Время доставки …, верно?», «Метро какое?», «Сумма заказа …»
   - Перечисляет состав, проговаривает адрес и сумму
   - Предлагает доп. товары / посуду / напитки
   - В конце прощается: «Хорошего дня», «Курьер позвонит»

   Признаки КЛИЕНТА:
   - Короткие ответы: «Да», «Нет», «Угу», «Хорошо», «Спасибо»
   - Сообщает свои данные (имя, адрес, состав корзины) ОТВЕЧАЯ на вопрос оператора
   - Задаёт вопросы про цену, время, скидки
   - Реакции: «Понятно», «Хорошо», «Можно ещё …»

   Логика чередования: оператор и клиент обычно чередуются. Если три реплики подряд помечены одинаково и по смыслу одна из них явно «ответ» на другую — посмотри, не перепутана ли роль.

3. ТЕКСТ — сравнивай обе версии, выбирай точнее по смыслу. Если оба ошибаются — догадайся по контексту («хатикурри»+«хатифури» = «хачапури», «Пряги номер один» = «Пироги №1»).
4. НЕ добавляй и НЕ удаляй реплики — только исправляй текст и роль.
5. Пунктуация — расставь правильно: запятые, точки, вопросы, тире. Текст должен читаться как грамотный русский.
6. Контекст: колл-центр доставки осетинских пирогов «Пироги №1». Операторы: Галина, Александра, Анна, Анастасия.

ДОМЕННЫЕ ТЕРМИНЫ (правь ТОЛЬКО если в обеих транскрибациях слово явно искажено по звучанию и из контекста очевидно, что имелся в виду один из этих терминов):
- Бренд: «Пироги №1», «Пироги номер один»
- Блюда: осетинские пироги, хачапури, сулугуни, облепиха, чак-чак
- Способы получения: доставка, курьер, самовывоз
- Имена операторов (только если оператор сам себя называет): Галина, Александра, Анна, Анастасия

ВАЖНО: НЕ вставляй термины «на всякий случай», если их нет в обеих транскрибациях. Не додумывай реплики.

Верни ТОЛЬКО строки в формате:
[M:SS] ОПЕРАТОР: текст
[M:SS] КЛИЕНТ: текст

Без JSON, без пояснений — только диалог."""

    async def _run_triple_merge(self, db_file: File) -> str | None:
        """Run triple merge: whisper-1 timestamps+speakers + gpt-4o text → GPT merge."""
        import asyncio
        import numpy as np
        import soundfile as sf

        # 1. Get gpt-4o-transcribe text from DB
        tr = self.db.scalar(
            sa_select(Transcription).where(Transcription.file_id == db_file.id)
        )
        if not tr or not tr.full_text:
            return None
        gpt4o_text = tr.full_text

        # 2. Run whisper-1 for timestamps + segments
        from app.services.whisper_service import WhisperService, DOMAIN_PROMPT
        whisper = WhisperService.get_instance()
        client = whisper._get_client()
        if client is None:
            return None

        audio_path = db_file.audio_path
        logger.info("Triple merge: running whisper-1 on %s", audio_path)

        def _run_whisper_1():
            with open(audio_path, "rb") as f:
                kwargs: dict[str, Any] = {
                    "model": "whisper-1",
                    "file": f,
                    "language": "ru",
                    "response_format": "verbose_json",
                    "timestamp_granularities": ["word", "segment"],
                }
                if DOMAIN_PROMPT:
                    kwargs["prompt"] = DOMAIN_PROMPT
                return client.audio.transcriptions.create(**kwargs)

        loop = asyncio.get_running_loop()
        whisper_response = await loop.run_in_executor(None, _run_whisper_1)
        whisper_segments = whisper_response.segments if hasattr(whisper_response, 'segments') else []

        if not whisper_segments:
            logger.warning("Triple merge: whisper-1 returned no segments")
            return None

        # 3. Assign speakers by channel energy.
        # Читаем фрагментами по таймстемпам (а не весь файл целиком) — на 1ГБ звонке
        # full-load = 600MB float32 в RAM, фрагментный — единицы MB.
        try:
            snd = sf.SoundFile(audio_path)
        except Exception as exc:
            logger.error("Triple merge: cannot open audio: %s", exc)
            return None

        try:
            if snd.channels != 2:
                logger.info("Triple merge: not stereo, skipping speaker assignment")
                return None
            sr = snd.samplerate
            n_frames = snd.frames

            labeled_lines = []
            for seg in whisper_segments:
                start = seg.start if hasattr(seg, 'start') else seg.get("start", 0)
                end = seg.end if hasattr(seg, 'end') else seg.get("end", 0)
                text = (seg.text if hasattr(seg, 'text') else seg.get("text", "")).strip()

                s_start = max(0, int(start * sr))
                s_end = min(n_frames, int(end * sr))
                if s_end > s_start:
                    snd.seek(s_start)
                    chunk = snd.read(frames=s_end - s_start, dtype='float32', always_2d=True)
                    l_energy = float(np.sqrt(np.mean(chunk[:, 0] ** 2)))
                    r_energy = float(np.sqrt(np.mean(chunk[:, 1] ** 2)))
                else:
                    l_energy = r_energy = 0.0

                if l_energy > r_energy * 1.3:
                    speaker = "ОПЕРАТОР"
                elif r_energy > l_energy * 1.3:
                    speaker = "КЛИЕНТ"
                else:
                    speaker = "НЕЯСНО"

                m, s = divmod(int(start), 60)
                labeled_lines.append(f"[{m}:{s:02d}] {speaker}: {text}")
        finally:
            snd.close()

        whisper_labeled = "\n".join(labeled_lines)
        logger.info("Triple merge: %d whisper segments with speakers", len(labeled_lines))

        # 4. GPT-5.4 merge
        user_msg = (
            f"=== ТРАНСКРИБАЦИЯ A (whisper-1, с таймстемпами и спикерами) ===\n{whisper_labeled}\n\n"
            f"=== ТРАНСКРИБАЦИЯ B (gpt-4o-transcribe, точный текст без разметки) ===\n{gpt4o_text}"
        )

        from app.services.llm_service import LLMService
        llm_service = LLMService.get_instance()
        if llm_service._get_client() is None:
            return None

        def _run_merge():
            text, model_used = llm_service.chat_completion(
                messages=[
                    {"role": "system", "content": self._MERGE_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                timeout=300,
            )
            logger.info("Triple merge served by model=%s", model_used)
            return text

        merged_text = await loop.run_in_executor(None, _run_merge)
        merged_text = merged_text.strip()

        # Clean markdown if any
        if merged_text.startswith("```"):
            lines = merged_text.splitlines()
            merged_text = "\n".join(l for l in lines if not l.strip().startswith("```")).strip()

        logger.info("Triple merge result: %d chars, %d lines", len(merged_text), merged_text.count("\n") + 1)
        return merged_text

    # ------------------------------------------------------------------
    # DB persistence helpers
    # ------------------------------------------------------------------

    def _save_transcription(self, db_file: File, result: Any) -> None:
        # Remove existing (idempotent on retry)
        existing = self.db.scalar(
            sa_select(Transcription).where(
                Transcription.file_id == db_file.id
            )
        )
        if existing:
            self.db.delete(existing)
            self.db.flush()

        from app.utils import mask_phone_numbers
        tr = Transcription(
            file_id=db_file.id,
            full_text=mask_phone_numbers(result.full_text),
            word_timestamps=result.word_timestamps,
            language="ru",
        )
        self.db.add(tr)
        self.db.commit()
        logger.debug("Saved transcription for %s (%d words)", db_file.id, len(result.word_timestamps))

    def _update_segments_from_merge(self, db_file: File, merged_text: str) -> None:
        """Parse triple-merge output and update diarization segments with punctuated text.

        Merged format: [M:SS] ОПЕРАТОР: text  /  [M:SS] КЛИЕНТ: text
        """
        import re
        from app.utils import mask_phone_numbers

        diar = self.db.scalar(
            sa_select(Diarization).where(Diarization.file_id == db_file.id)
        )
        if not diar:
            return

        # Parse merged lines: [0:05] ОПЕРАТОР: Добрый день, компания...
        pattern = re.compile(r"\[(\d+):(\d{2})\]\s*(ОПЕРАТОР|КЛИЕНТ|НЕЯСНО):\s*(.+)")
        new_segments = []
        for line in merged_text.strip().splitlines():
            m = pattern.match(line.strip())
            if not m:
                continue
            minutes, seconds, speaker_label, text = m.groups()
            start = int(minutes) * 60 + int(seconds)
            speaker = "operator" if speaker_label == "ОПЕРАТОР" else "client"
            new_segments.append({
                "speaker": speaker,
                "start": float(start),
                "end": 0.0,  # will be filled below
                "text": mask_phone_numbers(text.strip()),
            })

        if not new_segments:
            logger.warning("Could not parse merged text into segments for %s", db_file.id)
            return

        # Fill end times: each segment ends when the next one starts
        for i in range(len(new_segments) - 1):
            new_segments[i]["end"] = new_segments[i + 1]["start"]
        # Last segment: use last original segment's end, or start + 10
        orig_segments = diar.segments or []
        if orig_segments:
            new_segments[-1]["end"] = orig_segments[-1].get("end", new_segments[-1]["start"] + 10)
        else:
            new_segments[-1]["end"] = new_segments[-1]["start"] + 10

        diar.segments = new_segments
        self.db.commit()
        logger.info("Updated diarization segments with punctuated merge text for %s (%d segments)",
                     db_file.id, len(new_segments))

    def _save_diarization(self, db_file: File, result: Any) -> None:
        existing = self.db.scalar(
            sa_select(Diarization).where(
                Diarization.file_id == db_file.id
            )
        )
        if existing:
            self.db.delete(existing)
            self.db.flush()

        from app.utils import mask_phone_numbers
        segments_json = [
            {
                "speaker": seg.speaker,
                "start": seg.start,
                "end": seg.end,
                "text": mask_phone_numbers(seg.text),
            }
            for seg in result.transcript_segments
        ]
        diar = Diarization(
            file_id=db_file.id,
            segments=segments_json,
            method=result.method,
            confidence=result.confidence,
            num_speakers=result.num_speakers,
        )
        self.db.add(diar)
        self.db.commit()
        logger.debug("Saved diarization for %s (%d segments)", db_file.id, len(segments_json))

    def _save_analysis(self, db_file: File, result: Any) -> None:
        existing = self.db.scalar(
            sa_select(Analysis).where(
                Analysis.file_id == db_file.id
            )
        )
        if existing:
            self.db.delete(existing)
            self.db.flush()

        analysis = Analysis(
            file_id=db_file.id,
            standard=result.standard,
            loyalty=result.loyalty,
            kindness=result.kindness,
            overall=result.overall,
            summary=result.summary,
            quotes=result.quotes,
            criteria_details=result.details,
            llm_model=result.llm_model,
        )
        self.db.add(analysis)
        self.db.commit()

    # ------------------------------------------------------------------
    # DB checkpoint loaders
    # ------------------------------------------------------------------

    def _load_transcription(self, db_file: File) -> Any | None:
        from sqlalchemy import select as sa_select
        from app.services.whisper_service import TranscriptionResult

        tr = self.db.scalar(sa_select(Transcription).where(Transcription.file_id == db_file.id))
        if tr is None:
            logger.warning("Stage 1 checkpoint missing for %s — re-running", db_file.id)
            return None
        return TranscriptionResult(
            full_text=tr.full_text,
            word_timestamps=tr.word_timestamps or [],
        )

    def _load_diarization(self, db_file: File) -> Any | None:
        from sqlalchemy import select as sa_select
        from app.services.diarization import DiarizationResult, DiarizationSegment, TranscriptSegment

        diar = self.db.scalar(sa_select(Diarization).where(Diarization.file_id == db_file.id))
        if diar is None:
            logger.warning("Stage 2 checkpoint missing for %s — will re-run diarization", db_file.id)
            return None

        transcript_segments = [
            TranscriptSegment(
                speaker=seg["speaker"],
                start=seg["start"],
                end=seg["end"],
                text=seg["text"],
            )
            for seg in (diar.segments or [])
        ]
        return DiarizationResult(
            segments=[DiarizationSegment(seg["speaker"], seg["start"], seg["end"])
                      for seg in (diar.segments or [])],
            transcript_segments=transcript_segments,
            method=diar.method or "unknown",
            confidence=diar.confidence,
            num_speakers=diar.num_speakers or 1,
        )

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def _set_status(
        self,
        db_file: File,
        status: str,
        stage: int,
        progress: int,
    ) -> None:
        db_file.status = status
        db_file.stage = stage
        db_file.progress = progress
        self.db.commit()
        # Fire-and-forget broadcast (non-blocking)
        self._broadcast(str(db_file.id), status, progress, stage)

    def _broadcast(self, file_id: str, status: str, progress: int, stage: int) -> None:
        """Broadcast progress to WebSocket subscribers (non-blocking)."""
        import asyncio
        try:
            from app.routers.ws import ws_manager
            loop = asyncio.get_running_loop()
            loop.create_task(
                ws_manager.broadcast_progress(file_id, status, progress, stage)
            )
        except RuntimeError:
            # No running loop — skip broadcast (e.g. during tests)
            pass
        except Exception as exc:
            logger.debug("WS broadcast skipped: %s", exc)

    def _fail(self, db_file: File, error: str) -> None:
        # Сессия могла остаться "грязной" после сбоя предыдущего commit
        # (например внутри _save_transcription/_save_diarization) — без
        # rollback commit ниже сам упадёт с PendingRollbackError и
        # замаскирует исходную ошибку. rollback() на чистой сессии — no-op.
        self.db.rollback()
        db_file.status = "failed"
        db_file.error_message = error
        db_file.retry_count = (db_file.retry_count or 0) + 1
        self.db.commit()
        self._broadcast(str(db_file.id), "failed", db_file.progress or 0, db_file.stage or 0)
        logger.error("File %s failed: %s", db_file.id, error)
