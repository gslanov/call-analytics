# Conventions — Call Analytics

> Создано Vision Team: 2026-02-25

---

## Naming Conventions

### Файлы и папки

| Контекст | Стиль | Примеры |
|----------|-------|---------|
| Компоненты React | PascalCase | `UploadZone.tsx`, `ResultsTable.tsx`, `AudioPlayer.tsx` |
| Hooks React | camelCase с `use` | `useWebSocket.ts`, `useUpload.ts`, `useResults.ts` |
| Python модули | snake_case | `whisper_service.py`, `diarization.py`, `llm_service.py` |
| API маршруты (routers) | snake_case | `upload.py`, `results.py`, `operators.py` |
| SQL миграции (Alembic) | auto-generated | `001_initial_schema.py` |
| CSS/стили | TailwindCSS utility classes | Без отдельных CSS файлов |

### Переменные и функции

| Язык | Стиль | Примеры |
|------|-------|---------|
| Python (функции) | snake_case | `process_audio_file()`, `get_transcription()`, `run_diarization()` |
| Python (классы) | PascalCase | `WhisperService`, `PipelineOrchestrator` |
| TypeScript (функции) | camelCase | `handleUpload()`, `setProcessingStatus()`, `formatScore()` |
| TypeScript (компоненты) | PascalCase | `<UploadZone />`, `<ResultsTable />` |
| TypeScript (типы/интерфейсы) | PascalCase | `AnalysisResult`, `FileStatus`, `FilterParams` |
| Константы (оба языка) | UPPER_SNAKE_CASE | `MAX_FILE_SIZE`, `RETRY_ATTEMPTS`, `API_BASE_URL` |
| Env-переменные | UPPER_SNAKE_CASE | `OPENAI_API_KEY`, `DATABASE_URL`, `WHISPER_MODEL` |

### API Endpoints

| Стиль | Правило |
|-------|---------|
| Базовый путь | `/api/v1/` |
| Ресурсы | kebab-case, множественное число: `/api/v1/results`, `/api/v1/operators` |
| Параметры query | snake_case: `?date_from=&score_min=&operator_id=` |
| JSON ключи (response) | snake_case: `file_id`, `operator_name`, `created_at` |

---

## Реестр компонентов (Frontend)

| Компонент | Файл | Назначение | Ключевые Props |
|-----------|------|-----------|---------------|
| `UploadZone` | `UploadZone.tsx` | Drag-drop зона для загрузки файлов | `onFilesSelected`, `disabled`, `acceptedFormats` |
| `FileList` | `FileList.tsx` | Список выбранных файлов + поле оператора | `files`, `onRemove`, `onOperatorChange`, `onAnalyze` |
| `ResultsTable` | `ResultsTable.tsx` | Таблица результатов (expandable rows) | `results`, `onRowClick`, `sortBy`, `sortDir` |
| `ResultRow` | `ResultRow.tsx` | Строка таблицы с expand/collapse | `result`, `expanded`, `onToggle` |
| `AnalysisDetail` | `AnalysisDetail.tsx` | Детальный отчёт одного файла | `analysis`, `onBack` |
| `ScoreCard` | `ScoreCard.tsx` | Карточка оценки с процентом и цветом | `label`, `score`, `size` |
| `TranscriptView` | `TranscriptView.tsx` | Транскрипт с разделением оператор/клиент | `segments`, `highlights`, `onTimestampClick` |
| `AudioPlayer` | `AudioPlayer.tsx` | Плеер с навигацией по timestamp | `src`, `currentTime`, `onSeek` |
| `FilterBar` | `FilterBar.tsx` | Фильтры: оператор, дата, оценка | `filters`, `operators`, `onFilterChange` |
| `Pagination` | `Pagination.tsx` | Навигация по страницам | `total`, `page`, `limit`, `onPageChange` |
| `SummaryCards` | `SummaryCards.tsx` | Сводные карточки средних оценок | `averages` |

---

## Реестр сервисов (Backend)

