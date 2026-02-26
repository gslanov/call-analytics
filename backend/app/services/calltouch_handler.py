"""
Calltouch Handler — адаптированный под FastAPI backend (без Flask).
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import requests

from app.config import settings

logger = logging.getLogger(__name__)

CALLTOUCH_API_URL = "https://api.calltouch.ru/calls-service/RestAPI"


def parse_calltime(calltime_value) -> int:
    """Parse calltime from various formats (unix timestamp, string, etc)."""
    if not calltime_value:
        return 0
    try:
        # If it's already an int/float
        if isinstance(calltime_value, (int, float)):
            return int(calltime_value)
        # Try to convert string to int
        if isinstance(calltime_value, str):
            return int(calltime_value)
    except (ValueError, TypeError) as e:
        logger.warning("Could not parse calltime '%s': %s", calltime_value, e)
    return 0


def get_call_recording(call_id: str) -> tuple[bytes | None, str | None]:
    url = f"{CALLTOUCH_API_URL}/{settings.calltouch_site_id}/calls-diary/calls/{call_id}/download"
    params = {"clientApiId": settings.calltouch_api_key}
    try:
        response = requests.get(url, params=params, timeout=30)
        if response.status_code == 200:
            return response.content, f"{call_id}.mp3"
        logger.error("Calltouch API %s: %s", response.status_code, response.text[:200])
        return None, None
    except Exception as e:
        logger.error("Error downloading recording %s: %s", call_id, e)
        return None, None


def save_call_to_disk(call_id: str, call_data: dict, recording_content: bytes | None = None) -> str | None:
    """Сохраняет метаданные и запись на диск. Возвращает путь к директории или None."""
    try:
        call_timestamp = parse_calltime(call_data.get("calltime"))
        call_dt = datetime.fromtimestamp(call_timestamp) if call_timestamp else datetime.now()

        local_path = Path(settings.calltouch_call_records_path) / call_dt.strftime("%Y/%m/%d") / f"call_{call_id}"
        local_path.mkdir(parents=True, exist_ok=True)

        metadata = {
            "call": {
                "id": call_id,
                "caller_phone": call_data.get("callerphone", ""),
                "called_phone": call_data.get("calledphone", ""),
                "operator_phone": call_data.get("operatorphone", ""),
                "call_date": call_dt.isoformat(),
                "duration_seconds": call_data.get("duration", 0),
                "status": call_data.get("status", "unknown"),
                "has_recording": bool(recording_content),
                "source": call_data.get("source", ""),
                "medium": call_data.get("medium", ""),
                "utm_source": call_data.get("utm_source", ""),
                "utm_medium": call_data.get("utm_medium", ""),
                "utm_campaign": call_data.get("utm_campaign", ""),
                "utm_content": call_data.get("utm_content", ""),
                "utm_term": call_data.get("utm_term", ""),
                "page_url": call_data.get("page_url", ""),
                "referrer": call_data.get("referrer", ""),
                "city": call_data.get("city", ""),
                "country": call_data.get("country", ""),
                "ip": call_data.get("ip", ""),
                "tags": call_data.get("tag_id", []),
                "comment": call_data.get("comment", ""),
                "manager_id": call_data.get("manager_id", ""),
                "rating": call_data.get("rating", ""),
                "visitor_name": call_data.get("visitor_name", ""),
                "visitor_email": call_data.get("visitor_email", ""),
                "session_id": call_data.get("session_id", ""),
            },
            "order": {
                "id": call_data.get("order_id", ""),
                "number": call_data.get("order_number", ""),
                "price": call_data.get("order_price", ""),
                "status": call_data.get("order_status", ""),
                "date": call_data.get("order_date", ""),
            },
            "utm": {
                "source": call_data.get("utm_source", ""),
                "medium": call_data.get("utm_medium", ""),
                "campaign": call_data.get("utm_campaign", ""),
                "content": call_data.get("utm_content", ""),
                "term": call_data.get("utm_term", ""),
            },
            "ads": {
                "gclid": call_data.get("gclid", ""),
                "yclid": call_data.get("yclid", ""),
                "fbp": call_data.get("facebook_pixel_id", ""),
                "keyword": call_data.get("keyword", ""),
            },
            "upload_info": {
                "uploaded_at": datetime.now().isoformat(),
                "recording_file": "recording.mp3" if recording_content else None,
                "local_path": str(local_path),
            },
        }

        (local_path / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        if recording_content:
            (local_path / "recording.mp3").write_bytes(recording_content)

        manifest = {
            "status": "success",
            "call_id": call_id,
            "local_path": str(local_path),
            "timestamp": datetime.now().isoformat(),
            "files": {
                "metadata": "metadata.json",
                "recording": "recording.mp3" if recording_content else None,
            },
        }
        (local_path / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        logger.info("Call %s saved to %s", call_id, local_path)
        return str(local_path)

    except Exception as e:
        logger.error("Error saving call %s: %s", call_id, e)
        return None


def process_webhook(call_data: dict) -> dict:
    """
    Основная логика обработки webhook от Calltouch.
    Возвращает словарь с результатом.
    """
    call_id = call_data.get("id", "")
    if not call_id:
        return {"status": "ignored", "reason": "no_call_id"}

    caller_phone = call_data.get("callerphone", "")
    has_recording = call_data.get("record", "0")

    recording_content = None
    if has_recording == "1" or call_data.get("recordlink"):
        recording_content, _ = get_call_recording(call_id)

    local_path = save_call_to_disk(call_id, call_data, recording_content)

    if local_path:
        return {
            "status": "success",
            "call_id": call_id,
            "phone": caller_phone,
            "order_id": call_data.get("order_id", ""),
            "local_path": local_path,
        }
    return {"status": "error", "call_id": call_id, "message": "Failed to save call"}
