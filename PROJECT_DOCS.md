# Project Docs — Call Analytics

> **Автоматически обновляется Docs Agent** с описанием каждого файла проекта.

---

## Backend — Infrastructure

### app/

#### main.py
- **Статус:** ⏳ Планируется (задача 1.1)
- **Назначение:** FastAPI entrypoint, инициализация приложения, регистрация middleware, обработчики исключений
- **Ключевые функции:**
  - Создание FastAPI app
  - CORS middleware
  - Global exception handlers
  - Регистрация всех routers
- **Импорты из:** `routers/*`, `config`, `database`
- **Зависимости:** fastapi, uvicorn, sqlalchemy

#### config.py
- **Статус:** ⏳ Планируется (задача 1.1)
- **Назначение:** Конфигурация приложения из переменных окружения (.env)
- **Ключевые классы:**
  - `Settings` (pydantic-settings) — все конфиги
  - `DATABASE_URL`, `OPENAI_API_KEY`, `WHISPER_MODEL`, `HF_TOKEN`, лимиты файлов
- **Зависимости:** pydantic-settings

#### database.py
- **Статус:** ⏳ Планируется (задача 1.1)
- **Назначение:** Инициализация SQLAlchemy engine и session factory
- **Ключевые функции:**
  - `get_db()` — dependency для получения сессии в endpoints
  - Engine с подключением к PostgreSQL
- **Зависимости:** sqlalchemy, psycopg2-binary

#### models.py
- **Статус:** ⏳ Планируется (задача 1.1)
- **Назначение:** ORM модели для всех таблиц БД (SQLAlchemy declarative)
- **Ключевые классы:**
  - `Operator` — операторы контакт-центра
  - `File` — загруженные аудиофайлы, статус обработки
  - `Transcription` — результаты транскрибации
  - `Diarization` — результаты диаризации
  - `Analysis` — результаты LLM-анализа
- **Связи:** File → Operator, Transcription, Diarization, Analysis
- **Зависимости:** sqlalchemy

#### schemas.py
- **Статус:** ⏳ Планируется (задача 1.2)
- **Назначение:** Pydantic schemas для request/response validation
- **Ключевые классы:**
  - `FileUploadRequest` — multipart form
  - `AnalysisResponse` — оценки + резюме + цитаты
  - `ResultsListResponse` — список результатов с пагинацией
  - `ProgressUpdate` — WebSocket сообщение прогресса
- **Зависимости:** pydantic

### routers/

#### upload.py
- **Статус:** ⏳ Планируется (задача 1.2)
- **Назначение:** `POST /api/v1/upload` — загрузка аудиофайлов
- **Ключевые функции:**
  - Приём multipart: files[] + operator_name
  - Валидация файлов (расширение, размер, MIME)
  - Сохранение в `data/uploads/`
  - Создание записей в БД (статус 'queued')
  - Запуск pipeline для каждого файла
- **Зависимости:** fastapi, AudioValidator, File model

#### results.py
- **Статус:** ⏳ Планируется (задача 1.8)
- **Назначение:** GET endpoints для получения результатов
- **Ключевые endpoints:**
  - `GET /api/v1/results` — список с фильтрацией + пагинация
  - `GET /api/v1/results/{file_id}` — детальный отчёт
  - `GET /api/v1/status/{file_id}` — текущий статус
- **Фильтры:** operator, date_from, date_to, score_min, score_max, sort, order
- **Зависимости:** fastapi, sqlalchemy ORM

#### operators.py
- **Статус:** ⏳ Планируется (задача 1.8)
- **Назначение:** GET /api/v1/operators — автодополнение имён операторов
- **Ключевые функции:**
  - `?q=Ив` → возвращает список операторов, начинающихся с "Ив"
- **Зависимости:** fastapi, Operator model

#### health.py
- **Статус:** ⏳ Планируется (задача 1.8)
- **Назначение:** GET /api/v1/health — health check сервиса
- **Возвращает:** статус компонентов (whisper, llm, db), свободное место, длину очереди
- **Зависимости:** fastapi, config

#### ws.py
- **Статус:** ⏳ Планируется (задача 1.7)
- **Назначение:** WebSocket /api/v1/ws — live-обновления прогресса обработки
- **Сообщения:**
  - `progress` — текущий статус файла + процент этапа
  - `complete` — файл готов, результаты
  - `error` — ошибка обработки
- **Зависимости:** fastapi.WebSocket, asyncio, pipeline

### services/

