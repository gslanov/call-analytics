"""A/B тест: triple merge на gpt-5-nano + анализ критериев на gpt-5-mini.

Цель — сравнить качество с прод-моделью (gpt-5.4) на 5 звонках.
Скрипт:
  1. Берёт N последних classical звонков с сохранённым Analysis
  2. Для каждого заново гоняет whisper-1 + triple merge на gpt-5-nano
  3. Анализ по 22 критериям на gpt-5-mini
  4. Сравнивает результат с тем, что уже лежит в БД (gpt-5.4)
  5. Считает стоимость (по числу токенов из usage)
  6. Складывает результат в /tmp/ab_results.json

Запуск (внутри контейнера call-analytics-backend):
    python /tmp/test_model_ab.py --n 5

Скрипт НЕ пишет в БД и не меняет состояние пайплайна.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from contextlib import contextmanager
from typing import Any

from typing import Any

import numpy as np
import soundfile as sf
from sqlalchemy import select as sa_select

from app.config import settings
from app.database import SessionLocal
from app.models import Analysis, Diarization, File, Transcription
from app.services.llm_service import (
    SYSTEM_PROMPT,
    LLMService,
    _compute_group_score,
    _apply_dependencies,
)
from app.services.pipeline import PipelineOrchestrator as Pipeline
from app.services.whisper_service import WhisperService, DOMAIN_PROMPT

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ab")

MERGE_MODEL = "gpt-4o-mini"
ANALYSIS_MODEL = "gpt-5.4"


def _normalize_user_message(merged_text: str) -> str:
    return f"=== Полный диалог (с таймстемпами и спикерами) ===\n{merged_text.strip()}"


async def _run_triple_merge_with_model(
    pipeline: Pipeline,
    db_file: File,
    llm_client: Any,
    model: str,
) -> str | None:
    """Воспроизводит Pipeline._run_triple_merge, но с заданной моделью и без
    temperature (новые gpt-5-nano/mini не поддерживают temperature=0).
    Возвращает merged_text либо None."""
    tr = pipeline.db.scalar(
        sa_select(Transcription).where(Transcription.file_id == db_file.id)
    )
    if not tr or not tr.full_text:
        return None
    gpt4o_text = tr.full_text

    whisper = WhisperService.get_instance()
    whisper_client = whisper._get_client()
    if whisper_client is None:
        return None

    audio_path = db_file.audio_path
    log.info("Triple merge: running whisper-1 on %s", audio_path)

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
            return whisper_client.audio.transcriptions.create(**kwargs)

    loop = asyncio.get_running_loop()
    whisper_response = await loop.run_in_executor(None, _run_whisper_1)
    whisper_segments = getattr(whisper_response, "segments", []) or []
    if not whisper_segments:
        return None

    try:
        snd = sf.SoundFile(audio_path)
    except Exception as exc:
        log.error("Cannot open audio: %s", exc)
        return None

    try:
        if snd.channels != 2:
            log.info("Not stereo — skipping speaker assignment")
            return None
        sr = snd.samplerate
        n_frames = snd.frames

        labeled_lines = []
        for seg in whisper_segments:
            start = seg.start if hasattr(seg, "start") else seg.get("start", 0)
            end = seg.end if hasattr(seg, "end") else seg.get("end", 0)
            text = (seg.text if hasattr(seg, "text") else seg.get("text", "")).strip()

            s_start = max(0, int(start * sr))
            s_end = min(n_frames, int(end * sr))
            if s_end > s_start:
                snd.seek(s_start)
                chunk = snd.read(frames=s_end - s_start, dtype="float32", always_2d=True)
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
    log.info("Triple merge: %d whisper segments with speakers", len(labeled_lines))

    user_msg = (
        f"=== ТРАНСКРИБАЦИЯ A (whisper-1, с таймстемпами и спикерами) ===\n{whisper_labeled}\n\n"
        f"=== ТРАНСКРИБАЦИЯ B (gpt-4o-transcribe, точный текст без разметки) ===\n{gpt4o_text}"
    )

    def _run_merge():
        kwargs: dict[str, Any] = dict(
            model=model,
            messages=[
                {"role": "system", "content": pipeline._MERGE_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            timeout=180,
        )
        # gpt-5-* (reasoning) не поддерживают temperature; для остальных ставим 0
        if not model.startswith("gpt-5"):
            kwargs["temperature"] = 0
        response = llm_client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or "", response.usage

    merged_text, usage = await loop.run_in_executor(None, _run_merge)
    merged_text = merged_text.strip()
    if merged_text.startswith("```"):
        lines_ = merged_text.splitlines()
        merged_text = "\n".join(l for l in lines_ if not l.strip().startswith("```")).strip()

    return merged_text, {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def _analyze_with_model(client: Any, user_message: str, model: str) -> tuple[dict | None, dict]:
    """Analyse via given GPT model. Returns (parsed_json, usage_dict)."""
    t0 = time.time()
    kwargs: dict[str, Any] = dict(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        timeout=180,
    )
    if not model.startswith("gpt-5"):
        kwargs["temperature"] = 0
    response = client.chat.completions.create(**kwargs)
    elapsed = time.time() - t0
    raw = response.choices[0].message.content or ""

    # Strip markdown
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(l for l in lines if not l.strip().startswith("```")).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("Analysis JSON parse error (%s, %s): %s | raw=%r", model, exc, exc, raw[:300])
        parsed = None

    usage = {
        "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
        "completion_tokens": getattr(response.usage, "completion_tokens", None),
        "total_tokens": getattr(response.usage, "total_tokens", None),
        "elapsed_sec": round(elapsed, 2),
    }
    return parsed, usage


def _compute_scores(details: dict) -> dict:
    """Compute group + overall scores from details (mirrors LLMService logic)."""
    if not details:
        return {"standard": 0, "loyalty": 0, "kindness": 0, "overall": 0}

    # Strip nested {value, reason, timestamp} → just bool/null
    flat: dict[str, dict[str, bool | None]] = {}
    for group, items in details.items():
        if group == "markers":
            continue
        flat[group] = {}
        for k, v in items.items():
            if isinstance(v, dict):
                flat[group][k] = v.get("value")
            else:
                flat[group][k] = v

    _apply_dependencies(flat)
    standard = _compute_group_score(flat.get("standard", {}))
    loyalty = _compute_group_score(flat.get("loyalty", {}))
    kindness = _compute_group_score(flat.get("kindness", {}))
    overall = round((standard + loyalty + kindness) / 3)
    return {
        "standard": standard,
        "loyalty": loyalty,
        "kindness": kindness,
        "overall": overall,
    }


def _flatten_details(details: dict) -> dict[str, dict]:
    """Flatten {standard: {introduced_self: {value, reason, ts}, ...}, ...}
    into {group.criterion: {value, reason}, ...} for easy diff."""
    out = {}
    for group, items in (details or {}).items():
        if group == "markers":
            continue
        for k, v in items.items():
            if isinstance(v, dict):
                out[f"{group}.{k}"] = {
                    "value": v.get("value"),
                    "reason": v.get("reason", "")[:120],
                }
            else:
                out[f"{group}.{k}"] = {"value": v, "reason": ""}
    return out


def _diff_details(prod: dict, test: dict) -> list[dict]:
    """Compare flattened details. Return list of differences."""
    diffs = []
    keys = set(prod.keys()) | set(test.keys())
    for k in sorted(keys):
        pv = prod.get(k, {}).get("value", "missing")
        tv = test.get(k, {}).get("value", "missing")
        if pv != tv:
            diffs.append({
                "criterion": k,
                "prod_value": pv,
                "test_value": tv,
                "prod_reason": prod.get(k, {}).get("reason", ""),
                "test_reason": test.get(k, {}).get("reason", ""),
            })
    return diffs


async def process_file(pipeline: Pipeline, db_file: File, prod_analysis: Analysis, llm_client: Any) -> dict:
    log.info("=== File %s (%s, dur=%.1fs) ===", db_file.id, db_file.original_name, db_file.duration_sec or 0)

    result: dict[str, Any] = {
        "file_id": str(db_file.id),
        "original_name": db_file.original_name,
        "duration_sec": db_file.duration_sec,
        "prod": {
            "model": prod_analysis.llm_model,
            "standard": prod_analysis.standard,
            "loyalty": prod_analysis.loyalty,
            "kindness": prod_analysis.kindness,
            "overall": prod_analysis.overall,
            "details_flat": _flatten_details(prod_analysis.criteria_details or {}),
            "summary": prod_analysis.summary,
        },
    }

    # 1. Re-run triple merge with gpt-5-nano
    t0 = time.time()
    try:
        merge_result = await _run_triple_merge_with_model(pipeline, db_file, llm_client, MERGE_MODEL)
    except Exception as exc:
        log.exception("Merge failed for %s", db_file.id)
        result["error"] = f"merge: {exc}"
        return result
    merge_elapsed = time.time() - t0

    if not merge_result:
        result["error"] = "merge returned empty / non-stereo"
        return result

    merged_text, merge_usage = merge_result

    result["merge"] = {
        "model": MERGE_MODEL,
        "elapsed_sec": round(merge_elapsed, 2),
        "lines": merged_text.count("\n") + 1,
        "chars": len(merged_text),
        "preview": merged_text[:500],
        "usage": merge_usage,
    }

    # 2. Analyse with gpt-5-mini
    user_message = _normalize_user_message(merged_text)
    try:
        parsed, usage = _analyze_with_model(llm_client, user_message, ANALYSIS_MODEL)
    except Exception as exc:
        log.exception("Analysis failed for %s", db_file.id)
        result["error"] = f"analysis: {exc}"
        return result

    if parsed is None:
        result["error"] = "analysis returned invalid JSON"
        return result

    test_details = parsed.get("details", {})
    test_scores = _compute_scores(test_details)
    test_flat = _flatten_details(test_details)

    result["test"] = {
        "merge_model": MERGE_MODEL,
        "analysis_model": ANALYSIS_MODEL,
        "standard": test_scores["standard"],
        "loyalty": test_scores["loyalty"],
        "kindness": test_scores["kindness"],
        "overall": test_scores["overall"],
        "details_flat": test_flat,
        "summary": parsed.get("summary", ""),
        "usage": usage,
    }

    # 3. Diff
    result["diff"] = {
        "scores": {
            "standard": test_scores["standard"] - prod_analysis.standard,
            "loyalty": test_scores["loyalty"] - prod_analysis.loyalty,
            "kindness": test_scores["kindness"] - prod_analysis.kindness,
            "overall": test_scores["overall"] - prod_analysis.overall,
        },
        "criteria_changed": _diff_details(result["prod"]["details_flat"], test_flat),
    }

    log.info(
        "  prod: std=%d loy=%d kind=%d ovr=%d | test: std=%d loy=%d kind=%d ovr=%d | diff_criteria=%d",
        prod_analysis.standard, prod_analysis.loyalty, prod_analysis.kindness, prod_analysis.overall,
        test_scores["standard"], test_scores["loyalty"], test_scores["kindness"], test_scores["overall"],
        len(result["diff"]["criteria_changed"]),
    )
    return result


async def main(n: int, output_path: str):
    db = SessionLocal()
    try:
        # Pick N latest classical files with both transcription + analysis
        stmt = (
            sa_select(File)
            .join(Analysis, Analysis.file_id == File.id)
            .join(Transcription, Transcription.file_id == File.id)
            .where(File.call_type == "classical")
            .where(File.status == "done")
            .where(File.audio_path.is_not(None))
            .order_by(File.created_at.desc())
            .limit(n)
        )
        files = list(db.scalars(stmt).all())
        log.info("Picked %d files", len(files))
        if not files:
            log.error("No suitable files found")
            return

        # Load corresponding analyses
        analyses = {
            a.file_id: a
            for a in db.scalars(
                sa_select(Analysis).where(Analysis.file_id.in_([f.id for f in files]))
            ).all()
        }

        pipeline = Pipeline(db)
        llm_client = LLMService.get_instance()._get_client()
        if llm_client is None:
            log.error("OPENAI_API_KEY not set")
            return

        results = []
        for f in files:
            ana = analyses.get(f.id)
            if ana is None:
                log.warning("No analysis for %s — skip", f.id)
                continue
            r = await process_file(pipeline, f, ana, llm_client)
            results.append(r)

        # Aggregate
        total_diffs = sum(len(r.get("diff", {}).get("criteria_changed", [])) for r in results)
        total_calls = len([r for r in results if "test" in r])
        usage_in = sum((r.get("test", {}).get("usage", {}).get("prompt_tokens") or 0) for r in results)
        usage_out = sum((r.get("test", {}).get("usage", {}).get("completion_tokens") or 0) for r in results)

        # Pricing per 1M tokens (input / output)
        PRICING = {
            "gpt-5":        (1.25, 10.00),
            "gpt-5-mini":   (0.25,  2.00),
            "gpt-5-nano":   (0.05,  0.40),
            "gpt-5.4":      (1.25, 10.00),  # alias семейства
            "gpt-4o":       (2.50, 10.00),
            "gpt-4o-mini":  (0.15,  0.60),
            "gpt-4.1":      (2.00,  8.00),
            "gpt-4.1-mini": (0.40,  1.60),
        }
        a_in_price, a_out_price = PRICING.get(ANALYSIS_MODEL, (1.0, 4.0))
        m_in_price, m_out_price = PRICING.get(MERGE_MODEL, (1.0, 4.0))
        cost_analysis = (usage_in / 1_000_000) * a_in_price + (usage_out / 1_000_000) * a_out_price

        merge_in = sum((r.get("merge", {}).get("usage", {}).get("prompt_tokens") or 0) for r in results)
        merge_out = sum((r.get("merge", {}).get("usage", {}).get("completion_tokens") or 0) for r in results)
        cost_merge = (merge_in / 1_000_000) * m_in_price + (merge_out / 1_000_000) * m_out_price

        summary = {
            "tested_calls": total_calls,
            "merge_model": MERGE_MODEL,
            "analysis_model": ANALYSIS_MODEL,
            "total_criteria_diffs": total_diffs,
            "avg_diffs_per_call": round(total_diffs / total_calls, 1) if total_calls else 0,
            "analysis_input_tokens": usage_in,
            "analysis_output_tokens": usage_out,
            "analysis_cost_usd_total": round(cost_analysis, 4),
            "analysis_cost_per_call_usd": round(cost_analysis / total_calls, 4) if total_calls else 0,
            "merge_input_tokens": merge_in,
            "merge_output_tokens": merge_out,
            "merge_cost_usd_total": round(cost_merge, 4),
            "merge_cost_per_call_usd": round(cost_merge / total_calls, 4) if total_calls else 0,
            "total_cost_per_call_usd": round((cost_analysis + cost_merge) / total_calls, 4) if total_calls else 0,
        }

        out = {"summary": summary, "results": results}
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2, default=str)
        log.info("Wrote %s", output_path)
        log.info("Summary: %s", json.dumps(summary, ensure_ascii=False))
    finally:
        db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--out", default="/tmp/ab_results.json")
    args = p.parse_args()
    asyncio.run(main(args.n, args.out))
