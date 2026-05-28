# Phase: Chunked Upload + Non-Blocking Validation

## Цель

Сделать upload устойчивым к обрывам связи и медленным каналам. После фикса:

- Один обрыв соединения у клиента **не теряет всю партию**, а только текущий чанк
- Прогрессбар у РОП показывает **реальное движение** (N из 153 загружено), а не один зависший процент
- Тяжёлые синхронные операции в upload-хендлере (`ffprobe`, `write_bytes`) **не блокируют event loop**, пока идёт приём следующего чанка
- Существующий workflow Влады **не ломается**: тот же drag-and-drop, та же кнопка, та же страница

## Что НЕ делаем (out of scope)

- **Не** увеличиваем `uvicorn --workers N`. Это потребует выноса `QueueManager` в Redis/БД — отдельная фаза, не в этом фиксе.
- **Не** трогаем pipeline (whisper/gemini/диаризацию). Они уже параллельны через `PIPELINE_CONCURRENCY=4`.
- **Не** меняем формат API `/api/v1/upload`. Чанк — это просто N последовательных вызовов того же эндпоинта.

## Анатомия фикса

### Часть A — backend: снять блокировку event loop

**Файл `backend/app/routers/upload.py`:**

Сейчас в цикле `for upload in files:` (строки 74-150) выполняются:
- `validate_audio_file(...)` → внутри `subprocess.run(ffprobe, timeout=30)` ([audio_validator.py:67](../../../backend/app/services/audio_validator.py#L67)) — **блокирует event loop**
- `_save_file_to_disk(...)` → `dest.write_bytes(content)` ([upload.py:38](../../../backend/app/routers/upload.py#L38)) — **блокирует event loop**

Это значит: пока обрабатывается файл #1, event loop не отвечает на другие async-задачи (приём следующего чанка, `/audio/{id}` для прослушивания, статус-запросы, WS-нотификации pipeline).

**Фикс — обернуть в `asyncio.to_thread`:**

```python
import asyncio
# ...
result = await asyncio.to_thread(
    validate_audio_file, filename, content, existing_hashes=set(hash_to_file_id.keys())
)
# ...
audio_path = await asyncio.to_thread(_save_file_to_disk, file_id, ext, content)
```

Это переносит `ffprobe`-subprocess и `write_bytes` в default thread pool executor (10 потоков по умолчанию). Event loop остаётся свободным.

**Что НЕ выносим в to_thread:**
- SQLAlchemy-операции (`db.flush`, `db.execute`). Они и так sync-через-sync, движок SQLAlchemy не thread-safe для одной сессии. Оставляем как есть.
- `db.commit()` в конце — тоже остаётся sync.

### Часть B — frontend: чанки + per-chunk retry

**Файл `frontend/src/lib/api.ts`:**

Добавляется новая функция `uploadFilesChunked`:

```typescript
export interface ChunkedUploadOptions {
  chunkSize?: number          // default 20
  maxRetries?: number         // default 2 (на чанк, только при NetworkError)
  onChunkProgress?: (chunkIdx: number, totalChunks: number, chunkPercent: number) => void
  onChunkComplete?: (chunkIdx: number, totalChunks: number, result: UploadResponse) => void
  signal?: AbortSignal
}

export async function uploadFilesChunked(
  files: File[],
  operatorName: string,
  opts?: ChunkedUploadOptions
): Promise<UploadResponse> {
  // 1. Разрезать files на чанки по chunkSize
  // 2. Для каждого чанка вызвать uploadFiles(...) с retry-логикой:
  //    - На NetworkError (xhr.onerror) — sleep(1s, 2s) и повторить, до maxRetries
  //    - На HTTP 4xx (валидация) — НЕ ретраить, прокинуть как есть
  //    - На abort — прокинуть сразу
  // 3. Агрегировать ответы: объединить file_ids, accepted, validation_errors, total_files
  // 4. Возвращать совокупный UploadResponse
}
```

Существующая функция `uploadFiles` **остаётся** — это атомарная единица для одного чанка.

**Файл `frontend/src/hooks/useUpload.ts`:**

Переключить `startUpload` на `uploadFilesChunked`. Прогресс считается так:

```
overallProgress = (completedChunksFiles + currentChunkProgress / 100 * currentChunkFiles) / totalFiles * 100
```

После каждого `onChunkComplete` обновляются статусы файлов этого чанка (`done`/`duplicate`/`error`) — РОП видит, что N файлов из 153 уже на сервере и в очереди обработки, **до того, как закончится загрузка остальных**.

### Часть C — обработка отмены и ошибок

- `AbortSignal` пробрасывается в каждый чанк. При cancel — прерывается текущий чанк, остальные не запускаются.
- При HTTP 4xx (валидация) — обрабатываем как сейчас, прокидываем ApiError с per-file ошибками. Чанкинг тут не помогает и не мешает.
- При полном fail сетевого ретрая (3 попытки подряд) — отдаём ошибку, помечаем оставшиеся файлы как `error`. Уже загруженные чанки **сохраняются** в БД, дублей не будет (дедупликация по SHA-256 уже работает).

## Структура коммитов (атомарных)

1. `feat(backend): обернуть ffprobe и write_bytes в asyncio.to_thread`
2. `feat(api): uploadFilesChunked с per-chunk retry и агрегацией ответов`
3. `feat(ui): использовать chunked upload, размер чанка 20, per-chunk обновление статусов`

Каждый коммит **самодостаточен**: после (1) бэк работает с любым фронтом (старым и новым), после (2) функция добавлена и не используется, после (3) фронт переключён.

## План деплоя

### Шаг 0 — pre-flight
- `git status` чисто, на ветке `fix/frontend-backend-contracts`
- На проде та же ветка, тот же коммит (проверил уже: `74e8c5d` совпадает)
- Backup БД свежий (cron работает по памяти)
- Никто не грузит сейчас (РОП работает в МСК-рабочие часы, ночью свободно)

### Шаг 1 — test-сервер 45.152.87.62
- `git push` на ветку
- На тесте `git pull`, `docker compose build backend frontend`, `docker compose up -d --force-recreate backend frontend`
- Smoke-тест: загрузить 30+ файлов через UI, проверить:
  - все попали в БД
  - прогресс идёт пачками
  - при искусственном обрыве (Network throttle в DevTools → Offline → Online) — теряется только текущий чанк
  - pipeline обработал штатно

### Шаг 2 — прод 193.42.125.171 (через safe-deploy)
- `/safe-deploy pre` — паспортная проверка
- `git pull && docker compose build && docker compose up -d --force-recreate backend frontend`
- `/safe-deploy post` — инварианты, smoke-чек /reports, /audio
- Загрузить **малую тестовую партию (10 файлов)** реальной записи РОП → убедиться, что pipeline отработал

### Шаг 3 — связь
- Написать Владе утром: что починили, дать инструкцию (ничего нового, тот же интерфейс, но грузится надёжнее)

### Откат
Если что-то пошло не так на проде:
- `git reset --hard 74e8c5d && docker compose build && docker compose up -d --force-recreate backend frontend`
- В худшем случае: только бэк или только фронт — три коммита атомарные, можно откатить любой

## Риски и контрмеры

| Риск | Контрмера |
|---|---|
| `asyncio.to_thread` сломает что-то в SQLAlchemy/Session | НЕ переносим работу с db в thread, только validate+disk write. Логи теста покажут. |
| Чанкинг сломает дедупликацию | Дедуп на SHA-256, межчанковая работает. Тест с дублем в разных чанках обязателен. |
| ApiError формат "validation_errors" в чанке #3 — фронт не покажет | useUpload уже маппит errorByName из validation_errors. Чанк передаёт совокупный массив. |
| Пользователь отменяет в середине | Уже загруженные чанки остаются в БД (это нормально, дедуп их не пустит при повторе) |
| max_batch_size недостаточен (50000) | 20 файлов на чанк, до 2500 чанков — с запасом |
| `_get_or_create_operator` дёргает БД при каждом файле | Это уже было, не регрессия. Если станет узким — оптимизация отдельно. |
| Прогрессбар прыгает (несколько чанков параллельно) | Чанки шлются **последовательно** (concurrency=1). Параллельность не нужна — сеть всё равно общая. |

## Критерии успеха (verification)

После деплоя на прод:
1. РОП может загрузить 200+ файлов одной партией без жалоб
2. Если в DevTools отключить сеть на 5 секунд во время upload и снова включить — упало максимум 20 файлов, их можно докинуть повторно и они пройдут
3. nginx access-логи показывают **серию коротких** POST `/api/v1/upload` (по 1-3 сек на чанк), а не один длинный POST
4. В backend-логах больше **не видно** "client request body is buffered to a temporary file" warning при типичных партиях (тело каждого чанка ~10 МБ умещается в `client_body_buffer_size`)
5. Pipeline продолжает молотить с той же скоростью (нет регрессии по `Triple merge complete` за единицу времени)

## Memory-лог

После завершения — обновить `project_session_2026_05_28_chunked_upload.md` в `~/.claude/projects/c--Users-User-Desktop-claudeprojects-pirogi-callcenter/memory/`:
- Что было сломано (multi-attempt 400 при медленном канале)
- Что починили
- Как тестировали
- Какие новые риски осознаны