#### pipeline.py
- **Статус:** ⏳ Планируется (задача 1.6)
- **Назначение:** Оркестрация всего pipeline обработки с checkpoints
- **Ключевой класс:** `PipelineOrchestrator`
- **Этапы:**
  1. Загрузка и валидация файла
  2. Транскрибация (Whisper)
  3. Диаризация (pyannote или split каналов)
  4. Анализ LLM (GPT-4)
  5. Сохранение результатов
- **Checkpoints:** каждый этап сохраняется в БД перед следующим
- **Retry-логика:** exponential backoff (2s → 4s → 8s)
- **Graceful degradation:** при падении LLM показываем транскрипт без оценок
- **Зависимости:** WhisperService, DiarizationService, LLMService, AudioValidator

#### whisper_service.py
- **Статус:** ⏳ Планируется (задача 1.3)
- **Назначение:** Транскрибация аудио с использованием Whisper
- **Ключевой класс:** `WhisperService`
- **Методы:**
  - `transcribe(audio_path)` → `{text, word_timestamps}`
  - Поддержка длинных файлов (> 30 мин) с chunking
  - Fallback на OpenAI Whisper API при отказе GPU
- **Параметры:**
  - Model: `whisper-large-v3`
  - Language: `ru`
  - Device: CUDA (GPU)
- **Зависимости:** openai-whisper или faster-whisper, torch

#### diarization.py
- **Статус:** ⏳ Планируется (задача 1.4)
- **Назначение:** Определение спикеров (оператор ↔ клиент) в аудиозаписи
- **Ключевой класс:** `DiarizationService`
- **Стратегия:**
  - Стерео (2 канала) → split каналов (L=оператор, R=клиент), confidence=100%
  - Моно → pyannote speaker diarization, confidence=70-95%
- **Методы:**
  - `diarize(audio_path)` → `{segments: [{speaker, start, end, text}], confidence, num_speakers}`
- **Валидация:** > 2 спикеров → предупреждение
- **Зависимости:** pyannote.audio, librosa, torch

#### llm_service.py
- **Статус:** ⏳ Планируется (задача 1.5)
- **Назначение:** Анализ качества обслуживания через GPT-4 API
- **Ключевой класс:** `LLMService`
- **Методы:**
  - `analyze(operator_speech, client_context)` → `{standard, loyalty, kindness, overall, summary, quotes}`
- **Параметры:**
  - Model: `gpt-4`
  - Temperature: `0` (детерминизм)
  - Prompt: система оценивает по 3 критериям (Стандарты, Лояльность, Доброжелательность)
- **Валидация:** парсинг JSON ответа, clamp оценок к [0, 100]
- **Retry-логика:** 3 попытки с exponential backoff
- **Graceful degradation:** при failure возвращается транскрипт без оценок
- **Зависимости:** openai (Python client)

#### audio_validator.py
- **Статус:** ⏳ Планируется (задача 1.2)
- **Назначение:** Валидация загруженных аудиофайлов
- **Ключевой класс:** `AudioValidator`
- **Проверки:**
  - Расширение в whitelist (mp3, wav, ogg, flac, m4a, webm)
  - Размер файла (макс. 500 MB)
  - MIME-type / magic bytes
  - ffprobe: файл декодируется, длительность (3 сек - 4 часа)
  - SHA-256: дубликат?
- **Результат:** `{valid: bool, error?: str, duration?: float, channels?: int}`
- **Зависимости:** ffmpeg-python, hashlib

#### queue.py
- **Статус:** ⏳ Планируется (задача 1.6)
- **Назначение:** Управление очередью обработки файлов
- **Ключевой класс:** `QueueManager`
- **Методы:**
  - CRUD над статусами файлов в таблице `files`
  - Получение следующего файла в статусе 'queued'
  - Обновление статуса (queued → transcribing → diarizing → analyzing → done)
  - Обработка ошибок (retry_count, error_message)
- **Зависимости:** sqlalchemy, File model

### utils/

#### audio.py
- **Статус:** ⏳ Планируется (задача 1.3, 1.4)
- **Назначение:** Утилиты для работы с аудио
- **Функции:**
  - `get_duration(file_path)` → float (через ffprobe)
  - `get_channels(file_path)` → int
  - `split_stereo(audio_path)` → (mono_left, mono_right)
  - `apply_vad(audio_path)` → filtered audio (убрать тишину и музыку)
  - `convert_format(input, output_format)` → bytes
- **Зависимости:** ffmpeg-python, librosa, torch (для VAD)

