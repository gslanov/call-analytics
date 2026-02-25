# Test Report — Call Analytics (E2E)
**Дата:** 2026-02-25
**Тестировщик:** Tester agent
**Окружение:** Ubuntu/Kubuntu, PostgreSQL 5435 (Docker), FastAPI backend port 8001, Vite frontend port 5173

---

## Результаты

- ✅ Smoke-тесты (Backend): PASSED (6/6)
- ✅ Smoke-тесты (Frontend): PASSED (5/5)
- 🔴 Edge cases: FAILED (критические API-несоответствия — 3 из 6)
- ✅ Performance: OK (5 файлов одновременно → обработаны последовательно)
- 🟡 Graceful degradation: CONDITIONAL (работает, но не полностью)

---

## Smoke-тесты

### Backend ✅

| Тест | Статус | Детали |
|------|--------|--------|
| `GET /api/v1/health` | ✅ 200 | DB ok, Whisper lazy ok, LLM degraded (no key), Disk 739GB free |
| `POST /api/v1/upload` | ✅ 200 | Принимает `files[]` + `operator_name`, возвращает `file_ids` |
| `GET /api/v1/results` | ✅ 200 | Пагинация, фильтрация по status/date/score |
| `GET /api/v1/operators?q=...` | ✅ 200 | Autocomplete работает |
| `GET /api/v1/results/{id}` | ✅ 200 | Подробный результат (transcript, segments) |
| `GET /api/v1/audio/{id}` | ✅ 200 | Аудиофайл отдаётся |

### WebSocket ✅

| Тест | Статус | Детали |
|------|--------|--------|
| `ws://localhost:8001/api/v1/ws` | ✅ OK | Подписка по `{file_id}`, возвращает текущий статус |
| Ping/Pong | ✅ OK | `{"type":"ping"}` → `{"type":"pong"}` |
| Invalid file_id | ✅ OK | Возвращает `{"type":"error", "error":"Invalid file_id:..."}` |

### Frontend ✅

| Тест | Статус | Детали |
|------|--------|--------|
| `npm run dev` запускается | ✅ OK | Vite на порту 5173 |
| Главная страница | ✅ OK | Рендерится, UploadZone видна |
| Proxy к API | ✅ OK | `/api/v1/...` через Vite proxy → backend |
| Компоненты загружаются | ✅ OK | React SPA работает |
| Mock fallback | ✅ OK | При недоступном backend → 25 демо-результатов |

### Pipeline (Graceful degradation) ✅

| Этап | Статус | Детали |
|------|--------|--------|
| Upload → Queued | ✅ OK | Сразу в очередь |
| Whisper transcription | ✅ OK | Модель `large-v3` загружается лениво, GPU |
| Diarization | 🟡 DEGRADED | HF_TOKEN не установлен → single-speaker fallback |
| LLM analysis | 🟡 DEGRADED | OPENAI_API_KEY не установлен → graceful skip |
| Status: done | ✅ OK | Pipeline завершается успешно даже без API ключей |

---

## Баги и проблемы

### 🔴 Баг #1 (Критический): OperatorSelector — неверный тип данных

**Файлы:** `frontend/src/lib/api.ts:53`, `frontend/src/components/OperatorSelector.tsx:44`
**Описание:**
- Backend `GET /api/v1/operators` возвращает: `[{id, name, created_at, file_count}]`
- Frontend `fetchOperators()` объявлен как `Promise<string[]>`
- `OperatorSelector` рендерит `{name}` где `name` — это объект `{id, name, ...}`

**Воспроизведение:** Ввести текст в поле "Оператор" при загрузке → dropdown покажет `[object Object]`

**Ожидаемо:** Должны показываться строки (имена операторов)
**Фикс:** В `fetchOperators()` → маппинг `.map(op => op.name)`, или backend → возвращать `string[]`

---

### 🔴 Баг #2 (Критический): Polling fallback — endpoint 404

