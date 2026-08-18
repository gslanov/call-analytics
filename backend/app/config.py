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

    # OpenAI / Gemini / kie.ai / OpenRouter
    # Цепочка LLM-fallback (07.05.2026):
    #   1. gemini-direct (Google AI Studio, OpenAI-compat endpoint)
    #   2. openai-fallback (gpt-5-mini)
    #   3. openai-direct (gpt-5.4 — последняя надежда)
    # Старая kie-цепочка временно скрыта флагом kie_disabled — код оставлен,
    # включить обратно одним env: KIE_DISABLED=0.
    # openai_api_key всё равно нужен для whisper-1 + gpt-4o-transcribe (STT).
    openai_api_key: str = ""

    # Gemini direct (Google AI Studio, OpenAI-compat endpoint).
    # Получение ключа: https://aistudio.google.com/apikey
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    gemini_direct_model: str = "gemini-3-flash-preview"

    # OpenAI промежуточный fallback (между gemini и gpt-5.4).
    # gpt-5-mini с reasoning_effort=low и timeout=300 — качество ~75% точности
    # (тестили 08.05 на 5 звонках Лады, gpt-4o-mini давала 37% точности с
    # пропусками очевидных позитивов, gpt-5-nano 23% + галлюцинации).
    openai_fallback_model: str = "gpt-5-mini"

    # OpenRouter — оставлен на случай если когда-нибудь понадобится
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # kie.ai — временно скрыта (код сохранён, включить обратно: KIE_DISABLED=0)
    # У kie.ai endpoint нестандартный: модель зашита в URL пути
    # (https://api.kie.ai/<model>/v1/chat/completions).
    kie_disabled: bool = True
    kie_api_key: str = ""
    kie_primary_base_url: str = "https://api.kie.ai/gemini-3-flash/v1"
    kie_fallback_base_url: str = "https://api.kie.ai/gemini-3-pro/v1"
    kie_fallback2_base_url: str = "https://api.kie.ai/gpt-5-2/v1"
    llm_model: str = "gemini-3-flash"
    llm_fallback_model: str = "gemini-3-pro"
    llm_fallback2_model: str = "gpt-5-2"

    # Последний уровень — прямой OpenAI gpt-5.4 (как было до 01.05).
    openai_direct_model: str = "gpt-5.4"

    # ------------------------------------------------------------------
    # Транскрибация + диаризация: какой путь основной
    # ------------------------------------------------------------------
    # "audio_dialog" (основной с 18.08.2026) — ОДИН вызов мультимодальной
    #   модели: аудио → готовый диалог с ролями и таймкодами. ~$0.003/звонок.
    #   LLM-мерж при этом не нужен вообще (текст уже размечен и с пунктуацией).
    # "openai_quad" — исторический путь: gpt-4o-transcribe на микс + на левый
    #   канал + на правый канал, плюс whisper-1 ради таймкодов, плюс LLM-мерж.
    #   ~$0.034/звонок. Остаётся как fallback и как способ откатиться целиком.
    #
    # Откат на старое поведение: TRANSCRIPTION_MODE=openai_quad + restart.
    transcription_mode: str = "audio_dialog"

    # Цепочка аудио-моделей в порядке fallback, через запятую.
    # Формат элемента: "<канал>:<модель>", канал = gemini | openrouter.
    # Кандидаты без ключа в .env пропускаются молча.
    #
    # Замер 18.08 на 12 звонках РОП: 3.7-flash вдвое дешевле и точнее по
    # таймкодам (89% попаданий в 3 сек против 56%), 3-flash-preview чище по
    # тексту. Первый — основной, второй — страховка на случай проблем
    # конкретной модели (но не аккаунта — от этого спасает только openai_quad).
    audio_dialog_chain: str = "gemini:gemini-3.7-flash,gemini:gemini-3-flash-preview"

    # Если вся цепочка audio_dialog не смогла — прогнать звонок историческим
    # путём. Дороже, зато звонок не падает в failed. Gemini за лето ложился
    # трижды (06.05, 01.06, 18.08), так что выключать не стоит.
    audio_dialog_fallback_to_openai: bool = True

    audio_dialog_timeout: int = 300

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
