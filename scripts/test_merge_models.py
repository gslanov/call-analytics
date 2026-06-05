"""Эксперимент: сравнение моделей на ШАГЕ СКЛЕЙКИ (triple merge).

НЕ трогает прод и pipeline. Берёт несколько реальных звонков, один раз гоняет
whisper-1 (таймстемпы + энергетическая разметка каналов), затем прогоняет
ОДИН И ТОТ ЖЕ вход через набор моделей-кандидатов на OpenRouter и сохраняет
их склейки для глазного сравнения (роли ОПЕРАТОР/КЛИЕНТ + «Пироги №1»).

Запуск (внутри backend-контейнера на проде — там аудио + OPENAI_API_KEY):
  docker exec -e PYTHONPATH=/app -e OPENROUTER_API_KEY=sk-or-... \
    call-analytics-backend python /tmp/test_merge_models.py

Вывод: /tmp/merge_test/<call>__<model>.txt + компактная таблица в stdout.
"""
from __future__ import annotations

import os
import sys
import asyncio

import numpy as np
import soundfile as sf
from openai import OpenAI

sys.path.insert(0, "/app")
from app.config import settings
from app.database import SessionLocal
from app.models import File, Transcription
from app.services.pipeline import PipelineOrchestrator
from sqlalchemy import select

# --- что сравниваем -----------------------------------------------------------
FILE_IDS = [
    "d1afda22-0d67-4e98-b734-22617ac87d4b",
    "ec92bf6f-8314-4c51-8a70-36cb6d761ae2",
    "0f6e6794-b4d6-4095-bd32-02b3cc38e4b4",
]

# (метка, slug на OpenRouter)
MODELS = [
    ("gemini-gold",   "google/gemini-3-flash-preview"),  # эталон (то что было до 01.06)
    ("gpt5mini-now",  "openai/gpt-5-mini"),               # текущий fallback (на что жалоба)
    ("deepseek-v4pro","deepseek/deepseek-v4-pro"),
    ("minimax-m3",    "minimax/minimax-m3"),
    ("qwen3.7-plus",  "qwen/qwen3.7-plus"),
    ("gpt5.4-nano",   "openai/gpt-5.4-nano"),
    ("gpt5.4-mini",   "openai/gpt-5.4-mini"),
]

OUT_DIR = "/tmp/merge_test"
MERGE_PROMPT = PipelineOrchestrator._MERGE_PROMPT


def whisper_labeled_for(db, file_id: str) -> tuple[str, str] | None:
    """Возвращает (whisper_labeled, gpt4o_text) — вход для склейки. None если не стерео."""
    db_file = db.get(File, file_id)
    if not db_file:
        print(f"  [skip] {file_id}: нет File")
        return None
    tr = db.scalar(select(Transcription).where(Transcription.file_id == db_file.id))
    if not tr or not tr.full_text:
        print(f"  [skip] {file_id}: нет gpt4o text")
        return None
    gpt4o_text = tr.full_text

    wclient = OpenAI(api_key=settings.openai_api_key)
    with open(db_file.audio_path, "rb") as f:
        resp = wclient.audio.transcriptions.create(
            model="whisper-1", file=f, language="ru",
            response_format="verbose_json",
            timestamp_granularities=["word", "segment"],
        )
    segs = resp.segments if hasattr(resp, "segments") else []
    if not segs:
        print(f"  [skip] {file_id}: whisper-1 без сегментов")
        return None

    snd = sf.SoundFile(db_file.audio_path)
    if snd.channels != 2:
        snd.close()
        print(f"  [skip] {file_id}: не стерео")
        return None
    sr, n_frames = snd.samplerate, snd.frames
    lines = []
    for seg in segs:
        start = seg.start; end = seg.end
        text = (seg.text or "").strip()
        s0, s1 = max(0, int(start * sr)), min(n_frames, int(end * sr))
        if s1 > s0:
            snd.seek(s0)
            chunk = snd.read(frames=s1 - s0, dtype="float32", always_2d=True)
            le = float(np.sqrt(np.mean(chunk[:, 0] ** 2)))
            re = float(np.sqrt(np.mean(chunk[:, 1] ** 2)))
        else:
            le = re = 0.0
        sp = "ОПЕРАТОР" if le > re * 1.3 else ("КЛИЕНТ" if re > le * 1.3 else "НЕЯСНО")
        m, s = divmod(int(start), 60)
        lines.append(f"[{m}:{s:02d}] {sp}: {text}")
    snd.close()
    return "\n".join(lines), gpt4o_text


def run_model(or_client, slug: str, whisper_labeled: str, gpt4o_text: str) -> str:
    user_msg = (
        f"=== ТРАНСКРИБАЦИЯ A (whisper-1, с таймстемпами и спикерами) ===\n{whisper_labeled}\n\n"
        f"=== ТРАНСКРИБАЦИЯ B (gpt-4o-transcribe, точный текст без разметки) ===\n{gpt4o_text}"
    )
    kwargs = dict(
        model=slug,
        messages=[
            {"role": "system", "content": MERGE_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        timeout=180,
        extra_body={"reasoning": {"enabled": False}},  # не платить за reasoning, не ждать 100с
    )
    if not slug.startswith("openai/gpt-5"):
        kwargs["temperature"] = 0
    resp = or_client.chat.completions.create(**kwargs)
    if not resp.choices:
        return f"[ОШИБКА] пустой choices: {resp}"
    return (resp.choices[0].message.content or "").strip()


def main() -> None:
    or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not or_key:
        print("НЕТ OPENROUTER_API_KEY в окружении"); sys.exit(1)
    or_client = OpenAI(api_key=or_key, base_url="https://openrouter.ai/api/v1")
    os.makedirs(OUT_DIR, exist_ok=True)
    db = SessionLocal()

    summary_rows = []
    for fid in FILE_IDS:
        print(f"\n=== Звонок {fid} ===")
        print("  whisper-1 + энергия каналов...")
        prep = whisper_labeled_for(db, fid)
        if not prep:
            continue
        whisper_labeled, gpt4o_text = prep
        with open(f"{OUT_DIR}/{fid}__INPUT_whisper.txt", "w") as f:
            f.write(whisper_labeled)
        with open(f"{OUT_DIR}/{fid}__INPUT_gpt4o.txt", "w") as f:
            f.write(gpt4o_text)

        for label, slug in MODELS:
            try:
                out = run_model(or_client, slug, whisper_labeled, gpt4o_text)
            except Exception as exc:
                out = f"[ОШИБКА {type(exc).__name__}] {exc}"
            with open(f"{OUT_DIR}/{fid}__{label}.txt", "w") as f:
                f.write(out)
            n_lines = out.count("\n") + 1
            has_pirogi = "Пироги" in out or "пироги №1" in out.lower()
            n_op = out.count("ОПЕРАТОР"); n_cl = out.count("КЛИЕНТ")
            err = out.startswith("[ОШИБКА")
            first = next((l for l in out.splitlines() if l.strip()), "")
            print(f"  {label:14s} lines={n_lines:3d} op={n_op:2d} cl={n_cl:2d} "
                  f"Пироги={'Y' if has_pirogi else 'n'} {'ERR' if err else ''} | {first[:90]}")
            summary_rows.append((fid[:8], label, n_lines, n_op, n_cl, has_pirogi, err))

    db.close()
    print(f"\nПолные склейки: {OUT_DIR}/")


if __name__ == "__main__":
    main()
