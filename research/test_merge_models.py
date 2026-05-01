"""Сравнение мерж-моделей через OpenRouter на 5 звонках.

Архитектура:
  1. На каждый звонок один раз гоняем whisper-1 + энергию каналов → whisper_labeled.
  2. Для каждой мерж-модели прогоняем мерж по тем же двум транскрипциям (whisper_labeled + gpt-4o-text).
  3. merged_text каждой модели прогоняем через прод-анализ (OpenAI gpt-5.4).
  4. Сравниваем результат с прод-Analysis в БД.

Модели OpenRouter: переданы в --models через запятую.

Запуск (внутри call-analytics-backend):
    OPENROUTER_API_KEY=sk-or-v1-... python /tmp/test_merge_models.py --n 5

Скрипт НЕ пишет в БД.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from typing import Any

import numpy as np
import soundfile as sf
from sqlalchemy import select as sa_select

from app.config import settings
from app.database import SessionLocal
from app.models import Analysis, File, Transcription
from app.services.llm_service import (
    SYSTEM_PROMPT,
    LLMService,
    _apply_dependencies,
    _compute_group_score,
)
from app.services.pipeline import PipelineOrchestrator as Pipeline
from app.services.whisper_service import DOMAIN_PROMPT, WhisperService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("merge-ab")

DEFAULT_MODELS = [
    "moonshotai/kimi-latest",
    "qwen/qwen3.6-27b",
    "z-ai/glm-5.1",
    "google/gemma-4-26b-a4b-it:free",
    "google/gemini-flash-latest",
    "anthropic/claude-haiku-latest",
]


# ---------------------------------------------------------------------------
# OpenRouter client (uses OpenAI-compatible API)
# ---------------------------------------------------------------------------

def _make_openrouter_client():
    from openai import OpenAI
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY env var is required")
    return OpenAI(
        api_key=key,
        base_url="https://openrouter.ai/api/v1",
    )


# ---------------------------------------------------------------------------
# Step 1: cached whisper-1 + speaker labelling
# ---------------------------------------------------------------------------

async def get_whisper_labeled(db_file: File) -> tuple[str, str] | None:
    """Returns (whisper_labeled_text, gpt4o_text) or None if non-stereo / missing."""
    db = SessionLocal()
    try:
        tr = db.scalar(sa_select(Transcription).where(Transcription.file_id == db_file.id))
        if not tr or not tr.full_text:
            return None
        gpt4o_text = tr.full_text
    finally:
        db.close()

    whisper = WhisperService.get_instance()
    wclient = whisper._get_client()
    if wclient is None:
        return None

    audio_path = db_file.audio_path
    log.info("whisper-1: %s", audio_path)

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
            return wclient.audio.transcriptions.create(**kwargs)

    loop = asyncio.get_running_loop()
    resp = await loop.run_in_executor(None, _run_whisper_1)
    segments = getattr(resp, "segments", []) or []
    if not segments:
        return None

    try:
        snd = sf.SoundFile(audio_path)
    except Exception as exc:
        log.error("Cannot open audio: %s", exc)
        return None

    try:
        if snd.channels != 2:
            log.info("Not stereo, skipping")
            return None
        sr = snd.samplerate
        n_frames = snd.frames

        labeled = []
        for seg in segments:
            start = seg.start if hasattr(seg, "start") else seg.get("start", 0)
            end = seg.end if hasattr(seg, "end") else seg.get("end", 0)
            text = (seg.text if hasattr(seg, "text") else seg.get("text", "")).strip()

            s_start = max(0, int(start * sr))
            s_end = min(n_frames, int(end * sr))
            if s_end > s_start:
                snd.seek(s_start)
                chunk = snd.read(frames=s_end - s_start, dtype="float32", always_2d=True)
                l = float(np.sqrt(np.mean(chunk[:, 0] ** 2)))
                r = float(np.sqrt(np.mean(chunk[:, 1] ** 2)))
            else:
                l = r = 0.0

            if l > r * 1.3:
                spk = "ОПЕРАТОР"
            elif r > l * 1.3:
                spk = "КЛИЕНТ"
            else:
                spk = "НЕЯСНО"

            m, s = divmod(int(start), 60)
            labeled.append(f"[{m}:{s:02d}] {spk}: {text}")
    finally:
        snd.close()

    return "\n".join(labeled), gpt4o_text


# ---------------------------------------------------------------------------
# Step 2: merge with given OpenRouter model
# ---------------------------------------------------------------------------

def _build_merge_user_msg(whisper_labeled: str, gpt4o_text: str) -> str:
    return (
        f"=== ТРАНСКРИБАЦИЯ A (whisper-1, с таймстемпами и спикерами) ===\n{whisper_labeled}\n\n"
        f"=== ТРАНСКРИБАЦИЯ B (gpt-4o-transcribe, точный текст без разметки) ===\n{gpt4o_text}"
    )


async def run_merge_model(
    or_client: Any,
    pipeline: Pipeline,
    user_msg: str,
    model: str,
    timeout: int = 240,
) -> tuple[str | None, dict, float]:
    """Returns (merged_text, usage_dict, elapsed_sec)."""
    t0 = time.time()
    loop = asyncio.get_running_loop()

    def _call():
        return or_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": pipeline._MERGE_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            timeout=timeout,
            # OpenRouter: явно выключаем reasoning — у kimi-k2.6, qwen3.6 он
            # включён по умолчанию и тратит 100-300 сек на запрос.
            extra_body={"reasoning": {"enabled": False}},
        )

    try:
        resp = await loop.run_in_executor(None, _call)
    except Exception as exc:
        elapsed = time.time() - t0
        return None, {"error": f"{type(exc).__name__}: {exc}"}, elapsed

    elapsed = time.time() - t0
    text = (resp.choices[0].message.content or "").strip()
    if text.startswith("```"):
        ls = text.splitlines()
        text = "\n".join(l for l in ls if not l.strip().startswith("```")).strip()

    usage = {
        "prompt_tokens": getattr(resp.usage, "prompt_tokens", None),
        "completion_tokens": getattr(resp.usage, "completion_tokens", None),
        "total_tokens": getattr(resp.usage, "total_tokens", None),
        "model": getattr(resp, "model", model),
    }
    return text, usage, elapsed


# ---------------------------------------------------------------------------
# Step 3: production analysis via gpt-5.4
# ---------------------------------------------------------------------------

def analyze_via_prod(merged_text: str) -> tuple[dict | None, dict, float]:
    """Run analysis with whatever settings.llm_model is currently set to (gpt-5.4)."""
    llm = LLMService.get_instance()
    client = llm._get_client()
    user_msg = f"=== Полный диалог (с таймстемпами и спикерами) ===\n{merged_text.strip()}"
    t0 = time.time()
    resp = client.chat.completions.create(
        model=settings.llm_model,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        timeout=120,
    )
    elapsed = time.time() - t0
    raw = resp.choices[0].message.content or ""
    text = raw.strip()
    if text.startswith("```"):
        ls = text.splitlines()
        text = "\n".join(l for l in ls if not l.strip().startswith("```")).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("Analysis JSON parse error: %s | raw=%r", exc, raw[:300])
        parsed = None
    usage = {
        "prompt_tokens": getattr(resp.usage, "prompt_tokens", None),
        "completion_tokens": getattr(resp.usage, "completion_tokens", None),
    }
    return parsed, usage, elapsed


# ---------------------------------------------------------------------------
# Score helpers (mirrors LLMService logic)
# ---------------------------------------------------------------------------

def compute_scores(details: dict) -> dict:
    if not details:
        return {"standard": 0, "loyalty": 0, "kindness": 0, "overall": 0}
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
    s = _compute_group_score(flat.get("standard", {}))
    l = _compute_group_score(flat.get("loyalty", {}))
    k = _compute_group_score(flat.get("kindness", {}))
    return {"standard": s, "loyalty": l, "kindness": k, "overall": round((s + l + k) / 3)}


def flatten_details(details: dict) -> dict[str, dict]:
    out = {}
    for group, items in (details or {}).items():
        if group == "markers":
            continue
        for k, v in items.items():
            if isinstance(v, dict):
                out[f"{group}.{k}"] = {
                    "value": v.get("value"),
                    "reason": (v.get("reason", "") or "")[:120],
                }
            else:
                out[f"{group}.{k}"] = {"value": v, "reason": ""}
    return out


def diff_details(prod: dict, test: dict) -> list[dict]:
    diffs = []
    keys = set(prod.keys()) | set(test.keys())
    for k in sorted(keys):
        if k.startswith("reasons"):  # filter known noise
            continue
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(n: int, models: list[str], output_path: str):
    db = SessionLocal()
    try:
        files = list(db.scalars(
            sa_select(File)
            .join(Analysis, Analysis.file_id == File.id)
            .join(Transcription, Transcription.file_id == File.id)
            .where(File.call_type == "classical")
            .where(File.status == "done")
            .where(File.audio_path.is_not(None))
            .order_by(File.created_at.desc())
            .limit(n)
        ).all())
        log.info("Picked %d files", len(files))

        analyses = {
            a.file_id: a for a in db.scalars(
                sa_select(Analysis).where(Analysis.file_id.in_([f.id for f in files]))
            ).all()
        }
    finally:
        db.close()

    or_client = _make_openrouter_client()
    pipeline_dummy = Pipeline.__new__(Pipeline)  # only needed for _MERGE_PROMPT

    results_per_file = []

    for f in files:
        prod = analyses.get(f.id)
        if prod is None:
            continue

        log.info("=== File %s (%s) ===", f.id, f.original_name)

        cached = await get_whisper_labeled(f)
        if cached is None:
            log.warning("  whisper/cache failed, skip")
            continue
        whisper_labeled, gpt4o_text = cached
        user_msg = _build_merge_user_msg(whisper_labeled, gpt4o_text)

        prod_flat = flatten_details(prod.criteria_details or {})
        per_model_results = {}

        for model in models:
            log.info("  merge → %s", model)
            merged, m_usage, m_elapsed = await run_merge_model(or_client, pipeline_dummy, user_msg, model)
            if not merged or "error" in m_usage:
                log.warning("    merge FAILED: %s", m_usage.get("error", "empty"))
                per_model_results[model] = {
                    "error": m_usage.get("error", "empty merge"),
                    "merge_elapsed_sec": round(m_elapsed, 2),
                }
                continue

            log.info("    merge done in %.1fs (%d chars, %d lines)", m_elapsed, len(merged), merged.count("\n") + 1)

            # Analyse via gpt-5.4
            try:
                parsed, a_usage, a_elapsed = analyze_via_prod(merged)
            except Exception as exc:
                log.warning("    analysis FAILED: %s", exc)
                per_model_results[model] = {
                    "error": f"analysis: {exc}",
                    "merge_elapsed_sec": round(m_elapsed, 2),
                    "merge_chars": len(merged),
                }
                continue

            if parsed is None:
                per_model_results[model] = {
                    "error": "analysis returned invalid JSON",
                    "merge_elapsed_sec": round(m_elapsed, 2),
                    "merge_preview": merged[:500],
                }
                continue

            details = parsed.get("details", {})
            scores = compute_scores(details)
            test_flat = flatten_details(details)
            diffs = diff_details(prod_flat, test_flat)

            per_model_results[model] = {
                "merge_usage": m_usage,
                "merge_elapsed_sec": round(m_elapsed, 2),
                "merge_chars": len(merged),
                "merge_lines": merged.count("\n") + 1,
                "merge_preview": merged[:400],
                "analysis_usage": a_usage,
                "analysis_elapsed_sec": round(a_elapsed, 2),
                "scores": scores,
                "diff_count": len(diffs),
                "diffs": diffs,
                "delta_overall": scores["overall"] - prod.overall,
            }
            log.info(
                "    test scores: std=%d loy=%d kind=%d ovr=%d (Δovr=%+d) | diffs=%d",
                scores["standard"], scores["loyalty"], scores["kindness"], scores["overall"],
                scores["overall"] - prod.overall, len(diffs),
            )

        results_per_file.append({
            "file_id": str(f.id),
            "original_name": f.original_name,
            "duration_sec": f.duration_sec,
            "prod": {
                "model": prod.llm_model,
                "standard": prod.standard,
                "loyalty": prod.loyalty,
                "kindness": prod.kindness,
                "overall": prod.overall,
            },
            "per_model": per_model_results,
        })

    # Aggregate per model
    summary = {}
    for model in models:
        successes = [r["per_model"][model] for r in results_per_file if model in r["per_model"] and "error" not in r["per_model"][model]]
        failures = [r["per_model"][model] for r in results_per_file if model in r["per_model"] and "error" in r["per_model"][model]]
        if not successes:
            summary[model] = {"successes": 0, "failures": len(failures), "failure_reasons": [f["error"] for f in failures]}
            continue
        avg_diffs = sum(s["diff_count"] for s in successes) / len(successes)
        avg_delta = sum(s["delta_overall"] for s in successes) / len(successes)
        avg_merge_time = sum(s["merge_elapsed_sec"] for s in successes) / len(successes)
        avg_merge_chars = sum(s["merge_chars"] for s in successes) / len(successes)
        total_in = sum((s["merge_usage"].get("prompt_tokens") or 0) for s in successes)
        total_out = sum((s["merge_usage"].get("completion_tokens") or 0) for s in successes)
        summary[model] = {
            "successes": len(successes),
            "failures": len(failures),
            "failure_reasons": [f["error"] for f in failures],
            "avg_real_diffs": round(avg_diffs, 1),
            "avg_delta_overall": round(avg_delta, 1),
            "avg_merge_elapsed_sec": round(avg_merge_time, 1),
            "avg_merge_chars": round(avg_merge_chars),
            "total_merge_input_tokens": total_in,
            "total_merge_output_tokens": total_out,
        }

    out = {"summary": summary, "results": results_per_file, "models": models}
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)

    log.info("Saved %s", output_path)
    log.info("\n========= SUMMARY =========")
    for m, s in summary.items():
        if s["successes"]:
            log.info(
                "  %-45s succ=%d/%d diffs=%.1f Δovr=%+.1f merge_time=%.1fs merge_chars≈%d",
                m, s["successes"], s["successes"] + s["failures"],
                s["avg_real_diffs"], s["avg_delta_overall"],
                s["avg_merge_elapsed_sec"], s["avg_merge_chars"],
            )
        else:
            log.info("  %-45s ALL FAILED: %s", m, s["failure_reasons"][:1])


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--out", default="/tmp/merge_ab.json")
    p.add_argument("--models", default=",".join(DEFAULT_MODELS))
    args = p.parse_args()
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    asyncio.run(main(args.n, models, args.out))