**Файлы:** `frontend/src/lib/api.ts:65-69`, `frontend/src/hooks/useWebSocket.ts:57-70`
**Описание:**
- `useWebSocket` при N>=3 WS-сбоях переключается на polling
- Polling вызывает `fetchFileStatus(fileId)` → `GET /api/v1/status/{fileId}`
- Этот endpoint **отсутствует** в backend → 404

**Воспроизведение:** Заблокировать WS или вызвать 3+ ошибки → polling → все запросы 404

**Ожидаемо:** Должен использоваться `/api/v1/results/{file_id}` или добавить `/api/v1/status/{file_id}`
**Фикс (вариант 1):** Изменить `fetchFileStatus` в api.ts: `return fetch('/api/v1/results/${fileId}')`
**Фикс (вариант 2):** Добавить endpoint `GET /api/v1/status/{file_id}` в backend

---

### 🔴 Баг #3 (Критический): AnalysisDetail — данные transcript не отображаются

**Файлы:** `frontend/src/components/AnalysisDetail.tsx:90`, `backend/app/routers/results.py:149-167`
**Описание:**
- Backend `/api/v1/results/{id}` возвращает flat-структуру: `{full_text, diarization_method, diarization_confidence, segments}`
- Frontend ожидает nested: `{diarization: {method, confidence, num_speakers, segments}}`
- `detail.diarization?.segments` → всегда `undefined` → TranscriptView **никогда не рендерится**
- `detail.diarization?.num_speakers` → `undefined` → header пустой

**Воспроизведение:** Клик на строку в Results → "Подробный анализ" → блок Транскрипт пустой

**Ожидаемо:** Диалог транскрипта с репликами оператора/клиента

**Фикс:** Маппинг в `fetchResultDetail` (api.ts):
```ts
// Адаптер: flat → nested
const mapped: AnalysisDetailResult = {
  ...data,
  diarization: data.segments?.length > 0 ? {
    method: data.diarization_method,
    confidence: data.diarization_confidence,
    num_speakers: [...new Set(data.segments.map(s => s.speaker))].length,
    segments: data.segments,
  } : undefined,
}
```

---

### 🟡 Баг #4 (Умеренный): Pagination — `limit` vs `page_size`

**Файлы:** `frontend/src/lib/api.ts:90`, `backend/app/routers/results.py:47`
**Описание:**
- Frontend: `params.set('limit', String(limit))`
- Backend: `page_size: int = Query(20, ...)`
- Параметр `limit` игнорируется backend, всегда используется page_size=20

**Воспроизведение:** Изменить кол-во строк на странице (если есть UI) → не работает

**Ожидаемо:** Пагинация должна реагировать на выбор количества строк
**Фикс:** В api.ts: `params.set('page_size', String(limit))` **ИЛИ** в backend: добавить `limit` как alias для `page_size`

---

### 🟡 Баг #5 (Умеренный): FilterBar — operator фильтр по имени vs UUID

**Файлы:** `frontend/src/lib/api.ts:82`, `backend/app/routers/results.py:47`
**Описание:**
- Frontend: `if (filters.operator) params.set('operator', filters.operator)` (строка-имя)
- Backend: `operator_id: uuid.UUID | None = Query(None, ...)` (UUID)
- Параметр `operator` (имя) игнорируется backend

**Воспроизведение:** Фильтр по оператору → таблица не фильтруется по имени

**Фикс:** Добавить endpoint `/results?operator_name=...` ИЛИ frontend отправляет UUID (нужен дополнительный lookup)

---

### 🟡 Замечание #1: React Fragment без ключа в map()

**Файл:** `frontend/src/components/ResultsTable.tsx:242`
**Описание:** `results.map((r) => { return <> ... </> })` — Fragment не имеет `key`
**Ожидаемо:** `<React.Fragment key={r.file_id}> ... </React.Fragment>`
**Последствие:** Предупреждение в консоли: "Each child in a list should have a unique 'key' prop"

