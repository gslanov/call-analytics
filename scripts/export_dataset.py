"""Выгрузка датасета звонков для дообучения моделей (open-source — GigaAM/GigaChat и т.п.).

Структура итогового архива:

    call-analytics-dataset-YYYY-MM-DD/
    ├── README.md              — описание полей и процесса сборки
    ├── dataset.jsonl          — основной файл, по одной строке на звонок
    ├── system_prompt.txt      — наш системный промпт (для обучения LLM-аналитика)
    ├── stats.json             — сводная статистика (counts, ranges, models used)
    └── audio/
        └── <original_name>.mp3

Каждая JSONL строка — независимый объект следующей структуры:

    {
        "file_id": "uuid",
        "audio_relpath": "audio/2026-04-21__19-35-18__Менеджер Галина__790...mp3",
        "audio_sha256": "...",
        "operator": "Галина",
        "duration_sec": 206.4,
        "call_started_at": "2026-04-21T19:35:18",
        "uploaded_at": "2026-05-07T10:30:35",
        "call_type": "classical",
        "transcript": "[0:00] ОПЕРАТОР: ...\n[0:03] КЛИЕНТ: ...",
        "diarization": {
            "method": "stereo|gpt-4o-transcribe|...",
            "num_speakers": 2,
            "segments": [{"speaker":"operator", "start":0.0, "end":3.5, "text":"..."}, ...]
        },
        "analysis": {
            "scores": {"overall":91, "standard":92, "loyalty":80, "kindness":100},
            "details": { ... criteria_details ... },
            "summary": "...",
            "quotes": [...],
            "llm_model": "gemini-3-flash-preview",
            "criteria_version": "v4"
        }
    }

Запуск (внутри backend-контейнера):
    docker exec -e PYTHONPATH=/app call-analytics-backend \\
        python scripts/export_dataset.py --output /tmp/dataset_export

После — на хосте: tar czf dataset.tgz -C /tmp dataset_export
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("export")

SYSTEM_PROMPT_V4 = "(см. backend/app/services/llm_service.py:SYSTEM_PROMPT)"


def _infer_criteria_version(row: dict[str, Any]) -> str | None:
    """В БД нет колонки criteria_version (alembic drift), эвристика по дате анализа.
    v4 — после 2026-05-08 12:00 UTC (мой коммит 74e8c5d).
    v3 — до этого момента (расширения по фидбеку РОП с апреля).
    """
    analyzed = row.get("analyzed_at")
    if analyzed is None:
        return None
    cutoff = datetime(2026, 5, 8, 12, 0, 0)
    return "v4" if analyzed >= cutoff else "v3"


def fetch_all_calls(only_classical: bool = False) -> list[dict[str, Any]]:
    """Тянет ВСЕ звонки со статусом done и связанными analyses/transcriptions/diarizations."""
    engine = create_engine(os.environ["DATABASE_URL"])
    where = "f.status = 'done'"
    if only_classical:
        where += " AND f.call_type = 'classical'"

    sql = f"""
        SELECT
            f.id::text          AS file_id,
            f.original_name     AS original_name,
            f.audio_path        AS audio_path,
            f.file_hash         AS audio_sha256,
            f.file_size         AS file_size,
            f.duration_sec      AS duration_sec,
            f.call_type         AS call_type,
            f.call_started_at   AS call_started_at,
            f.created_at        AS uploaded_at,
            o.name              AS operator_name,
            t.full_text         AS transcript,
            d.method            AS diar_method,
            d.num_speakers      AS num_speakers,
            d.segments          AS diar_segments,
            a.overall, a.standard, a.loyalty, a.kindness,
            a.summary, a.quotes,
            a.criteria_details  AS criteria_details,
            a.llm_model,
            a.created_at        AS analyzed_at
        FROM files f
        LEFT JOIN operators       o ON o.id = f.operator_id
        LEFT JOIN transcriptions  t ON t.file_id = f.id
        LEFT JOIN diarizations    d ON d.file_id = f.id
        LEFT JOIN analyses        a ON a.file_id = f.id
        WHERE {where}
        ORDER BY f.created_at DESC
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql)).mappings().all()
    return [dict(r) for r in rows]


