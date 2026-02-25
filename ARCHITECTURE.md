# Architecture — Call Analytics

> Создано Vision Team: 2026-02-25
> Tech Stack: **Python (FastAPI) + React (TypeScript) + PostgreSQL + Whisper + pyannote + GPT-4 API**

---

## Обзор

Трёхслойная архитектура:

```
┌─────────────────────────────────┐
│     Frontend (React SPA)        │  ← UI: загрузка, прогресс, результаты
├─────────────────────────────────┤
│     Backend (FastAPI)           │  ← API + Pipeline оркестрация
├──────────┬──────────┬───────────┤
│ Whisper  │ pyannote │  GPT-4    │  ← Обработка аудио и анализ
│ (GPU)    │ (GPU)    │  (API)    │
├──────────┴──────────┴───────────┤
│     PostgreSQL + File Storage   │  ← Данные и аудиофайлы
└─────────────────────────────────┘
```

---

## Структура проекта

```
call-analytics/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI entrypoint
│   │   ├── config.py               # Settings из .env
│   │   ├── database.py             # SQLAlchemy engine, session
│   │   ├── models.py               # ORM модели (files, transcriptions, analyses)
│   │   ├── schemas.py              # Pydantic schemas (request/response)
│   │   ├── routers/
│   │   │   ├── upload.py           # POST /api/v1/upload
│   │   │   ├── results.py          # GET /api/v1/results, /api/v1/results/{id}
│   │   │   ├── operators.py        # GET /api/v1/operators (автодополнение)
│   │   │   ├── health.py           # GET /api/v1/health
│   │   │   └── ws.py               # WebSocket /api/v1/ws (прогресс)
│   │   ├── services/
│   │   │   ├── pipeline.py         # Оркестрация pipeline с checkpoints
│   │   │   ├── whisper_service.py  # Транскрибация (Whisper large-v3)
│   │   │   ├── diarization.py      # Диаризация (pyannote + channel split)
│   │   │   ├── llm_service.py      # Анализ через GPT-4 API
│   │   │   ├── audio_validator.py  # Валидация файлов (формат, размер, длительность)
│   │   │   └── queue.py            # Очередь обработки
│   │   └── utils/
│   │       ├── audio.py            # ffprobe, VAD, формат-конвертация
│   │       └── hash.py             # SHA-256 дедупликация
│   ├── alembic/                    # Миграции БД
│   ├── requirements.txt
│   ├── .env.example
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   ├── types/
│   │   │   └── index.ts            # TypeScript типы
│   │   ├── components/
│   │   │   ├── UploadZone.tsx       # Drag-and-drop загрузка
│   │   │   ├── FileList.tsx         # Список файлов с прогрессом
│   │   │   ├── ResultsTable.tsx     # Таблица результатов с пагинацией
│   │   │   ├── ResultRow.tsx        # Строка таблицы (expandable)
│   │   │   ├── AnalysisDetail.tsx   # Детальный отчёт по файлу
│   │   │   ├── ScoreCard.tsx        # Карточка оценки (круговой индикатор)
│   │   │   ├── TranscriptView.tsx   # Транскрипт с подсветкой и разделением
│   │   │   ├── AudioPlayer.tsx      # Плеер с навигацией по timestamp
│   │   │   ├── FilterBar.tsx        # Фильтры: оператор, дата, оценка
│   │   │   ├── Pagination.tsx       # Пагинация
│   │   │   └── SummaryCards.tsx     # Сводные карточки (средние оценки)
│   │   ├── hooks/
│   │   │   ├── useWebSocket.ts      # WebSocket для live-прогресса
│   │   │   ├── useUpload.ts         # Логика загрузки файлов
│   │   │   └── useResults.ts        # Загрузка и фильтрация результатов
│   │   └── lib/
│   │       ├── api.ts               # HTTP-клиент (fetch wrapper)
│   │       └── utils.ts             # Утилиты (форматирование, цвета)
│   ├── package.json
│   ├── tsconfig.json
│   ├── tailwind.config.js
│   ├── vite.config.ts
│   └── Dockerfile
├── data/
│   ├── uploads/                     # Загруженные аудиофайлы (temp)
│   └── audio/                       # Аудио для плеера (хранятся 7 дней)
├── docker-compose.yml
├── VISION.md
├── ARCHITECTURE.md
└── CONVENTIONS.md
```

---

## Компоненты системы