| Сервис | Файл | Назначение | Вход → Выход |
|--------|------|-----------|-------------|
| `PipelineOrchestrator` | `pipeline.py` | Оркестрация этапов с checkpoints | `file_id` → обновляет статус в БД |
| `WhisperService` | `whisper_service.py` | Транскрибация аудио | `audio_path` → `{text, word_timestamps}` |
| `DiarizationService` | `diarization.py` | Определение спикеров | `audio_path` → `[{speaker, start, end}]` |
| `LLMService` | `llm_service.py` | Анализ через GPT-4 | `operator_text` → `{standard, loyalty, kindness, summary, quotes}` |
| `AudioValidator` | `audio_validator.py` | Валидация файлов | `file` → `{valid, error?, duration?, channels?}` |
| `QueueManager` | `queue.py` | Управление очередью обработки | CRUD над статусами файлов в БД |

---

## Сторонние библиотеки

### Backend (Python)

| Пакет | Версия | Назначение |
|-------|--------|-----------|
| `fastapi` | ^0.115 | Web-фреймворк |
| `uvicorn` | ^0.34 | ASGI сервер |
| `sqlalchemy` | ^2.0 | ORM |
| `alembic` | ^1.14 | Миграции БД |
| `psycopg2-binary` | ^2.9 | PostgreSQL драйвер |
| `pydantic` | ^2.0 | Валидация данных |
| `pydantic-settings` | ^2.0 | Конфигурация из .env |
| `openai-whisper` или `faster-whisper` | latest | Транскрибация |
| `pyannote.audio` | ^3.3 | Диаризация |
| `openai` | ^1.0 | GPT-4 API клиент |
| `python-multipart` | ^0.0.18 | Загрузка файлов |
| `websockets` | ^14.0 | WebSocket поддержка |
| `ffmpeg-python` | ^0.2 | Работа с аудио (ffprobe) |

### Frontend (TypeScript/React)

| Пакет | Версия | Назначение |
|-------|--------|-----------|
| `react` | ^18 | UI фреймворк |
| `react-dom` | ^18 | DOM rendering |
| `typescript` | ^5.0 | Типизация |
| `vite` | ^6.0 | Сборщик |
| `tailwindcss` | ^4.0 | CSS утилиты |
| `react-dropzone` | ^14 | Drag-and-drop файлов |

---

## Конвенции кода

### Python (Backend)

| Правило | Инструмент | Конфиг |
|---------|-----------|--------|
| Форматирование | `ruff format` | pyproject.toml |
| Линтинг | `ruff check` | pyproject.toml |
| Type hints | Обязательны для публичных функций | mypy (опционально) |
| Docstrings | Только для неочевидной логики | Google style |
| Импорты | isort (через ruff) | — |

### TypeScript/React (Frontend)

| Правило | Инструмент | Конфиг |
|---------|-----------|--------|
| Форматирование | `prettier` | .prettierrc |
| Линтинг | `eslint` | eslint.config.js |
| Компоненты | Функциональные с hooks | Без class components |
| State management | React hooks (`useState`, `useReducer`) | Без Redux (MVP) |
| Типы | Общие типы в `src/types/index.ts` | — |

---

## API Контракты

### POST /api/v1/upload

```
Request:
  Content-Type: multipart/form-data
  Body:
    files: File[]              # аудиофайлы
    operator_name: string      # имя оператора

Response (200):
  {
    "file_ids": ["uuid-1", "uuid-2"],
    "operator": "Иван Петров",
    "status": "queued",
    "total_files": 2
  }

Response (400):
  {
    "error": "validation_error",
    "details": [
      {"file": "doc.docx", "error": "Формат не поддерживается"},
      {"file": "huge.wav", "error": "Размер превышает 500 MB"}
    ]
  }
```

### GET /api/v1/results

```
Request:
  Query: ?operator=Иван&date_from=2026-02-01&date_to=2026-02-28
          &score_min=0&score_max=100&sort=created_at&order=desc
          &page=1&limit=20

Response (200):
  {
    "items": [
      {
        "file_id": "uuid",
        "original_name": "call_001.mp3",
        "operator_name": "Иван Петров",
        "duration_sec": 320.5,
        "status": "done",
        "analysis": {
          "standard": 87,
          "loyalty": 92,
          "kindness": 95,
          "overall": 91,
          "summary": "Оператор соблюдает стандарты..."
        },
        "diarization_confidence": 88.5,
        "created_at": "2026-02-25T10:30:00Z"
      }
    ],
    "total": 156,
    "page": 1,
    "limit": 20,
    "pages": 8
  }
```

