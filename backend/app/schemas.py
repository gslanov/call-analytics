import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# --- Upload ---

class AcceptedFile(BaseModel):
    file_id: str
    original_name: str
    is_duplicate: bool = False


class UploadResponse(BaseModel):
    # accepted — новый поле, фронт мапит по original_name (нет index drift при partial success)
    accepted: list[AcceptedFile] = []
    # file_ids оставлен для обратной совместимости со старым фронтом
    file_ids: list[str]
    operator: str
    status: str
    total_files: int
    validation_errors: list["ValidationError"] = []


class ValidationError(BaseModel):
    file: str
    error: str


class UploadValidationErrorResponse(BaseModel):
    error: str = "validation_error"
    details: list[ValidationError]


# --- File ---

class FileSchema(BaseModel):
    file_id: uuid.UUID
    original_name: str
    operator_name: str | None
    file_size: int
    duration_sec: float | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Operator ---

class OperatorSchema(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime

    model_config = {"from_attributes": True}


class OperatorDetailSchema(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    file_count: int = 0


# --- Analysis ---

class AnalysisSchema(BaseModel):
    standard: int
    loyalty: int
    kindness: int
    overall: int
    summary: str
    quotes: list | None
    criteria_details: dict | None = None
    llm_model: str | None
    rejected: bool = False
    rejection_reason: str | None = None
    rejected_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Diarization ---

class DiarizationSegmentSchema(BaseModel):
    speaker: str
    start: float
    end: float
    text: str


# --- Results list ---

class ResultListItem(BaseModel):
    file_id: uuid.UUID
    original_name: str
    operator_id: uuid.UUID | None
    operator_name: str | None
    file_size: int
    duration_sec: float | None
    status: str
    stage: int
    progress: int
    created_at: datetime
    analysis: AnalysisSchema | None
    diarization_method: str | None = None  # channel_split / llm_diarization / pyannote
    call_date: str | None = None      # "04.04" — из имени файла
    call_time: str | None = None      # "19:51" — из имени файла
    caller_phone: str | None = None   # "**3351" — последние 4 цифры
    # Markers (informational flags, do NOT affect scores)
    order_confirmation: bool | None = None   # звонок для подтверждения заказа
    prepayment_20k: bool | None = None       # заказ ≥20k → озвучена предоплата


class PaginatedResults(BaseModel):
    items: list[ResultListItem]
    total: int
    page: int
    limit: int
    pages: int


# --- Result detail ---

class TranscriptionDetail(BaseModel):
    full_text: str
    word_timestamps: list | None = None


class DiarizationDetail(BaseModel):
    method: str | None
    confidence: float | None
    num_speakers: int | None = None
    segments: list[DiarizationSegmentSchema]


class ResultDetail(BaseModel):
    file_id: uuid.UUID
    original_name: str
    operator_id: uuid.UUID | None
    operator_name: str | None
    file_size: int
    duration_sec: float | None
    status: str
    stage: int
    progress: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    # Transcription (nested — фронт ожидает transcription.full_text)
    full_text: str | None = None
    transcription: TranscriptionDetail | None = None
    # Diarization (nested, с num_speakers)
    diarization: DiarizationDetail | None = None
    # Analysis
    analysis: AnalysisSchema | None = None


# --- Health ---

class ServiceHealth(BaseModel):
    ok: bool
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str  # "ok" | "degraded" | "error"
    database: ServiceHealth
    whisper: ServiceHealth
    llm: ServiceHealth
    disk: ServiceHealth
    queue_length: int
    current_file: str | None