### 1. Frontend (React SPA)

**Технологии:** React 18, TypeScript, Vite, TailwindCSS

**Состояния приложения:**
```
Empty → FilesPicked → Uploading → Processing → Results
         ↑                                        │
         └────────────── «+ Загрузить» ───────────┘
```

**Ключевые компоненты:**

| Компонент | Назначение | Ключевые props/state |
|-----------|-----------|---------------------|
| `UploadZone` | Drag-drop зона для файлов | `onFilesSelected`, `disabled` |
| `FileList` | Список файлов + поле оператора + кнопка «Анализировать» | `files`, `onRemove`, `onAnalyze` |
| `ResultsTable` | Таблица результатов с expandable rows | `results`, `filters`, `page` |
| `AnalysisDetail` | Детальный отчёт (оценки + резюме + транскрипт + плеер) | `analysis` |
| `AudioPlayer` | HTML5 audio с навигацией по timestamp | `audioUrl`, `timestamps` |
| `FilterBar` | Фильтры: оператор (autocomplete), дата (range), оценка (range) | `onFilterChange` |
| `Pagination` | Навигация по страницам (20 результатов на странице) | `total`, `page`, `onPageChange` |

**Real-time обновления:** WebSocket для live-прогресса обработки. Fallback: polling каждые 3 сек.

---

### 2. Backend (FastAPI)

**API Endpoints:**

| Метод | Endpoint | Назначение | Request | Response |
|-------|----------|-----------|---------|----------|
| `POST` | `/api/v1/upload` | Загрузка файлов | `multipart/form-data` (files[], operator_name) | `{file_ids: [uuid], status: 'queued'}` |
| `GET` | `/api/v1/results` | Список результатов с фильтрацией | `?operator=&date_from=&date_to=&score_min=&score_max=&page=1&limit=20` | `{items: [...], total: 156, page: 1}` |
| `GET` | `/api/v1/results/{file_id}` | Детальный результат | — | `{file_id, operator, transcription, diarization, analysis, audio_url}` |
| `GET` | `/api/v1/status/{file_id}` | Статус обработки файла | — | `{status: 'transcribing', progress: 45, stage: 2/4}` |
| `GET` | `/api/v1/operators` | Автодополнение операторов | `?q=Ив` | `['Иван Петров', 'Иван Сидоров']` |
| `GET` | `/api/v1/audio/{file_id}` | Стриминг аудиофайла для плеера | — | `audio/mpeg` (range requests) |
| `GET` | `/api/v1/health` | Health check | — | `{status, whisper, llm, disk_free_gb, queue_length}` |
| `WS` | `/api/v1/ws` | Live-обновления прогресса | — | `{file_id, status, progress, stage}` |

**Pipeline с checkpoints:**

```
┌─────────┐     ┌─────────────┐     ┌──────────────┐     ┌──────────┐     ┌──────┐
│ Upload  │────▸│ Validate    │────▸│ Transcribe   │────▸│ Diarize  │────▸│ LLM  │
│         │     │ (ffprobe,   │     │ (Whisper     │     │(pyannote │     │(GPT-4│
│         │     │  format,    │     │  large-v3,   │     │ or split │     │ API) │
│         │     │  size)      │     │  GPU)        │     │ channels)│     │      │
└────┬────┘     └─────┬───────┘     └──────┬───────┘     └────┬─────┘     └──┬───┘
     │                │                     │                  │              │
  [disk]          [status:DB]         [transcript:DB]    [diarization:DB] [analysis:DB]
  checkpoint 0    checkpoint 1        checkpoint 2        checkpoint 3     checkpoint 4
```

При падении на этапе N → автоматическое продолжение с checkpoint N-1.

---

### 3. Whisper (Транскрибация)

| Параметр | Значение |
|----------|---------|
| Модель | `whisper-large-v3` (openai-whisper или faster-whisper) |
| Запуск | Локально на GPU (RTX 5090, 32 GB VRAM) |
| Язык | `ru` (hardcoded для MVP) |
| Выход | Текст + word-level timestamps |
| Chunking | Файлы > 30 мин разбиваются на сегменты с overlap 1 сек |
| Скорость | ~5 мин аудио за ~1 мин обработки (на RTX 5090) |

**Резервная стратегия:**
- Локальный Whisper упал → retry 3x с backoff
- Если все retry провалились → fallback на OpenAI Whisper API (если API ключ указан)
- Если и API недоступен → файл в очередь «ожидает сервис»

