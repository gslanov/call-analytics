"""Финальный валидационный тест: полный пайплайн на gemini-3-flash-preview.

Архитектура:
  1. whisper-1 → whisper_labeled (один раз на звонок)
  2. МЕРЖ gemini-3-flash-preview (OpenRouter, reasoning=false)
  3. АНАЛИЗ gemini-3-flash-preview (OpenRouter, reasoning=false)
  4. Сравнение с прод-Analysis в БД

Прод gpt-5.4 НЕ используется. Цель — убедиться что полный пайплайн на одной
дешёвой модели не уступает по качеству.

Запуск:
    OPENROUTER_API_KEY=... python /tmp/test_full_gemini.py --n 20
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
log = logging.getLogger("full")

MERGE_MODEL = "google/gemini-3-flash-preview"
ANALYSIS_MODEL = "google/gemini-3-flash-preview"

# $0.50 / $3.00 per 1M
GEMINI_IN_PRICE = 0.50 / 1e6
GEMINI_OUT_PRICE = 3.00 / 1e6


def _make_or_client():
    from openai import OpenAI
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY required")
    return OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1")


# ------------- whisper + speakers (cached per call) -------------

async def get_whisper_labeled(db_file: File) -> tuple[str, str] | None:
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

    def _run():
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
    resp = await loop.run_in_executor(None, _run)
    segments = getattr(resp, "segments", []) or []
    if not segments:
        return None

    try:
        snd = sf.SoundFile(audio_path)
    except Exception:
        return None
    try:
        if snd.channels != 2:
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


# ------------- merge with gemini -------------

async def run_gemini_merge(or_client: Any, pipeline: Pipeline, whisper_labeled: str, gpt4o_text: str) -> tuple[str | None, dict, float]:
    user_msg = (
        f"=== ТРАНСКРИБАЦИЯ A (whisper-1, с таймстемпами и спикерами) ===\n{whisper_labeled}\n\n"
        f"=== ТРАНСКРИБАЦИЯ B (gpt-4o-transcribe, точный текст без разметки) ===\n{gpt4o_text}"
    )
    loop = asyncio.get_running_loop()

    def _call():
        return or_client.chat.completions.create(
            model=MERGE_MODEL,
            messages=[
                {"role": "system", "content": pipeline._MERGE_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            timeout=180,
            extra_body={"reasoning": {"enabled": False}},
        )

    t0 = time.time()
    try:
        resp = await loop.run_in_executor(None, _call)
    except Exception as exc:
        return None, {"error": f"{type(exc).__name__}: {exc}"}, time.time() - t0

    elapsed = time.time() - t0
    text = (resp.choices[0].message.content or "").strip()
    if text.startswith("```"):
        text = "\n".join(l for l in text.splitlines() if not l.strip().startswith("```")).strip()
    usage = {
        "prompt_tokens": getattr(resp.usage, "prompt_tokens", None),
        "completion_tokens": getattr(resp.usage, "completion_tokens", None),
    }
    return text, usage, elapsed


# ------------- analysis with gemini -------------

def run_gemini_analysis(or_client: Any, merged_text: str) -> tuple[dict | None, dict, float]:
    user_msg = f"=== Полный диалог (с таймстемпами и спикерами) ===\n{merged_text.strip()}"
    t0 = time.time()
    try:
        resp = or_client.chat.completions.create(
            model=ANALYSIS_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            timeout=180,
            extra_body={"reasoning": {"enabled": False}},
        )
    except Exception as exc:
        return None, {"error": f"{type(exc).__name__}: {exc}"}, time.time() - t0
    elapsed = time.time() - t0
    raw = resp.choices[0].message.content or ""
    text = raw.strip()
    if text.startswith("```"):
        text = "\n".join(l for l in text.splitlines() if not l.strip().startswith("```")).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("Analysis JSON parse error: %s | raw=%r", exc, raw[:200])
        return None, {"json_error": str(exc), "raw_preview": raw[:300]}, elapsed
    usage = {
        "prompt_tokens": getattr(resp.usage, "prompt_tokens", None),
        "completion_tokens": getattr(resp.usage, "completion_tokens", None),
    }
    return parsed, usage, elapsed


# ------------- score helpers -------------

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
                out[f"{group}.{k}"] = {"value": v.get("value"), "reason": (v.get("reason", "") or "")[:120]}
            else:
                out[f"{group}.{k}"] = {"value": v, "reason": ""}
    return out


def diff_details(prod: dict, test: dict) -> list[dict]:
    diffs = []
    keys = set(prod.keys()) | set(test.keys())
    for k in sorted(keys):
        if k.startswith("reasons"):
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


# ------------- main -------------

async def main(n: int, output_path: str):
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
        analyses = {a.file_id: a for a in db.scalars(
            sa_select(Analysis).where(Analysis.file_id.in_([f.id for f in files]))
        ).all()}
    finally:
        db.close()

    log.info("Picked %d files", len(files))

    or_client = _make_or_client()
    pipeline = Pipeline.__new__(Pipeline)
    results = []

    total_merge_in = 0
    total_merge_out = 0
    total_analysis_in = 0
    total_analysis_out = 0

    for i, f in enumerate(files, 1):
        prod = analyses.get(f.id)
        if prod is None:
            continue

        log.info("=== [%d/%d] %s ===", i, len(files), f.original_name)

        cached = await get_whisper_labeled(f)
        if cached is None:
            log.warning("  whisper failed")
            continue
        whisper_labeled, gpt4o_text = cached

        # Merge via gemini
        merged_text, m_usage, m_elapsed = await run_gemini_merge(or_client, pipeline, whisper_labeled, gpt4o_text)
        if not merged_text or "error" in m_usage:
            log.warning("  merge FAILED: %s", m_usage.get("error", "empty"))
            results.append({"file_id": str(f.id), "original_name": f.original_name,
                          "error": f"merge: {m_usage.get('error', 'empty')}"})
            continue
        log.info("  merge done %.1fs (%d chars)", m_elapsed, len(merged_text))
        total_merge_in += m_usage.get("prompt_tokens") or 0
        total_merge_out += m_usage.get("completion_tokens") or 0

        # Analysis via gemini
        parsed, a_usage, a_elapsed = run_gemini_analysis(or_client, merged_text)
        if parsed is None:
            log.warning("  analysis FAILED: %s", a_usage.get("error") or a_usage.get("json_error"))
            results.append({"file_id": str(f.id), "original_name": f.original_name,
                          "error": f"analysis: {a_usage}"})
            continue
        total_analysis_in += a_usage.get("prompt_tokens") or 0
        total_analysis_out += a_usage.get("completion_tokens") or 0

        details = parsed.get("details", {})
        scores = compute_scores(details)
        prod_flat = flatten_details(prod.criteria_details or {})
        test_flat = flatten_details(details)
        diffs = diff_details(prod_flat, test_flat)

        result_record = {
            "file_id": str(f.id),
            "original_name": f.original_name,
            "duration_sec": f.duration_sec,
            "prod": {
                "model": prod.llm_model,
                "standard": prod.standard, "loyalty": prod.loyalty,
                "kindness": prod.kindness, "overall": prod.overall,
            },
            "test": {
                "merge_model": MERGE_MODEL,
                "analysis_model": ANALYSIS_MODEL,
                "standard": scores["standard"], "loyalty": scores["loyalty"],
                "kindness": scores["kindness"], "overall": scores["overall"],
                "merge_elapsed_sec": round(m_elapsed, 2),
                "analysis_elapsed_sec": round(a_elapsed, 2),
                "merge_usage": m_usage,
                "analysis_usage": a_usage,
                "merge_chars": len(merged_text),
                "summary_preview": (parsed.get("summary", "") or "")[:200],
            },
            "delta_overall": scores["overall"] - prod.overall,
            "diff_count": len(diffs),
            "diffs": diffs,
        }
        results.append(result_record)
        log.info(
            "  ovr=%d (Δ%+d) diffs=%d total=%.1fs analysis=%.1fs",
            scores["overall"], scores["overall"] - prod.overall, len(diffs),
            m_elapsed + a_elapsed, a_elapsed,
        )

    # Aggregate
    succ = [r for r in results if "error" not in r]
    if not succ:
        log.error("ALL FAILED")
        return

    avg_diffs = sum(r["diff_count"] for r in succ) / len(succ)
    avg_delta = sum(r["delta_overall"] for r in succ) / len(succ)
    avg_merge_time = sum(r["test"]["merge_elapsed_sec"] for r in succ) / len(succ)
    avg_analysis_time = sum(r["test"]["analysis_elapsed_sec"] for r in succ) / len(succ)

    cost_total = (
        total_merge_in * GEMINI_IN_PRICE + total_merge_out * GEMINI_OUT_PRICE
        + total_analysis_in * GEMINI_IN_PRICE + total_analysis_out * GEMINI_OUT_PRICE
    )

    summary = {
        "tested_calls": len(succ),
        "failures": len(results) - len(succ),
        "merge_model": MERGE_MODEL,
        "analysis_model": ANALYSIS_MODEL,
        "avg_real_diffs": round(avg_diffs, 2),
        "avg_delta_overall": round(avg_delta, 2),
        "avg_merge_elapsed_sec": round(avg_merge_time, 1),
        "avg_analysis_elapsed_sec": round(avg_analysis_time, 1),
        "avg_total_per_call_sec": round(avg_merge_time + avg_analysis_time, 1),
        "merge_input_tokens": total_merge_in,
        "merge_output_tokens": total_merge_out,
        "analysis_input_tokens": total_analysis_in,
        "analysis_output_tokens": total_analysis_out,
        "cost_total_usd": round(cost_total, 4),
        "cost_per_call_usd": round(cost_total / len(succ), 5),
        # Distribution of diffs
        "diffs_distribution": {
            "0_diffs": sum(1 for r in succ if r["diff_count"] == 0),
            "1_diff": sum(1 for r in succ if r["diff_count"] == 1),
            "2_diffs": sum(1 for r in succ if r["diff_count"] == 2),
            "3_diffs": sum(1 for r in succ if r["diff_count"] == 3),
            "4plus_diffs": sum(1 for r in succ if r["diff_count"] >= 4),
        },
        "delta_distribution": {
            "perfect (Δ=0)": sum(1 for r in succ if r["delta_overall"] == 0),
            "minor (|Δ|≤3)": sum(1 for r in succ if 0 < abs(r["delta_overall"]) <= 3),
            "medium (3<|Δ|≤10)": sum(1 for r in succ if 3 < abs(r["delta_overall"]) <= 10),
            "major (|Δ|>10)": sum(1 for r in succ if abs(r["delta_overall"]) > 10),
        },
    }

    out = {"summary": summary, "results": results}
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    log.info("Saved %s", output_path)
    log.info("\n========= FINAL SUMMARY =========")
    log.info("Tested: %d/%d  | Diffs avg: %.2f  | Δovr avg: %+.2f", len(succ), len(results), avg_diffs, avg_delta)
    log.info("Time/call: merge=%.1fs + analysis=%.1fs = %.1fs", avg_merge_time, avg_analysis_time, avg_merge_time + avg_analysis_time)
    log.info("Cost/call: $%.5f  |  Total: $%.4f", cost_total / len(succ), cost_total)
    log.info("Diffs distribution: %s", summary["diffs_distribution"])
    log.info("Delta distribution: %s", summary["delta_distribution"])


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--out", default="/tmp/full_gemini.json")
    args = p.parse_args()
    asyncio.run(main(args.n, args.out))
