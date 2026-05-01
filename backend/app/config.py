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

    # OpenAI / OpenRouter
    # Если openrouter_api_key задан — primary LLM-клиент идёт через OpenRouter.
    # openai_api_key всё равно нужен для whisper-1 + gpt-4o-transcribe (STT).
    openai_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "gpt-5.4"

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
    audio_retention_days: int = 0  # 0 = never delete audio (accumulating for STT/TTS training)

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "http://localhost:5173"

    # Data paths
    uploads_dir: str = "/app/data/uploads"
    audio_dir: str = "/app/data/audio"
    mango_sftp_dir: str = "/app/data/mango_sftp/uploads"

    # Calltouch
    calltouch_site_id: str = ""
    calltouch_api_key: str = ""
    calltouch_call_records_path: str = "/app/data/calltouch_records"
    calltouch_webhook_secret: str = ""

    # Auth (app-level Basic Auth — defense in depth поверх nginx)
    basic_auth_user: str = ""
    basic_auth_password: str = ""

    # Pipeline concurrency — сколько файлов воркер обрабатывает параллельно.
    # Path B (один процесс, asyncio.gather + Semaphore). True parallelism через
    # несколько процессов потребует Postgres durable queue (фаза 5+).
    pipeline_concurrency: int = 4

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
