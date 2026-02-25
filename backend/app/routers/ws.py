"""WebSocket endpoint — live progress updates for file processing.

Protocol:
  Client → Server:  {"file_id": "uuid"}        — subscribe to file progress
  Server → Client:  {"type": "progress", ...}   — status/progress update
  Server → Client:  {"type": "complete", ...}   — processing finished
  Server → Client:  {"type": "error", ...}      — processing failed
  Server → Client:  {"type": "pong"}            — keepalive response to ping
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

STAGE_NAMES = {
    0: "Ожидание",
    1: "Транскрибация",
    2: "Диаризация",
    3: "Анализ",
    4: "Готово",
}

# Inactivity timeout: 5 minutes
WS_TIMEOUT_SEC = 300


class WebSocketManager:
    """Manages active WebSocket connections and file subscriptions."""

    _instance: "WebSocketManager | None" = None

    def __init__(self) -> None:
        # Map: file_id (str) → set of WebSocket connections
        self._subscriptions: dict[str, set[WebSocket]] = {}
        # Map: ws → set of subscribed file_ids (for cleanup on disconnect)
        self._ws_files: dict[int, set[str]] = {}  # id(ws) → file_ids

    @classmethod
    def get_instance(cls) -> "WebSocketManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self, ws: WebSocket) -> None:
        self._ws_files[id(ws)] = set()
        logger.debug("WS connected: %s (total: %d)", id(ws), len(self._ws_files))

    def disconnect(self, ws: WebSocket) -> None:
        fids = self._ws_files.pop(id(ws), set())
        for fid in fids:
            self._subscriptions.get(fid, set()).discard(ws)
            if not self._subscriptions.get(fid):
                self._subscriptions.pop(fid, None)
        logger.debug("WS disconnected: %s, unsubscribed from %d file(s)", id(ws), len(fids))

    def subscribe(self, ws: WebSocket, file_id: str) -> None:
        if file_id not in self._subscriptions:
            self._subscriptions[file_id] = set()
        self._subscriptions[file_id].add(ws)
        self._ws_files.setdefault(id(ws), set()).add(file_id)
        logger.info("WS %s subscribed to file %s", id(ws), file_id)

    # ------------------------------------------------------------------
    # Broadcasting
    # ------------------------------------------------------------------

    async def broadcast_progress(
        self,
        file_id: str,
        status: str,
        progress: int,
        stage: int,
    ) -> None:
        """Send progress update to all clients subscribed to file_id."""
        subscribers = self._subscriptions.get(file_id, set()).copy()
        if not subscribers:
            return

        msg_type = "complete" if status == "done" else ("error" if status == "failed" else "progress")
        payload: dict[str, Any] = {
            "type": msg_type,
            "file_id": file_id,
            "status": status,
            "progress": progress,
            "stage": stage,
            "stage_name": STAGE_NAMES.get(stage, ""),
        }
        data = json.dumps(payload, ensure_ascii=False)

        dead: list[WebSocket] = []
        for ws in subscribers:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self.disconnect(ws)

    async def send_error(self, ws: WebSocket, file_id: str, error: str) -> None:
        try:
            await ws.send_text(json.dumps({
                "type": "error",
                "file_id": file_id,
                "error": error,
            }, ensure_ascii=False))
        except Exception:
            pass

    @property
    def connection_count(self) -> int:
        return len(self._ws_files)


# Module-level singleton (imported by pipeline.py)
ws_manager = WebSocketManager.get_instance()


# ------------------------------------------------------------------
# FastAPI endpoint
# ------------------------------------------------------------------

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    ws_manager.connect(websocket)
    logger.info("WebSocket connection accepted (total: %d)", ws_manager.connection_count)

    try:
        while True:
            try:
                # Wait for client message with inactivity timeout
                raw = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=WS_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                logger.info("WS inactivity timeout — closing")
                await websocket.close(code=1001, reason="Inactivity timeout")
                break

            # Parse client message
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "error": "Invalid JSON",
                }))
                continue

            # Keepalive ping
            if data.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            # Subscribe to file_id
            file_id_raw = data.get("file_id")
            if not file_id_raw:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "error": "Missing file_id",
                }))
                continue

            try:
                file_id = str(uuid.UUID(str(file_id_raw)))
            except ValueError:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "error": f"Invalid file_id: {file_id_raw}",
                }))
                continue

            ws_manager.subscribe(websocket, file_id)

            # Immediately send current status from DB
            await _send_current_status(websocket, file_id)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected gracefully")
    except Exception as exc:
        logger.error("WebSocket error: %s", exc)
    finally:
        ws_manager.disconnect(websocket)


async def _send_current_status(ws: WebSocket, file_id: str) -> None:
    """Send the current DB status immediately upon subscription."""
    from app.database import SessionLocal
    from app.models import File

    db = SessionLocal()
    try:
        try:
            fid = uuid.UUID(file_id)
        except ValueError:
            return
        db_file = db.get(File, fid)
        if db_file is None:
            await ws_manager.send_error(ws, file_id, "File not found")
            return

        stage = db_file.stage or 0
        msg_type = (
            "complete" if db_file.status == "done"
            else "error" if db_file.status == "failed"
            else "progress"
        )
        payload: dict[str, Any] = {
            "type": msg_type,
            "file_id": file_id,
            "status": db_file.status,
            "progress": db_file.progress or 0,
            "stage": stage,
            "stage_name": STAGE_NAMES.get(stage, ""),
        }
        if db_file.status == "failed" and db_file.error_message:
            payload["error"] = db_file.error_message
        await ws.send_text(json.dumps(payload, ensure_ascii=False))
    except Exception as exc:
        logger.error("Error sending current status for %s: %s", file_id, exc)
    finally:
        db.close()