### GET /api/v1/results/{file_id}

```
Response (200):
  {
    "file_id": "uuid",
    "original_name": "call_001.mp3",
    "operator_name": "Иван Петров",
    "duration_sec": 320.5,
    "status": "done",
    "audio_url": "/api/v1/audio/uuid",
    "transcription": {
      "full_text": "Добрый день, компания...",
      "word_timestamps": [
        {"word": "Добрый", "start": 0.0, "end": 0.4},
        {"word": "день", "start": 0.4, "end": 0.7}
      ]
    },
    "diarization": {
      "method": "pyannote",
      "confidence": 88.5,
      "num_speakers": 2,
      "segments": [
        {"speaker": "operator", "start": 0.0, "end": 5.2, "text": "Добрый день, компания..."},
        {"speaker": "client", "start": 5.5, "end": 12.1, "text": "Здравствуйте, я хотел бы..."}
      ]
    },
    "analysis": {
      "standard": 87,
      "loyalty": 92,
      "kindness": 95,
      "overall": 91,
      "summary": "Оператор соблюдает стандарты приветствия и завершения звонка...",
      "quotes": [
        {"text": "Добрый день, компания Альфа, Иван, чем могу помочь?", "criterion": "standard"},
        {"text": "Конечно, я постараюсь решить ваш вопрос", "criterion": "loyalty"}
      ],
      "llm_model": "gpt-4"
    },
    "created_at": "2026-02-25T10:30:00Z"
  }
```

### WebSocket /api/v1/ws

```
Server → Client messages:
  {
    "type": "progress",
    "file_id": "uuid",
    "status": "transcribing",    // queued | transcribing | diarizing | analyzing | done | failed
    "stage": 2,                  // 0-4
    "stage_name": "Транскрибация",
    "progress": 45               // 0-100 (процент этапа)
  }

  {
    "type": "complete",
    "file_id": "uuid",
    "analysis": { ... }          // краткий результат для обновления таблицы
  }

  {
    "type": "error",
    "file_id": "uuid",
    "error": "Не удалось распознать речь",
    "recoverable": true
  }
```

---

## Environment Variables (.env)

```env
# Database
DATABASE_URL=postgresql://callanalytics:password@localhost:5432/callanalytics

# OpenAI (GPT-4 + Whisper API fallback)
OPENAI_API_KEY=sk-...

# Whisper
WHISPER_MODEL=large-v3
WHISPER_DEVICE=cuda
WHISPER_LANGUAGE=ru

# pyannote (requires HuggingFace token)
HF_TOKEN=hf_...

# Limits
MAX_FILE_SIZE_MB=500
MAX_BATCH_SIZE=20
MIN_DURATION_SEC=3
MAX_DURATION_SEC=14400
AUDIO_RETENTION_DAYS=7

# Server
HOST=0.0.0.0
PORT=8000
CORS_ORIGINS=http://localhost:5173

# Frontend (build-time)
VITE_API_BASE_URL=http://localhost:8000/api/v1
VITE_WS_URL=ws://localhost:8000/api/v1/ws
```

---

## Git Conventions

| Правило | Пример |
|---------|--------|
| Формат коммита | `feat: add upload endpoint with file validation` |
| Prefix | `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`, `test:` |
| Ветки | `main` (production), `dev` (development) |
| Feature-ветки | `feat/upload-endpoint`, `fix/whisper-timeout` |

---

## Docker

```yaml
# docker-compose.yml (production)
services:
  backend:
    build: ./backend
    ports: ["8000:8000"]
    volumes:
      - ./data:/app/data
    env_file: .env
    depends_on: [db]
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

  frontend:
    build: ./frontend
    ports: ["3000:80"]

  db:
    image: postgres:16
    environment:
      POSTGRES_DB: callanalytics
      POSTGRES_USER: callanalytics
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports: ["5432:5432"]

volumes:
  pgdata:
```