---

### 🟡 Замечание #2: Нет заголовка приложения в `<title>`

**Файл:** `frontend/index.html` (или vite config)
**Описание:** `<title>frontend</title>` вместо "Call Analytics"
**Ожидаемо:** Осмысленный заголовок вкладки браузера

---

### ℹ️ Замечание #3: `full_text` транскрипт — не отображается нигде

**Описание:**
- Backend возвращает `full_text: "Субтитры создавал DimaTorzok"` (raw Whisper output)
- В `AnalysisDetail` нет блока для показа `full_text` (только `diarization.segments`)
- Если сегменты пустые — транскрипт полностью скрыт

---

### ✅ Что работает хорошо

- **Graceful degradation**: без API ключей пайплайн завершается, статус `done`, нет крашей
- **WebSocket** (основной путь): подписка, live-прогресс, ping/pong, invalid UUID — всё ✅
- **Дедупликация**: повторный upload того же файла → возвращает тот же `file_id` ✅
- **Конкурентные загрузки**: 5 файлов одновременно → каждый обработан за ~200ms ✅
- **Фильтрация результатов**: по status, date range, min/max score — работает ✅
- **Mock fallback**: при недоступном backend — demo данные показываются ✅
- **AudioPlayer**: использует `audioUrl(fileId)` как fallback — рабочий ✅
- **Валидация загрузки**: неверный формат → 400, пустой operator → 422 ✅
- **Pagination backend**: `page`, `page_size`, `pages` — всё правильно ✅

---

## Performance

| Тест | Результат |
|------|-----------|
| 5 concurrent uploads | 303ms (все приняты) |
| Queue processing (5 файлов, sine wave 5s) | ~200ms каждый (GPU, Whisper) |
| `/api/v1/results` с 7 записями | <50ms |
| Health check | <30ms |

---

## Статус готовности

| Компонент | Статус | Детали |
|-----------|--------|--------|
| Backend API | 🟡 85% | 3 критических бага (polling endpoint, pagination param) |
| Frontend UI | 🟡 75% | OperatorSelector сломан, AnalysisDetail без transcript |
| Интеграция | 🟡 70% | API type mismatches нужно исправить |
| WebSocket | ✅ 95% | Основной путь работает, polling fallback сломан |
| Pipeline | ✅ 90% | Graceful degradation отличный, нужны API ключи |
| DB/Infrastructure | ✅ 100% | PostgreSQL, Docker — работают |

---

## Рекомендации (приоритет)

### Критические (блокируют работу)

1. **Исправить `fetchOperators`** — backend возвращает объекты, frontend ждёт строки
   *Фикс: маппинг в api.ts или изменение контракта backend*

2. **Добавить `/api/v1/status/{file_id}` endpoint** — нужен для polling fallback
   *Или: переключить fetchFileStatus на `/api/v1/results/{file_id}`*

3. **Исправить маппинг AnalysisDetail** — flat → nested diarization структура
   *Фикс: адаптер в fetchResultDetail в api.ts*

### Умеренные (ухудшают UX)

4. **Исправить параметр пагинации**: frontend `limit` → backend `page_size`
5. **Оператор-фильтр**: frontend отправляет имя, backend ждёт UUID — нужен lookup

### Низкий приоритет

6. **React Fragment key** в ResultsTable map
7. **Заголовок браузерной вкладки** — изменить "frontend" на "Call Analytics"
8. **Показать `full_text`** в AnalysisDetail если нет diarization segments

### Инфраструктурные

9. **Docker-compose** — собрать prod-окружение с backend + frontend + PostgreSQL
10. **Error boundaries** в React — глобальная обработка UI ошибок
11. **Установить API ключи** (`OPENAI_API_KEY`, `HF_TOKEN`) для полного pipeline

---

*Отчёт сгенерирован: 2026-02-25, Tester agent*
