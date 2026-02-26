from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: str = "postgresql://callanalytics:password@localhost:5432/callanalytics"

    # OpenAI
    openai_api_key: str = ""

    # Whisper
    whisper_model: str = "large-v3"
    whisper_device: str = "cuda"
    whisper_language: str = "ru"

    # pyannote
    hf_token: str = ""

    # Limits
    max_file_size_mb: int = 500
    max_batch_size: int = 20
    min_duration_sec: int = 3
    max_duration_sec: int = 14400
    audio_retention_days: int = 7

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "http://localhost:5173"

    # Data paths
    uploads_dir: str = "/app/data/uploads"
    audio_dir: str = "/app/data/audio"
    mango_sftp_dir: str = "/app/data/mango_sftp/uploads"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str) -> str:
        return v

    def get_cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


settings = Settings()
