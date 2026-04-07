"""Settings API — manage FTP credentials and other config via UI."""

import logging

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AppSetting

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


class MangoFtpSettings(BaseModel):
    host: str = ""
    user: str = ""
    password: str = ""


class MangoFtpSettingsResponse(BaseModel):
    host: str = ""
    user: str = ""
    has_password: bool = False


# --- Encryption helpers ---

def _get_or_create_key(db: Session) -> bytes:
    """Get or create Fernet encryption key, stored in app_settings."""
    row = db.get(AppSetting, "_encryption_key")
    if row:
        return row.value.encode()
    key = Fernet.generate_key()
    db.add(AppSetting(key="_encryption_key", value=key.decode()))
    db.commit()
    return key


def _encrypt(db: Session, plaintext: str) -> str:
    key = _get_or_create_key(db)
    return Fernet(key).encrypt(plaintext.encode()).decode()


def _decrypt(db: Session, ciphertext: str) -> str:
    try:
        key = _get_or_create_key(db)
        return Fernet(key).decrypt(ciphertext.encode()).decode()
    except Exception:
        logger.warning("Failed to decrypt value — key may have changed")
        return ""


# --- DB helpers ---

def _get_setting(db: Session, key: str) -> str:
    row = db.get(AppSetting, key)
    return row.value if row else ""


def _set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))


def _get_password(db: Session) -> str:
    """Read and decrypt FTP password."""
    encrypted = _get_setting(db, "mango_ftp_password")
    if not encrypted:
        return ""
    return _decrypt(db, encrypted)


def _set_password(db: Session, plaintext: str) -> None:
    """Encrypt and save FTP password."""
    encrypted = _encrypt(db, plaintext)
    _set_setting(db, "mango_ftp_password", encrypted)


# --- Endpoints ---

@router.get("/mango-ftp", response_model=MangoFtpSettingsResponse)
def get_mango_ftp(db: Session = Depends(get_db)):
    """Get Mango FTP settings (password masked)."""
    return MangoFtpSettingsResponse(
        host=_get_setting(db, "mango_ftp_host"),
        user=_get_setting(db, "mango_ftp_user"),
        has_password=bool(_get_setting(db, "mango_ftp_password")),
    )


@router.put("/mango-ftp")
def save_mango_ftp(
    data: MangoFtpSettings,
    db: Session = Depends(get_db),
):
    """Save Mango FTP settings."""
    _set_setting(db, "mango_ftp_host", data.host.strip())
    _set_setting(db, "mango_ftp_user", data.user.strip())
    if data.password:  # don't overwrite with empty
        _set_password(db, data.password.strip())
    db.commit()
    return {"status": "ok"}


@router.post("/mango-ftp/test")
def test_mango_ftp(db: Session = Depends(get_db)):
    """Test FTP connection with saved credentials."""
    import ftplib

    host = _get_setting(db, "mango_ftp_host")
    user = _get_setting(db, "mango_ftp_user")
    password = _get_password(db)

    if not host:
        return {"status": "error", "message": "FTP хост не указан"}

    try:
        ftp = ftplib.FTP(host, timeout=10)
        ftp.login(user, password)
        files = ftp.nlst()
        ftp.quit()
        return {
            "status": "ok",
            "message": f"Подключение успешно. Файлов на сервере: {len(files)}",
        }
    except Exception as exc:
        return {"status": "error", "message": f"Ошибка подключения: {exc}"}
