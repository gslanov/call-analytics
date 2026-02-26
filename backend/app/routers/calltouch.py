from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import CallRecord, File
from app.services.calltouch_handler import process_webhook, parse_calltime

router = APIRouter(prefix="/calltouch", tags=["calltouch"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.api_route("/webhook", methods=["GET", "POST"])
async def calltouch_webhook(request: Request, db: Session = Depends(get_db)):
    import logging
    import json
    logger = logging.getLogger(__name__)

    # Support GET query params, POST form data, and POST JSON
    if request.method == "GET":
        call_data = dict(request.query_params)
    else:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            call_data = await request.json()
        else:
            form = await request.form()
            call_data = dict(form)

    # Log incoming webhook data for debugging - FULL DATA
    logger.info("Calltouch webhook received - RAW DATA: %s", json.dumps(call_data, ensure_ascii=False, default=str)[:500])

    if not call_data.get("id"):
        raise HTTPException(status_code=400, detail="Call ID is required")

    result = process_webhook(call_data)

    if result["status"] in ("success",):
        call_id = call_data["id"]
        existing = db.query(CallRecord).filter(CallRecord.calltouch_id == call_id).first()
        if not existing:
            call_timestamp = parse_calltime(call_data.get("calltime"))
            call_date = datetime.fromtimestamp(call_timestamp) if call_timestamp else None
            record = CallRecord(
                calltouch_id=call_id,
                callerphone=call_data.get("callerphone"),
                calledphone=call_data.get("calledphone"),
                operatorphone=call_data.get("operatorphone"),
                duration=int(call_data.get("duration") or 0) or None,
                order_id=call_data.get("order_id") or None,
                call_date=call_date,
                status=call_data.get("status"),
                has_recording=bool(call_data.get("record") == "1" or call_data.get("recordlink")),
                local_path=result.get("local_path"),
                raw_data=call_data,
            )
            db.add(record)
            db.commit()

    return result


@router.get("/metadata/{file_id}")
def get_calltouch_metadata(file_id: str, db: Session = Depends(get_db)):
    record = db.query(CallRecord).filter(CallRecord.file_id == file_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Calltouch metadata not found")
    return {
        "calltouch_id": record.calltouch_id,
        "callerphone": record.callerphone,
        "calledphone": record.calledphone,
        "operatorphone": record.operatorphone,
        "duration": record.duration,
        "order_id": record.order_id,
        "call_date": record.call_date,
        "status": record.status,
        "has_recording": record.has_recording,
        "local_path": record.local_path,
        "raw_data": record.raw_data,
        "created_at": record.created_at,
    }


@router.post("/sync")
def sync_calltouch(db: Session = Depends(get_db)):
    """Синхронизировать данные Calltouch с файлами в БД по номеру телефона."""
    updated = 0
    records = db.query(CallRecord).filter(CallRecord.file_id.is_(None)).all()
    for record in records:
        if not record.callerphone:
            continue
        file = (
            db.query(File)
            .filter(File.callerphone == record.callerphone)
            .order_by(File.created_at.desc())
            .first()
        )
        if file:
            record.file_id = file.id
            file.callerphone = record.callerphone
            file.calledphone = record.calledphone
            file.operatorphone = record.operatorphone
            file.duration = record.duration
            file.order_id = record.order_id
            updated += 1
    db.commit()
    return {"status": "ok", "updated": updated}


@router.get("/available-fields")
def get_available_json_fields(db: Session = Depends(get_db)):
    """Получить все доступные JSON ключи из raw_data записей Calltouch."""
    records = db.query(CallRecord.raw_data).filter(CallRecord.raw_data.isnot(None)).limit(100).all()

    all_fields = set()
    for record in records:
        if record[0]:
            # Собрать все ключи из raw_data рекурсивно
            def extract_keys(obj, prefix=""):
                if isinstance(obj, dict):
                    for key, value in obj.items():
                        full_key = f"{prefix}.{key}" if prefix else key
                        all_fields.add(full_key)
                        if isinstance(value, (dict, list)):
                            extract_keys(value, full_key)
                elif isinstance(obj, list) and obj:
                    if isinstance(obj[0], dict):
                        extract_keys(obj[0], prefix)

            extract_keys(record[0])

    return {"fields": sorted(list(all_fields))}


@router.get("/search-by-field")
def search_calltouch_by_field(
    field: str = Query(..., description="JSON field path (e.g., 'utm_source' or 'call.status')"),
    value: str = Query(..., description="Value to search for"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """Поиск записей Calltouch по любому полю из JSON."""
    records = db.query(CallRecord).filter(CallRecord.raw_data.isnot(None)).limit(limit).all()

    results = []
    for record in records:
        try:
            # Получить значение из nested JSON по пути field
            obj = record.raw_data
            for key in field.split('.'):
                if isinstance(obj, dict):
                    obj = obj.get(key)
                else:
                    break

            # Проверить совпадение значения (case-insensitive)
            if obj and str(obj).lower().find(value.lower()) >= 0:
                results.append({
                    "calltouch_id": record.calltouch_id,
                    "callerphone": record.callerphone,
                    "operatorphone": record.operatorphone,
                    "order_id": record.order_id,
                    "field": field,
                    "field_value": obj,
                    "raw_data": record.raw_data
                })
        except Exception:
            pass

    return {"results": results, "count": len(results)}