**Фильтрация перед транскрибацией:**
- VAD (Voice Activity Detection) — отсечь тишину и музыку
- Только speech-сегменты отправляются в Whisper

---

### 4. Диаризация (pyannote) — РАЗРЕШЕНИЕ БЛОКЕРА #1

**Стратегия: стерео vs моно**

```
Аудиофайл загружен
       │
       ▼
┌─ Стерео (2 канала)? ─┐
│                       │
│ ДА                    │ НЕТ (моно)
│                       │
▼                       ▼
Split каналов:          pyannote speaker diarization:
 L = Оператор            Определение спикеров по голосу
 R = Клиент              speaker_0 = Оператор (первый голос)
                         speaker_1 = Клиент
 Confidence: 100%        Confidence: 70-95% (зависит от качества)
 (каналы жёстко          │
  разделены)              ├─ confidence >= 70% → OK, используем
                          └─ confidence < 70% → предупреждение:
                              "Разделение неуверенное, проверьте"
```

**Библиотека:** `pyannote.audio` v3.x (HuggingFace)
- Вход: аудиофайл
- Выход: `[(start_time, end_time, speaker_id), ...]`
- При > 2 спикерах: предупреждение «Обнаружено N говорящих, оценка может быть неточной»

**Привязка транскрипта к спикерам:**
- Timestamps из Whisper + timestamps из pyannote → merge по временным меткам
- Каждая реплика получает метку: `operator` или `client`
- В LLM-анализ отправляются **только реплики оператора** (+ контекст клиента для понимания)

---

### 5. Привязка к операторам — РАЗРЕШЕНИЕ БЛОКЕРА #2

**Механизм MVP:**

```
При загрузке файлов:
  1. Поле "Оператор" (обязательное)
     ├── Autocomplete из ранее введённых имён (GET /api/v1/operators?q=)
     ├── Свободный ввод нового имени
     └── Одно имя для всей пачки файлов (с возможностью поменять для отдельных)

  2. Автодетекция из имени файла (эвристика):
     "ivanov_2026-02-25_call001.mp3" → предзаполнить "Ivanov"
     Формат: {operator}_{date}_{id}.{ext}
     Если не совпадает — ручной ввод

Хранение:
  - Таблица operators: (id, name, created_at)
  - Связь: files.operator_id → operators.id
  - Агрегация: средние оценки по оператору, количество звонков
```

---

### 6. История и поиск — РАЗРЕШЕНИЕ БЛОКЕРА #3

**База данных: PostgreSQL**

```sql
-- Операторы
CREATE TABLE operators (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(255) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- Файлы
CREATE TABLE files (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operator_id     UUID REFERENCES operators(id),
    original_name   VARCHAR(512) NOT NULL,
    file_hash       CHAR(64) NOT NULL,          -- SHA-256
    file_size       BIGINT NOT NULL,
    duration_sec    FLOAT,                       -- длительность аудио
    audio_path      VARCHAR(1024),               -- путь к файлу (пока не удалён)
    status          VARCHAR(32) DEFAULT 'queued', -- queued/transcribing/diarizing/analyzing/done/failed
    stage           SMALLINT DEFAULT 0,           -- 0-4 (номер checkpoint)
    retry_count     SMALLINT DEFAULT 0,
    error_message   TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- Транскрипции
CREATE TABLE transcriptions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id     UUID UNIQUE REFERENCES files(id) ON DELETE CASCADE,
    full_text   TEXT NOT NULL,
    word_timestamps JSONB,  -- [{word, start, end}, ...]
    language    VARCHAR(10) DEFAULT 'ru',
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- Диаризация
CREATE TABLE diarizations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id     UUID UNIQUE REFERENCES files(id) ON DELETE CASCADE,
    segments    JSONB NOT NULL,  -- [{speaker_id, start, end, text}, ...]
    method      VARCHAR(32),     -- 'channel_split' или 'pyannote'
    confidence  FLOAT,           -- 0-100%, null для channel_split (=100%)
    num_speakers SMALLINT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- Анализы (LLM)
CREATE TABLE analyses (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id     UUID UNIQUE REFERENCES files(id) ON DELETE CASCADE,
    standard    SMALLINT NOT NULL CHECK (standard BETWEEN 0 AND 100),
    loyalty     SMALLINT NOT NULL CHECK (loyalty BETWEEN 0 AND 100),
    kindness    SMALLINT NOT NULL CHECK (kindness BETWEEN 0 AND 100),
    overall     SMALLINT NOT NULL CHECK (overall BETWEEN 0 AND 100),
    summary     TEXT NOT NULL,
    quotes      JSONB,           -- [{text, timestamp, criterion, sentiment}, ...]
    llm_model   VARCHAR(64),     -- 'gpt-4' для трекинга
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- Индексы для фильтрации
CREATE INDEX idx_files_operator ON files(operator_id);
CREATE INDEX idx_files_status ON files(status);
CREATE INDEX idx_files_created ON files(created_at DESC);
CREATE INDEX idx_files_hash ON files(file_hash);
CREATE INDEX idx_analyses_standard ON analyses(standard);
CREATE INDEX idx_analyses_loyalty ON analyses(loyalty);
CREATE INDEX idx_analyses_kindness ON analyses(kindness);
CREATE INDEX idx_analyses_overall ON analyses(overall);
```

