"""Сравнение моделей-анализаторов на тех же 5 звонках.

Архитектура:
  1. Один whisper-1 + gpt-5.4 мерж на звонок (прод-эквивалент мерджа).
  2. Полученный merged_text прогоняется через несколько анализаторов:
     - gemini-3-flash-preview (OpenRouter)
     - z-ai/glm-5.1 (OpenRouter)
     - anthropic/claude-haiku-4.5 (OpenRouter)
     - gpt-5.4 (OpenAI прямой) — контрольный, должен почти совпасть с прод
  3. Сравнение с прод-Analysis в БД.

Reasoning у OpenRouter моделей выключен через extra_body.

Запуск:
    OPENROUTER_API_KEY=... python /tmp/test_analysis_models.py --n 5
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
log = logging.getLogger("ab")

DEFAULT_ANALYSIS_MODELS = [
    "google/gemini-3-flash-preview",
    "z-ai/glm-5.1",
    "anthropic/claude-haiku-4.5",
]

PRICING = {
    "google/gemini-3-flash-preview": (0.50, 3.00),
    "z-ai/glm-5.1": (1.05, 3.50),
    "anthropic/claude-haiku-4.5": (1.00, 5.00),
    "gpt-5.4": (1.25, 10.00),
}


def _make_or_client():
    from openai import OpenAI
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY required")
    return OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1")


def _make_openai_client():
    from openai import OpenAI
    return OpenAI(api_key=settings.openai_api_key)


# ---- Step 1: whisper-1 + speakers (same as test_merge_models) -----------------

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
    log.info("whisper-1: %s", audio_path)

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
    except Exception as exc:
        log.error("Cannot open audio: %s", exc)
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


# ---- Step 2: gpt-5.4 merge (production-equivalent) -----------------------------

async def run_prod_merge(openai_client: Any, pipeline: Pipeline, whisper_labeled: str, gpt4o_text: str) -> str | None:
    user_msg = (
        f"=== ТРАНСКРИБАЦИЯ A (whisper-1, с таймстемпами и спикерами) ===\n{whisper_labeled}\n\n"
        f"=== ТРАНСКРИБАЦИЯ B (gpt-4o-transcribe, точный текст без разметки) ===\n{gpt4o_text}"
    )
    loop = asyncio.get_running_loop()

    def _call():
        return openai_client.chat.completions.create(
            model=settings.llm_model,  # gpt-5.4
            temperature=0,
            messages=[
                {"role": "system", "content": pipeline._MERGE_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            timeout=180,
        )

    resp = await loop.run_in_executor(None, _call)
    text = (resp.choices[0].message.content or "").strip()
    if text.startswith("```"):
        text = "\n".join(l for l in text.splitlines() if not l.strip().startswith("```")).strip()
    return text


# ---- Step 3: analyze with given model (OpenRouter or OpenAI) ---------------------

def analyze_with(client: Any, model: str, merged_text: str, *, is_openrouter: bool) -> tuple[dict | None, dict, float]:
    user_msg = f"=== Полный диалог (с таймстемпами и спикерами) ===\n{merged_text.strip()}"
    t0 = time.time()
    kwargs: dict[str, Any] = dict(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        timeout=180,
    )
    if is_openrouter:
        kwargs["extra_body"] = {"reasoning": {"enabled": False}}
    else:
        # OpenAI gpt-5.4 поддерживает temperature=0
        kwargs["temperature"] = 0

    try:
        resp = client.chat.completions.create(**kwargs)
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
        log.warning("[%s] JSON parse error: %s | raw=%r", model, exc, raw[:200])
        return None, {"json_error": str(exc), "raw_preview": raw[:300]}, elapsed
    usage = {
        "prompt_tokens": getattr(resp.usage, "prompt_tokens", None),
        "completion_tokens": getattr(resp.usage, "completion_tokens", None),
    }
    return parsed, usage, elapsed


# ---- Score helpers (mirrors LLMService) ----------------------------------------

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


# ---- Main ----------------------------------------------------------------------

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
        analyses = {a.file_id: a for a in db.scalars(
            sa_select(Analysis).where(Analysis.file_id.in_([f.id for f in files]))
        ).all()}
    finally:
        db.close()

    log.info("Picked %d files", len(files))

    or_client = _make_or_client()
    openai_client = _make_openai_client()
    pipeline = Pipeline.__new__(Pipeline)

    # Add gpt-5.4 control as a model
    if "gpt-5.4" not in models:
        models = models + ["gpt-5.4"]

    results = []

    for f in files:
        prod = analyses.get(f.id)
        if prod is None:
            continue

        log.info("=== File %s (%s) ===", f.id, f.original_name)

        cached = await get_whisper_labeled(f)
        if cached is None:
            log.warning("  whisper failed")
            continue
        whisper_labeled, gpt4o_text = cached

        # Production-equivalent merge
        log.info("  merge → gpt-5.4 (prod-equiv)")
        t0 = time.time()
        try:
            merged_text = await run_prod_merge(openai_client, pipeline, whisper_labeled, gpt4o_text)
        except Exception as exc:
            log.exception("  merge failed: %s", exc)
            continue
        log.info("    merged %d chars in %.1fs", len(merged_text or ""), time.time() - t0)
        if not merged_text:
            continue

        prod_flat = flatten_details(prod.criteria_details or {})
        per_model = {}

        for model in models:
            log.info("  analyze → %s", model)
            is_or = "/" in model
            client = or_client if is_or else openai_client
            parsed, usage, elapsed = analyze_with(client, model, merged_text, is_openrouter=is_or)
            if parsed is None:
                log.warning("    FAIL: %s", usage)
                per_model[model] = {"error": usage, "elapsed_sec": round(elapsed, 2)}
                continue
            details = parsed.get("details", {})
            scores = compute_scores(details)
            test_flat = flatten_details(details)
            diffs = diff_details(prod_flat, test_flat)
            per_model[model] = {
                "elapsed_sec": round(elapsed, 2),
                "usage": usage,
                "scores": scores,
                "diff_count": len(diffs),
                "diffs": diffs,
                "delta_overall": scores["overall"] - prod.overall,
                "summary_preview": (parsed.get("summary", "") or "")[:200],
            }
            log.info(
                "    ovr=%d (Δ%+d) diffs=%d time=%.1fs tokens=%s/%s",
                scores["overall"], scores["overall"] - prod.overall, len(diffs), elapsed,
                usage.get("prompt_tokens"), usage.get("completion_tokens"),
            )

        results.append({
            "file_id": str(f.id),
            "original_name": f.original_name,
            "duration_sec": f.duration_sec,
            "prod": {
                "model": prod.llm_model,
                "standard": prod.standard, "loyalty": prod.loyalty,
                "kindness": prod.kindness, "overall": prod.overall,
            },
            "merged_chars": len(merged_text),
            "per_model": per_model,
        })

    # Aggregate
    summary = {}
    for model in models:
        succ = [r["per_model"][model] for r in results if model in r["per_model"] and "error" not in r["per_model"][model]]
        fail = [r["per_model"][model] for r in results if model in r["per_model"] and "error" in r["per_model"][model]]
        if not succ:
            summary[model] = {"successes": 0, "failures": len(fail)}
            continue
        avg_diffs = sum(s["diff_count"] for s in succ) / len(succ)
        avg_delta = sum(s["delta_overall"] for s in succ) / len(succ)
        avg_time = sum(s["elapsed_sec"] for s in succ) / len(succ)
        in_tok = sum((s["usage"].get("prompt_tokens") or 0) for s in succ)
        out_tok = sum((s["usage"].get("completion_tokens") or 0) for s in succ)
        in_p, out_p = PRICING.get(model, (0, 0))
        cost_total = (in_tok / 1e6) * in_p + (out_tok / 1e6) * out_p
        summary[model] = {
            "successes": len(succ),
            "failures": len(fail),
            "avg_real_diffs": round(avg_diffs, 1),
            "avg_delta_overall": round(avg_delta, 1),
            "avg_elapsed_sec": round(avg_time, 1),
            "input_tokens_total": in_tok,
            "output_tokens_total": out_tok,
            "cost_usd_total_5calls": round(cost_total, 4),
            "cost_per_call_usd": round(cost_total / len(succ), 5) if succ else 0,
        }

    out = {"summary": summary, "results": results, "models": models}
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)

    log.info("Saved %s", output_path)
    log.info("\n========= ANALYSIS-MODEL SUMMARY =========")
    for m, s in summary.items():
        if s.get("successes"):
            log.info(
                "  %-40s succ=%d  diffs=%.1f  Δovr=%+.1f  time=%.1fs  $/call=%.5f",
                m, s["successes"], s["avg_real_diffs"], s["avg_delta_overall"],
                s["avg_elapsed_sec"], s["cost_per_call_usd"],
            )
        else:
            log.info("  %-40s ALL FAILED", m)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--out", default="/tmp/analysis_ab.json")
    p.add_argument("--models", default=",".join(DEFAULT_ANALYSIS_MODELS))
    args = p.parse_args()
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    asyncio.run(main(args.n, models, args.out))
