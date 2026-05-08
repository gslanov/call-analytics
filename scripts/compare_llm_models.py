"""Сравнивает новую модель с уже посчитанным анализом по БД.

Берёт N последних звонков с заданной reference-моделью (например, тех что
сегодня обработал gpt-4o-mini), прогоняет их транскрипты через указанную
кандидат-модель напрямую (минуя цепочку fallback), и печатает таблицу
сравнения по 4 метрикам (overall/standard/loyalty/kindness) + время.

НЕ меняет ничего в БД. НЕ трогает прод-pipeline. Безопасно гонять
параллельно с обработкой свежих звонков.

Запуск (внутри backend-контейнера):
    docker exec call-analytics-backend python scripts/compare_llm_models.py \\
        --candidate gpt-4.1-mini \\
        --reference gpt-4o-mini \\
        --limit 10
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from typing import Any

from openai import OpenAI
from sqlalchemy import create_engine, text

from app.services.llm_service import (
    LLMService,
    SYSTEM_PROMPT,
    STRICT_SYSTEM_PROMPT,
    CRITERIA_SCHEMA,
    _compute_group_score,
    _apply_dependencies,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("compare")


def fetch_calls(reference_model: str, limit: int) -> list[dict[str, Any]]:
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                f.id::text          AS file_id,
                f.original_name     AS name,
                f.duration_sec      AS duration,
                t.full_text         AS transcript,
                a.overall           AS old_overall,
                a.standard          AS old_standard,
                a.loyalty           AS old_loyalty,
                a.kindness          AS old_kindness,
                a.llm_model         AS old_model
            FROM analyses a
            JOIN files f         ON f.id = a.file_id
            JOIN transcriptions t ON t.file_id = f.id
            WHERE a.llm_model = :ref
              AND f.call_type = 'classical'
              AND f.status = 'done'
              AND length(t.full_text) > 200
            ORDER BY a.created_at DESC
            LIMIT :lim
        """), {"ref": reference_model, "lim": limit}).mappings().all()
        return [dict(r) for r in rows]


def call_candidate(client: OpenAI, model: str, transcript: str, reasoning: str = "low", provider: str = "openai") -> tuple[dict, float]:
    t0 = time.time()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": f"=== Полный диалог (с таймстемпами и спикерами) ===\n{transcript.strip()}"},
    ]
    kwargs: dict[str, Any] = dict(model=model, messages=messages, timeout=300)
    if provider == "gemini":
        kwargs["reasoning_effort"] = reasoning
        kwargs["temperature"] = 0
    elif model.startswith("gpt-5"):
        kwargs["reasoning_effort"] = reasoning
    else:
        kwargs["temperature"] = 0
    resp = client.chat.completions.create(**kwargs)
    elapsed = time.time() - t0
    content = (resp.choices[0].message.content or "").strip()
    if content.startswith("```"):
        lines = content.splitlines()
        content = "\n".join(l for l in lines if not l.strip().startswith("```")).strip()
    return json.loads(content), elapsed


def parse_scores(data: dict) -> dict[str, int | None]:
    details = data.get("details") or {}
    out = {}
    for group in ("standard", "loyalty", "kindness"):
        gd = details.get(group) or {}
        validated = {}
        for key in CRITERIA_SCHEMA[group]:
            raw = gd.get(key)
            if isinstance(raw, dict):
                v = raw.get("value")
            else:
                v = raw
            validated[key] = v if isinstance(v, bool) or v is None else None
        details[group] = validated
    _apply_dependencies(details)
    out["standard"] = _compute_group_score(details["standard"])
    out["loyalty"] = _compute_group_score(details["loyalty"])
    out["kindness"] = _compute_group_score(details["kindness"])
    out["overall"] = round(out["standard"] * 0.4 + out["loyalty"] * 0.3 + out["kindness"] * 0.3)
    return out


