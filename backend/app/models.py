import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Operator(Base):
    __tablename__ = "operators"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    files: Mapped[list["File"]] = relationship("File", back_populates="operator")


class File(Base):
    __tablename__ = "files"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    operator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("operators.id"), nullable=True
    )
    original_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    audio_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(32), server_default="queued")
    stage: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    progress: Mapped[int] = mapped_column(SmallInteger, server_default="0")   # 0-100%
    retry_count: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    operator: Mapped["Operator | None"] = relationship("Operator", back_populates="files")
    transcription: Mapped["Transcription | None"] = relationship(
        "Transcription", back_populates="file", uselist=False
    )
    diarization: Mapped["Diarization | None"] = relationship(
        "Diarization", back_populates="file", uselist=False
    )
    analysis: Mapped["Analysis | None"] = relationship(
        "Analysis", back_populates="file", uselist=False
    )

    __table_args__ = (
        Index("idx_files_operator", "operator_id"),
        Index("idx_files_status", "status"),
        Index("idx_files_created", "created_at"),
        Index("idx_files_hash", "file_hash"),
    )


class Transcription(Base):
    __tablename__ = "transcriptions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), unique=True
    )
    full_text: Mapped[str] = mapped_column(Text, nullable=False)
    word_timestamps: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    language: Mapped[str] = mapped_column(String(10), server_default="ru")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    file: Mapped["File"] = relationship("File", back_populates="transcription")


class Diarization(Base):
    __tablename__ = "diarizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), unique=True
    )
    segments: Mapped[list] = mapped_column(JSONB, nullable=False)
    method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    num_speakers: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    file: Mapped["File"] = relationship("File", back_populates="diarization")


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), unique=True
    )
    standard: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    loyalty: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    kindness: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    overall: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    quotes: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    file: Mapped["File"] = relationship("File", back_populates="analysis")

    __table_args__ = (
        CheckConstraint("standard BETWEEN 0 AND 100", name="ck_analyses_standard"),
        CheckConstraint("loyalty BETWEEN 0 AND 100", name="ck_analyses_loyalty"),
        CheckConstraint("kindness BETWEEN 0 AND 100", name="ck_analyses_kindness"),
        CheckConstraint("overall BETWEEN 0 AND 100", name="ck_analyses_overall"),
        Index("idx_analyses_standard", "standard"),
        Index("idx_analyses_loyalty", "loyalty"),
        Index("idx_analyses_kindness", "kindness"),
        Index("idx_analyses_overall", "overall"),
    )