#### hash.py
- **Статус:** ⏳ Планируется (задача 1.2)
- **Назначение:** Подсчёт SHA-256 хеша файла для дедупликации
- **Функции:**
  - `compute_file_hash(file_path)` → str (SHA-256)
  - `file_exists_by_hash(db, hash_value)` → bool
- **Зависимости:** hashlib, sqlalchemy

---

## Frontend — User Interface

### src/components/

#### UploadZone.tsx
- **Статус:** ⏳ Планируется (задача 2.1)
- **Назначение:** Drag-and-drop зона для загрузки аудиофайлов
- **Props:** `onFilesSelected`, `disabled`, `acceptedFormats`
- **Поведение:**
  - Drag-over → выделение зоны
  - Drop → вызов `onFilesSelected` с файлами
  - Click → открыть файл-выбиратель
  - Валидация расширений на фронте
- **Зависимости:** react-dropzone

#### FileList.tsx
- **Статус:** ⏳ Планируется (задача 2.1, 2.2)
- **Назначение:** Список выбранных файлов + поле оператора + кнопка "Анализировать"
- **Props:** `files`, `onRemove`, `onOperatorChange`, `onAnalyze`
- **Состояние:** `operator_name` (с autocomplete из `/api/v1/operators`)
- **Функции:**
  - Удаление файла из списка
  - Ввод имени оператора (общее для всей пачки)
  - Кнопка "Анализировать" → POST `/api/v1/upload`

#### ResultsTable.tsx
- **Статус:** ⏳ Планируется (задача 2.4)
- **Назначение:** Таблица результатов анализа с expandable rows
- **Props:** `results`, `onRowClick`, `sortBy`, `sortDir`
- **Столбцы:** Файл, Оператор, Стандарты, Лояльность, Доброжел., Итог
- **Поведение:**
  - Клик на строку → expand/collapse детали
  - Expandable row показывает краткий транскрипт + оценки
  - Клик на "Подробнее" → AnalysisDetail

#### ResultRow.tsx
- **Статус:** ⏳ Планируется (задача 2.4)
- **Назначение:** Одна строка таблицы результатов (expandable)
- **Props:** `result`, `expanded`, `onToggle`
- **Содержимое:**
  - При expanded=false: таблица (файл, оператор, оценки)
  - При expanded=true: краткие детали (первые 200 слов транскрипта + цитаты)

#### AnalysisDetail.tsx
- **Статус:** ⏳ Планируется (задача 2.5)
- **Назначение:** Полный детальный отчёт по одному файлу
- **Props:** `analysis` (результат `GET /api/v1/results/{file_id}`), `onBack`
- **Разделы:**
  1. ScoreCard (3 критерия + общая оценка)
  2. ResumeSummary (текст + выводы)
  3. TranscriptView (полный транскрипт с подсветкой проблемных мест)
  4. AudioPlayer (с навигацией по timestamp из цитат)
  5. QuotesHighlights (ключевые цитаты, раскрасены по критериям)