def serialize_record(row: dict[str, Any], audio_relpath: str | None) -> dict[str, Any]:
    def iso(dt):
        return dt.isoformat() if dt else None

    record = {
        "file_id": row["file_id"],
        "original_name": row["original_name"],
        "audio_relpath": audio_relpath,
        "audio_sha256": row["audio_sha256"],
        "audio_size_bytes": row["file_size"],
        "operator": row["operator_name"],
        "duration_sec": float(row["duration_sec"]) if row["duration_sec"] is not None else None,
        "call_started_at": iso(row["call_started_at"]),
        "uploaded_at": iso(row["uploaded_at"]),
        "call_type": row["call_type"],
        "transcript": row["transcript"],
        "diarization": {
            "method": row["diar_method"],
            "num_speakers": row["num_speakers"],
            "segments": row["diar_segments"],
        } if row["transcript"] is not None else None,
    }
    if row.get("overall") is not None:
        record["analysis"] = {
            "scores": {
                "overall":  row["overall"],
                "standard": row["standard"],
                "loyalty":  row["loyalty"],
                "kindness": row["kindness"],
            },
            "summary":  row["summary"],
            "quotes":   row["quotes"],
            "details":  row["criteria_details"],
            "llm_model": row["llm_model"],
            "criteria_version": _infer_criteria_version(row),
            "analyzed_at": iso(row["analyzed_at"]),
        }
    else:
        record["analysis"] = None
    return record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True, help="Output dir (will contain dataset.jsonl + audio/)")
    ap.add_argument("--only-classical", action="store_true", help="Только classical звонки (default: все типы)")
    ap.add_argument("--no-audio", action="store_true", help="Пропустить копирование аудио (только метаданные)")
    ap.add_argument("--limit", type=int, default=0, help="Ограничить N записями для теста (0 = все)")
    args = ap.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    audio_dir = out / "audio"
    audio_dir.mkdir(exist_ok=True)

    log.info("Fetching calls from DB (only_classical=%s)…", args.only_classical)
    calls = fetch_all_calls(only_classical=args.only_classical)
    if args.limit:
        calls = calls[:args.limit]
    log.info("Got %d calls", len(calls))

    stats = {
        "total": len(calls),
        "by_call_type": {},
        "by_llm_model": {},
        "by_criteria_version": {},
        "with_analysis": 0,
        "with_transcript": 0,
        "with_audio": 0,
        "audio_missing": 0,
        "audio_path_empty": 0,
        "duration_sec_total": 0.0,
        "exported_at": datetime.now().isoformat(),
    }

    jsonl_path = out / "dataset.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for i, row in enumerate(calls, 1):
            audio_relpath: str | None = None
            if not args.no_audio:
                if not row["audio_path"]:
                    stats["audio_path_empty"] += 1
                else:
                    src = Path(row["audio_path"])
                    # audio_path в БД = "/app/data/uploads/<UUID>.mp3" (внутри контейнера)
                    if not src.exists():
                        log.warning("Audio missing on disk: %s (file_id=%s)", src, row["file_id"])
                        stats["audio_missing"] += 1
                    else:
                        dst_name = src.name  # имя на диске = UUID.mp3
                        dst = audio_dir / dst_name
                        if not dst.exists():
                            try:
                                # hard link не дублирует место и I/O — оба пути на одной FS
                                os.link(src, dst)
                            except OSError:
                                try:
                                    shutil.copyfile(src, dst)
                                except Exception as exc:
                                    log.warning("Copy failed for %s: %s", src, exc)
                                    stats["audio_missing"] += 1
                                    dst = None  # type: ignore
                        if dst is not None:
                            audio_relpath = f"audio/{dst_name}"
                            stats["with_audio"] += 1

            rec = serialize_record(row, audio_relpath)
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

            ct = row["call_type"] or "unknown"
            stats["by_call_type"][ct] = stats["by_call_type"].get(ct, 0) + 1
            if row.get("transcript"):
                stats["with_transcript"] += 1
            if row.get("overall") is not None:
                stats["with_analysis"] += 1
                m = row.get("llm_model") or "unknown"
                stats["by_llm_model"][m] = stats["by_llm_model"].get(m, 0) + 1
                v = _infer_criteria_version(row) or "unknown"
                stats["by_criteria_version"][v] = stats["by_criteria_version"].get(v, 0) + 1
            if row.get("duration_sec"):
                stats["duration_sec_total"] += float(row["duration_sec"])

            if i % 100 == 0:
                log.info("Progress: %d/%d (%.0f%%)", i, len(calls), 100.0 * i / len(calls))

    # Summary stats
    stats["duration_hours_total"] = round(stats["duration_sec_total"] / 3600, 1)
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Wrote %s", out / "stats.json")

    # System prompt — копируем напрямую из llm_service
    try:
        from app.services.llm_service import SYSTEM_PROMPT
        (out / "system_prompt.txt").write_text(SYSTEM_PROMPT, encoding="utf-8")
        log.info("Wrote %s", out / "system_prompt.txt")
    except Exception as exc:
        log.warning("Could not import SYSTEM_PROMPT: %s", exc)

    # README
    readme = f"""# call-analytics dataset

Экспорт всех звонков колл-центра «Пироги №1» с транскрипциями, диаризацией и LLM-оценками.
Создан: {stats['exported_at']}

## Содержимое

- `dataset.jsonl` — {stats['total']} записей, по одной строке на звонок.
- `audio/` — {stats['with_audio']} mp3-файлов (исходные имена сохранены — содержат дату/время/оператора/телефон клиента).
- `system_prompt.txt` — системный промпт версии {next(iter(stats['by_criteria_version']), 'v4')}, на котором обучалась референсная LLM (Gemini 3 Flash). Используется парами вместе с transcript для fine-tune.
- `stats.json` — сводка по полям, моделям, длительности.

## Структура одной JSONL-записи

```json
{{
  "file_id":         "<UUID>",
  "audio_relpath":   "audio/<имя_файла>.mp3",
  "audio_sha256":    "<hash>",
  "operator":        "Галина",
  "duration_sec":    206.4,
  "call_started_at": "2026-04-21T19:35:18",
  "uploaded_at":     "2026-05-07T10:30:35",
  "call_type":       "classical | short | no_answer | voicemail | internal",
  "transcript":      "[0:00] ОПЕРАТОР: ...\\n[0:03] КЛИЕНТ: ...",
  "diarization": {{
    "method":       "stereo | gpt-4o-transcribe | ...",
    "num_speakers": 2,
    "segments":     [...]
  }},
  "analysis": {{
    "scores":  {{"overall":91, "standard":92, "loyalty":80, "kindness":100}},
    "summary": "<2-3 предложения>",
    "quotes":  [{{"text":"...", "criterion":"standard", "sentiment":"positive"}}],
    "details": {{ ... 22 критерия, value/reason/timestamp ... }},
    "llm_model":         "gemini-3-flash-preview",
    "criteria_version":  "v4",
    "analyzed_at":       "..."
  }}
}}
```

## Краткая статистика

- Звонков всего: **{stats['total']}**
- С транскрипцией: **{stats['with_transcript']}**
- С анализом: **{stats['with_analysis']}**
- С аудио на диске: **{stats['with_audio']}**
- Аудио потеряно: **{stats['audio_missing']}**
- Суммарная длительность: **{stats['duration_hours_total']} часов**

По типу звонка: {json.dumps(stats['by_call_type'], ensure_ascii=False)}
По LLM-модели: {json.dumps(stats['by_llm_model'], ensure_ascii=False)}

## Важно про privacy

Имена файлов содержат:
- дату и время звонка
- ФИО оператора («Менеджер Галина»)
- номер телефона клиента (`79xxxxxxxxx`)

Транскрипты содержат имена клиентов (если они представились), адреса доставки, номера телефонов.
Если планируется публикация датасета — нужна анонимизация.

## Use cases

1. **Fine-tune LLM-аналитика** (например GigaChat-Lite или Qwen2.5-7B):
   - вход: `system_prompt.txt` + `transcript`
   - выход: `analysis.details` + `analysis.summary` + `analysis.quotes`
   Формат для HuggingFace SFT (chat-template):
   ```python
   {{"messages": [
     {{"role":"system","content":SYSTEM_PROMPT}},
     {{"role":"user","content":transcript}},
     {{"role":"assistant","content": json.dumps(analysis_subset)}}
   ]}}
   ```

2. **Fine-tune ASR** (например GigaAM):
   - вход: аудио из `audio/`
   - выход: текст из `diarization.segments` (но это машинная разметка — лучше иметь ground truth поверх).

3. **Fine-tune диаризатора**:
   - вход: аудио, выход: `diarization.segments`.
"""
    (out / "README.md").write_text(readme, encoding="utf-8")
    log.info("Wrote %s", out / "README.md")
    log.info("DONE. Output: %s", out)
    log.info("Stats: %s", json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