**Фильтрация и пагинация:**
- `GET /api/v1/results?operator=Иван&date_from=2026-02-01&score_min=0&score_max=60&page=2&limit=20`
- Поиск: по имени оператора, имени файла
- Сортировка: по дате, по оценке (любой критерий), по оператору
- Пагинация: offset-based, 20 результатов на странице

---

### 7. LLM Analysis (GPT-4 API) — РАЗРЕШЕНИЕ БЛОКЕРА #4

**Пользователь выбрал GPT-4 → облачное решение.**

| Параметр | Значение |
|----------|---------|
| Модель | `gpt-4` (не gpt-4-turbo — стабильнее) |
| Temperature | `0` (для детерминизма) |
| Вход | Реплики оператора + контекст клиента (из диаризации) |
| Выход | JSON: `{standard, loyalty, kindness, overall, summary, quotes}` |
| Стоимость | ~$0.03-0.10 за звонок (зависит от длины) |

**Данные, отправляемые в OpenAI:**
- Текст транскрипта (реплики оператора + контекст клиента)
- НЕ отправляются: аудиофайлы, имена операторов, метаданные файлов
- Транскрипт анонимизирован от файловых метаданных

**Промпт-стратегия:**
```
Системный промпт: Ты — эксперт по оценке качества обслуживания в контакт-центре.
Оцени оператора по трём критериям (0-100%):
1. Стандарты — соблюдение протокола (приветствие, представление, уточнение, прощание)
2. Лояльность — клиентоориентированность (решение проблемы, удержание, работа с возражениями)
3. Доброжелательность — тон (вежливость, эмпатия, спокойствие)

Верни JSON: {standard, loyalty, kindness, overall, summary, quotes: [{text, criterion}]}
- overall = средневзвешенное (стандарты 0.4, лояльность 0.3, доброжелательность 0.3)
- summary: 2-3 предложения на русском
- quotes: 2-5 цитат из разговора, подтверждающих оценку
```

**Валидация ответа LLM:**
- JSON парсится? → нет → retry с более строгим промптом (1 раз)
- Оценки в диапазоне 0-100? → нет → clamp + логирование аномалии
- summary и quotes присутствуют? → нет → пометить «частичный анализ»

**Резервная стратегия:**
- GPT-4 недоступен → graceful degradation: показать транскрипт + диаризацию без оценок
- Кнопка «Запустить анализ повторно» (когда LLM вернётся)
- Rate limit → очередь с throttling

---

### 8. Обработка ошибок

| Компонент | Отказ | Стратегия |
|-----------|-------|-----------|
| Whisper (GPU) | Упал / OOM | Retry 3x с backoff → fallback на OpenAI Whisper API |
| pyannote | Не определил спикеров | Предупреждение «Не удалось разделить говорящих» + транскрипт целиком |
| GPT-4 API | 429 / 500 / timeout | Retry 3x → graceful degradation (транскрипт без оценок) |
| GPT-4 API | Content policy block | «Автоанализ недоступен для этого звонка» + транскрипт |
| PostgreSQL | Недоступен | Приложение не запускается, health check «down» |
| Сеть (upload) | Обрыв | Ошибка загрузки, файл не сохранён, «Повторите загрузку» |
| Диск | Переполнен | Проверка свободного места перед загрузкой. Автоочистка старых аудио |

---

### 9. Валидация входных данных

**Поддерживаемые форматы:**

