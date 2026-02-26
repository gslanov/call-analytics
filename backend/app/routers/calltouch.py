from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import CallRecord, File
from app.services.calltouch_handler import process_webhook

router = APIRouter(prefix="/calltouch", tags=["calltouch"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/webhook")
async def calltouch_webhook(request: Request, db: Session = Depends(get_db)):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        call_data = await request.json()
    else:
        form = await request.form()
        call_data = dict(form)

    if not call_data.get("id"):
        raise HTTPException(status_code=400, detail="Call ID is required")

    result = process_webhook(call_data)

    if result["status"] in ("success",):
        call_id = call_data["id"]
        existing = db.query(CallRecord).filter(CallRecord.calltouch_id == call_id).first()
        if not existing:
            call_timestamp = int(call_data.get("calltime") or 0)
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