#### ScoreCard.tsx
- **Статус:** ⏳ Планируется (задача 2.5)
- **Назначение:** Карточка оценки с круговым индикатором (%)
- **Props:** `label`, `score` (0-100), `size` ('sm' | 'md' | 'lg')
- **Цвета:**
  - 90-100%: зелёный (#22C55E) → "Отлично"
  - 75-89%: синий (#3B82F6) → "Хорошо"
  - 60-74%: жёлтый (#F59E0B) → "Удовлетворительно"
  - 0-59%: красный (#EF4444) → "Требует внимания"

#### TranscriptView.tsx
- **Статус:** ⏳ Планируется (задача 2.5)
- **Назначение:** Отображение полного транскрипта с разделением оператор↔клиент
- **Props:** `segments` (из диаризации), `highlights` (проблемные моменты), `onTimestampClick`
- **Функции:**
  - Цветовое разделение (оператор ↔ клиент)
  - Подсветка проблемных цитат
  - Клик на слово → `onTimestampClick(timestamp)` для плеера

#### AudioPlayer.tsx
- **Статус:** ⏳ Планируется (задача 2.5)
- **Назначение:** HTML5 audio плеер с навигацией по timestamp
- **Props:** `src` (URL аудиофайла), `currentTime`, `onSeek`
- **Функции:**
  - Play/Pause
  - Прогресс-бар (seek-able)
  - Volume control
  - Speed control (0.5x, 1x, 1.5x, 2x)
  - Display current time / duration
  - Клик на цитату → перемотка к timestamp

#### FilterBar.tsx
- **Статус:** ⏳ Планируется (задача 2.6)
- **Назначение:** Фильтры результатов (оператор, дата, оценка)
- **Props:** `filters`, `operators`, `onFilterChange`
- **Элементы:**
  - Autocomplete: выбор оператора
  - Date range: от-до
  - Score range: 0-100%
  - Кнопка "Сбросить фильтры"

#### Pagination.tsx
- **Статус:** ⏳ Планируется (задача 2.6)
- **Назначение:** Навигация по страницам результатов
- **Props:** `total`, `page`, `limit`, `onPageChange`
- **Элементы:** « 1 2 3 ... » → "20 из 156 результатов"

#### SummaryCards.tsx
- **Статус:** ⏳ Планируется (задача 2.7)
- **Назначение:** Сводные карточки средних оценок по отфильтрованным результатам
- **Props:** `averages` (объект с среднимиоценками)
- **Показывает:** Средние оценки по 3 критериям (крупные карточки в шапке)

### src/hooks/

#### useWebSocket.ts
- **Статус:** ⏳ Планируется (задача 2.3)
- **Назначение:** Hook для WebSocket подключения к серверу (live-прогресс)
- **Функции:**
  - Подключение к `/api/v1/ws`
  - Автоматическая переподключение при разрыве
  - Fallback: polling каждые 3 сек (при недоступности WebSocket)
  - Обработка сообщений: `progress`, `complete`, `error`
  - Обновление состояния компонентов в реальном времени

#### useUpload.ts
- **Статус:** ⏳ Планируется (задача 2.1, 2.2)
- **Назначение:** Hook для логики загрузки файлов
- **Функции:**
  - `uploadFiles(files, operator_name)` → POST `/api/v1/upload`
  - Обработка ошибок валидации
  - Отслеживание прогресса (используя WebSocket)
  - Состояние: `files`, `operator`, `uploading`, `errors`

#### useResults.ts
- **Статус:** ⏳ Планируется (задача 2.4, 2.6)
- **Назначение:** Hook для загрузки и фильтрации результатов
- **Функции:**
  - `fetchResults(params)` → GET `/api/v1/results` с фильтрами
  - Кеширование результатов
  - Пагинация
  - Состояние: `results`, `loading`, `error`, `total`, `currentPage`

### src/lib/

#### api.ts
- **Статус:** ⏳ Планируется (задача 2.1)
- **Назначение:** HTTP-клиент (wrapper over fetch)
- **Функции:**
  - `GET(url, params)` → JSON
  - `POST(url, data)` → JSON
  - `UPLOAD(url, formData)` → JSON (для multipart)
  - Обработка ошибок (кросс-браузерная поддержка)
  - Базовая URL из env: `VITE_API_BASE_URL`

#### utils.ts
- **Статус:** ⏳ Планируется (задача 2.5)
- **Назначение:** Утилиты для фронтенда
- **Функции:**
  - `formatScore(score, withLabel?)` → "87%" или "Хорошо (87%)"
  - `getScoreColor(score)` → CSS класс или hex цвет
  - `formatDuration(seconds)` → "5:30"
  - `formatDate(timestamp)` → "25 февр. 2026, 14:35"
  - `timeToSeconds(time)` → преобразование из HH:MM:SS в секунды

### src/types/

#### index.ts
- **Статус:** ⏳ Планируется (задача 2.1)
- **Назначение:** Общие TypeScript типы для фронтенда
- **Типы:**
  - `FileUpload` — файл, готовый к загрузке
  - `FileStatus` — статус обработки (queued | transcribing | diarizing | analyzing | done | failed)
  - `AnalysisResult` — полный результат анализа (оценки + резюме + цитаты)
  - `TranscriptionSegment` — один сегмент транскрипта (speaker, text, timestamps)
  - `FilterParams` — параметры фильтрации
  - Соответствуют API контрактам в CONVENTIONS.md

### App.tsx
- **Статус:** ⏳ Планируется (задача 2.1)
- **Назначение:** Главный компонент приложения
- **Состояния:**
  - Empty → FilesPicked → Uploading → Processing → Results
- **Layout:**
  - Header (logo, title, health check)
  - Main content (в зависимости от состояния)
  - Footer

### main.tsx
- **Статус:** ⏳ Планируется (задача 2.1)
- **Назначение:** Entry point React приложения
- **Инициализация:**
  - Рендер App в #root
  - Подключение TailwindCSS стилей

---

## Database

### alembic/ (Миграции)
- **Статус:** ⏳ Планируется (задача 1.1)
- **Назначение:** Версионирование схемы БД через Alembic
- **Содержимое:**
  - `versions/001_initial_schema.py` — создание всех таблиц (operators, files, transcriptions, diarizations, analyses)
  - `versions/002_*.py` (будущие) — другие миграции
- **Команды:**
  - `alembic upgrade head` — применить все миграции
  - `alembic downgrade -1` — откатить последнюю

---

## Configuration

### .env.example
- **Статус:** ⏳ Планируется (задача 1.1)
- **Назначение:** Пример файла переменных окружения
- **Включает:** все возможные переменные с примерами значений
- **Копируется:** в `.env` перед запуском (не коммитится в git)

### docker-compose.yml
- **Статус:** ⏳ Планируется (задача 3.2)
- **Назначение:** Оркестрация контейнеров (backend + frontend + PostgreSQL)
- **Сервисы:**
  - `backend` — FastAPI приложение
  - `frontend` — React SPA (Vite)
  - `db` — PostgreSQL 16
- **Volumes:** `data/uploads/` для аудиофайлов, `pgdata` для БД

### backend/Dockerfile
- **Статус:** ⏳ Планируется (задача 3.2)
- **Назначение:** Docker образ для backend
- **Основано на:** `python:3.12-slim`
- **Установка:** зависимости из requirements.txt, Whisper моделъ, pyannote токен HF

### backend/requirements.txt
- **Статус:** ⏳ Планируется (задача 1.1)
- **Назначение:** Python зависимости
- **Основные:** fastapi, uvicorn, sqlalchemy, alembic, psycopg2-binary, pydantic, openai-whisper, pyannote.audio, openai, websockets

### frontend/package.json
- **Статус:** ⏳ Планируется (задача 2.1)
- **Назначение:** Node.js зависимости и скрипты
- **Основные:** react, react-dom, typescript, vite, tailwindcss, react-dropzone, prettier, eslint

### frontend/tsconfig.json
- **Статус:** ⏳ Планируется (задача 2.1)
- **Назначение:** Конфигурация TypeScript
- **Основные:** strictMode, jsx react-jsx

### frontend/vite.config.ts
- **Статус:** ⏳ Планируется (задача 2.1)
- **Назначение:** Конфигурация сборщика Vite
- **Основные:** plugins (react), server proxy

### frontend/tailwind.config.js
- **Статус:** ⏳ Планируется (задача 2.1)
- **Назначение:** Конфигурация TailwindCSS
- **Основные:** расширение цветов для индикаторов оценок

---

## Documentation

### VISION.md
- **Статус:** ✅ Завершено
- **Автор:** Vision Team
- **Содержит:** видение продукта, целевую аудиторию, сценарии, UX-принципы, edge cases, отказоустойчивость

### ARCHITECTURE.md
- **Статус:** ✅ Завершено
- **Автор:** Vision Team (Architect)
- **Содержит:** трёхслойная архитектура, структура проекта, компоненты, API endpoints, pipeline с checkpoints, технические решения

### CONVENTIONS.md
- **Статус:** ✅ Завершено
- **Автор:** Vision Team
- **Содержит:** naming conventions, реестр компонентов, реестр сервисов, библиотеки, конвенции кода, API контракты, env variables, git и docker

### HANDOFF.md
- **Статус:** ✅ Завершено
- **Назначение:** История проекта, текущий статус, следующие шаги
- **Обновляется:** Chronicler после каждой сессии

### PROJECT_DOCS.md
- **Статус:** ✅ Начато
- **Назначение:** Этот файл — реестр всех файлов с описаниями
- **Автор:** Docs Agent (автоматическое обновление)

### CHANGELOG.local.md
- **Статус:** ✅ Начато
- **Назначение:** История изменений в проекте (новые файлы, удаления, важные события)
- **Автор:** Docs Agent (новые записи сверху)

---

## Структура данных (хранилище)

### data/uploads/
- **Назначение:** Временное хранилище загруженных аудиофайлов
- **Жизненный цикл:** файл загружен → обработан → удалён через 7 дней (конфигурируемо)
- **Объём:** зависит от количества одновременных обработок

### data/audio/
- **Назначение:** Аудиофайлы для стриминга в плеер (фронтенд)
- **Жизненный цикл:** конвертированы из оригинала после транскрибации → хранятся 7 дней

---

## Скрипты и утилиты

### (Планируется добавить)
- Скрипт инициализации БД
- Скрипт заполнения тестовыми данными
- Скрипт очистки старых файлов

---

**Последнее обновление:** 2026-02-25 17:25 (Docs Agent инициализация)