| Формат | MIME | Расширения |
|--------|------|-----------|
| WAV | audio/wav | .wav |
| MP3 | audio/mpeg | .mp3 |
| OGG | audio/ogg | .ogg |
| FLAC | audio/flac | .flac |
| M4A | audio/mp4 | .m4a |
| WebM | audio/webm | .webm |

**Лимиты:**

| Параметр | Значение | Конфигурируемо |
|----------|---------|---------------|
| Макс. размер файла | 500 MB | Да (.env) |
| Мин. длительность | 3 секунды | Да |
| Макс. длительность | 4 часа | Да |
| Макс. файлов в batch | 20 | Да |
| Хранение аудио | 7 дней | Да |
| Результаты на странице | 20 | Да |

**Порядок валидации (fail-fast):**
1. Расширение файла в whitelist?
2. Размер в пределах лимита?
3. MIME-type / magic bytes корректны?
4. ffprobe: файл декодируется? Длительность?
5. Дубликат (SHA-256)?
6. При дубликате: «Показать старый результат / Обработать заново»

---

### 10. Мониторинг

**Health Check:**
```
GET /api/v1/health
{
    "status": "ok" | "degraded" | "down",
    "whisper": "available" | "unavailable",
    "llm": "available" | "unavailable" | "rate_limited",
    "disk_free_gb": 150,
    "queue_length": 3,
    "processing": { "file_id": "...", "stage": "transcribing", "progress": 45 }
}
```

**Логирование:**
- Все API-ответы с latency
- Время обработки каждого этапа pipeline
- Rate limit events
- Невалидные ответы LLM (полный response)
- Disk space warnings (порог < 5 GB)
- Аномальные оценки LLM

---

## Масштабирование

| Масштаб | Звонков/день | Whisper | LLM | БД | Workers |
|---------|-------------|---------|-----|-----|---------|
| **Текущий (MVP)** | ~6-20 | 1 GPU, sequential | GPT-4 API | PostgreSQL, 1 instance | 1 worker process |
| **10x** | ~60 | 1 GPU, faster-whisper | GPT-4 API + throttling | PostgreSQL + indices | 1-2 workers |
| **100x** | ~600 | Multi-GPU / faster-whisper | Параллельные API-запросы | PostgreSQL + read replicas | N workers |

**Архитектурное решение:** очередь обработки с самого начала (в PostgreSQL через таблицу `files.status`). Это позволит добавить workers без переписывания pipeline.

---

## Задачи для Dev Team

### Фаза 1: Backend Infrastructure (основа)
- **1.1** Настроить FastAPI + PostgreSQL + Alembic миграции
- **1.2** Реализовать upload endpoint с валидацией файлов
- **1.3** Интегрировать Whisper (локально, GPU, chunking для длинных файлов)
- **1.4** Интегрировать pyannote (стерео split + моно diarization)
- **1.5** Интегрировать GPT-4 API с промптом, валидацией JSON, retry logic
- **1.6** Pipeline оркестрация с checkpoints и очередью
- **1.7** WebSocket endpoint для live-прогресса
- **1.8** Endpoints: /results (пагинация, фильтрация), /operators, /audio/{id}, /health

### Фаза 2: Frontend & UX
- **2.1** React SPA: UploadZone (drag-drop) + FileList + кнопка «Анализировать»
- **2.2** Привязка к оператору (поле с autocomplete)
- **2.3** Progress view (WebSocket, прогресс по этапам для каждого файла)
- **2.4** ResultsTable с expandable rows, пагинацией, фильтрацией
- **2.5** AnalysisDetail: оценки + резюме + TranscriptView + AudioPlayer
- **2.6** FilterBar (оператор, дата, оценка) + Pagination
- **2.7** SummaryCards (средние оценки по отфильтрованным результатам)

### Фаза 3: Polish & Deploy
- **3.1** Error handling и graceful degradation (все 4 уровня)
- **3.2** Docker-compose (backend + frontend + PostgreSQL)
- **3.3** E2E smoke-тесты (загрузка → обработка → результаты)

### Порядок разработки
```
Фаза 1 (1.1 → 1.2 → 1.3 → 1.4 → 1.5 → 1.6 → 1.7 → 1.8)
         │                                           │
         └── Фаза 2 (2.1-2.2 можно начать после 1.2) ──── Фаза 3
```

Фронтенд можно начинать параллельно с бэкендом после готовности upload endpoint (1.2).
