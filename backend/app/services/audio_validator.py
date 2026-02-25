"""Audio file validation service.

Validates: extension, size, MIME magic bytes, ffprobe integrity, duration, SHA-256 dedup.
"""

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import settings

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".webm"}

# Magic bytes: (offset, bytes)
MAGIC_SIGNATURES: dict[str, list[bytes]] = {
    ".mp3":  [b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"ID3"],
    ".wav":  [b"RIFF"],
    ".ogg":  [b"OggS"],
    ".flac": [b"fLaC"],
    ".m4a":  [b"\x00\x00\x00\x18ftypM4A", b"\x00\x00\x00\x20ftypM4A",
               b"\x00\x00\x00\x1cftypM4A", b"ftyp"],
    ".webm": [b"\x1a\x45\xdf\xa3"],
}


@dataclass
class ValidationResult:
    valid: bool
    error: str | None = None
    duration_sec: float | None = None
    channels: int | None = None
    file_hash: str | None = None


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_magic_bytes(content: bytes, ext: str) -> bool:
    """Check if file content matches known magic bytes for given extension."""
    signatures = MAGIC_SIGNATURES.get(ext, [])
    if not signatures:
        return True  # Unknown ext — already blocked by extension check
    for sig in signatures:
        # m4a "ftyp" can appear at offset 4
        if ext == ".m4a":
            if content[4:8] == b"ftyp":
                return True
        if content[: len(sig)] == sig:
            return True
    return False


def _probe_audio(file_path: Path) -> tuple[float | None, int | None, str | None]:
    """Run ffprobe and return (duration_sec, channels, error)."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(file_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None, None, "Файл не может быть декодирован"
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0) or 0)
        channels = None
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "audio":
                channels = stream.get("channels")
                break
        if duration <= 0:
            return None, channels, "Не удалось определить длительность файла"
        return duration, channels, None
    except subprocess.TimeoutExpired:
        return None, None, "Превышено время анализа файла"
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        return None, None, f"Ошибка анализа файла: {exc}"


def validate_audio_file(
    filename: str,
    content: bytes,
    *,
    existing_hashes: set[str] | None = None,
) -> ValidationResult:
    """Validate a single audio file.

    Args:
        filename: Original filename (used for extension check).
        content: Raw file bytes.
        existing_hashes: Set of SHA-256 hashes already in DB (for dedup detection).

    Returns:
        ValidationResult with valid=True and populated fields, or valid=False with error.
    """
    ext = Path(filename).suffix.lower()

    # 1. Extension whitelist
    if ext not in ALLOWED_EXTENSIONS:
        return ValidationResult(
            valid=False,
            error=f"Формат не поддерживается. Разрешены: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # 2. Size limit
    size = len(content)
    if size > settings.max_file_size_bytes:
        return ValidationResult(
            valid=False,
            error=f"Размер файла ({size // 1024 // 1024} MB) превышает лимит {settings.max_file_size_mb} MB",
        )

    if size == 0:
        return ValidationResult(valid=False, error="Файл пустой")

    # 3. MIME / magic bytes
    if not _check_magic_bytes(content, ext):
        return ValidationResult(
            valid=False,
            error=f"Содержимое файла не соответствует расширению {ext}",
        )

    # 4. SHA-256 (compute before ffprobe — fast, no disk I/O)
    file_hash = compute_sha256(content)

    # 5. ffprobe: write to temp file, probe, then clean up
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        duration, channels, probe_error = _probe_audio(tmp_path)
    finally:
        os.unlink(tmp_path)

    if probe_error:
        return ValidationResult(valid=False, error=probe_error)

    # 6. Duration limits
    if duration < settings.min_duration_sec:
        return ValidationResult(
            valid=False,
            error=f"Длительность ({duration:.1f} сек) меньше минимума {settings.min_duration_sec} сек",
        )
    if duration > settings.max_duration_sec:
        return ValidationResult(
            valid=False,
            error=(
                f"Длительность ({duration / 3600:.1f} ч) превышает максимум "
                f"{settings.max_duration_sec // 3600} ч"
            ),
        )

    # 7. Deduplication check (caller passes existing hashes)
    if existing_hashes and file_hash in existing_hashes:
        return ValidationResult(
            valid=False,
            error=f"duplicate:{file_hash}",  # Special marker — router handles it
            duration_sec=duration,
            channels=channels,
            file_hash=file_hash,
        )

    return ValidationResult(
        valid=True,
        duration_sec=duration,
        channels=channels,
        file_hash=file_hash,
    )