def fetch_old_details(file_ids: list[str]) -> dict[str, dict]:
    """Тянет criteria_details существующих analyses из БД, чтобы сравнивать
    не только числа но и сами critic reasons по каждому критерию."""
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT file_id::text AS fid, criteria_details, summary
            FROM analyses
            WHERE file_id::text = ANY(:ids)
        """), {"ids": file_ids}).mappings().all()
    return {r["fid"]: {"details": r["criteria_details"], "summary": r["summary"]} for r in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True, help="model to test (e.g. gpt-4.1-mini)")
    ap.add_argument("--reference", default="gpt-4o-mini", help="model to compare against")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--dump", default=None, help="path to save raw JSON dump")
    ap.add_argument("--reasoning", default="low", help="reasoning_effort for gpt-5*/gemini (minimal/low/medium/high)")
    ap.add_argument("--provider", default="openai", choices=("openai", "gemini"), help="OpenAI direct or Gemini via base_url")
    args = ap.parse_args()

    calls = fetch_calls(args.reference, args.limit)
    if not calls:
        print(f"No calls found with llm_model={args.reference}")
        return

    old_details = fetch_old_details([c["file_id"] for c in calls])

    print(f"Comparing {args.candidate} vs {args.reference} on {len(calls)} calls\n")

    if args.provider == "gemini":
        client = OpenAI(
            api_key=os.environ["GEMINI_API_KEY"],
            base_url=os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"),
        )
    else:
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    rows = []
    times: list[float] = []
    dump: list[dict] = []
    for i, c in enumerate(calls, 1):
        record: dict[str, Any] = {
            "n": i,
            "file_id": c["file_id"],
            "name": c["name"],
            "duration_sec": float(c["duration"]),
            "transcript": c["transcript"],
            "reference": {
                "model": args.reference,
                "overall": c["old_overall"],
                "standard": c["old_standard"],
                "loyalty": c["old_loyalty"],
                "kindness": c["old_kindness"],
                "details": old_details.get(c["file_id"], {}).get("details"),
                "summary": old_details.get(c["file_id"], {}).get("summary"),
            },
        }
        try:
            data, elapsed = call_candidate(client, args.candidate, c["transcript"], reasoning=args.reasoning, provider=args.provider)
            new = parse_scores(data)
            times.append(elapsed)
            record["candidate"] = {
                "model": args.candidate,
                "overall": new["overall"],
                "standard": new["standard"],
                "loyalty": new["loyalty"],
                "kindness": new["kindness"],
                "raw": data,
                "elapsed_sec": elapsed,
            }
            rows.append({
                "n": i,
                "name": c["name"][:50],
                "dur": f"{c['duration']:.0f}s",
                "old": f"{c['old_overall']}/{c['old_standard']}/{c['old_loyalty']}/{c['old_kindness']}",
                "new": f"{new['overall']}/{new['standard']}/{new['loyalty']}/{new['kindness']}",
                "diff": new["overall"] - c["old_overall"],
                "t":   f"{elapsed:.1f}s",
            })
            print(f"  [{i}/{len(calls)}] {c['name'][:60]} done in {elapsed:.1f}s")
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            record["candidate"] = {"model": args.candidate, "error": err}
            print(f"  [{i}/{len(calls)}] {c['name'][:60]} FAILED: {err}")
            rows.append({"n": i, "name": c["name"][:50], "dur": f"{c['duration']:.0f}s", "old": "?", "new": "ERR", "diff": 0, "t": "-"})
        dump.append(record)

    print()
    print(f"REFERENCE = {args.reference}    CANDIDATE = {args.candidate}")
    print(f"{'#':<3} {'name':<52} {'dur':>5} {'REF ovr/s/l/k':<15} {'CAND ovr/s/l/k':<15} {'Δovr':>5} {'time':>6}")
    print("-" * 120)
    for r in rows:
        print(f"{r['n']:<3} {r['name']:<52} {r['dur']:>5} {r['old']:<15} {r['new']:<15} {r['diff']:>+5} {r['t']:>6}")

    print()
    diffs = [r["diff"] for r in rows if r["new"] != "ERR"]
    if diffs:
        print(f"avg Δoverall: {sum(diffs)/len(diffs):+.1f} pts | range [{min(diffs):+d}, {max(diffs):+d}]")
    if times:
        print(f"avg time: {sum(times)/len(times):.1f}s | min {min(times):.1f}s | max {max(times):.1f}s")

    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as f:
            json.dump(dump, f, ensure_ascii=False, indent=2, default=str)
        print(f"\nDump saved to {args.dump}")


if __name__ == "__main__":
    main()
