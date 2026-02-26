# HANDOFF — call-analytics

## Запрос пользователя
Разработать и развернуть веб-приложение для аналитики звонков операторов с функционалом загрузки аудиофайлов, транскрибации (Whisper), анализа по критериям (Стандарты, Лояльность, Доброжелательность) с выводом результатов в процентах. Развернуть на облачный сервер (IP: 23.94.143.122) с интеграцией МАНГО Office FTP для автоматической синхронизации файлов.

## Текущий этап
✅ **ПОЛНОСТЬЮ ЗАВЕРШЕН**
- ✅ Vision Team (дизайн архитектуры)
- ✅ Dev Team (реализация frontend/backend)
- ✅ Deployment (Docker, Kubernetes-ready)
- ✅ Сервер (23.94.143.122 онлайн и работает)

## Статус развертывания
🚀 **LIVE на производстве**

| Сервис | Статус | Порт | Описание |
|--------|--------|------|---------|
| Frontend (React) | ✅ Running | 3000 | Web UI - http://23.94.143.122:3000 |
| Backend (FastAPI) | ✅ Running | 8001 | API - http://23.94.143.122:8001/docs |
| Database (PostgreSQL) | ✅ Healthy | 5432 | Persistent storage |
| МАНГО Sync | ✅ Running | - | Scheduled FTP sync daily + 6h fallback |

## Технический стек
- **Frontend**: React 18 + TypeScript + Vite + TailwindCSS
- **Backend**: FastAPI + Python 3.11 + SQLAlchemy + Alembic
- **Audio Processing**: Whisper (OpenAI) + pyannote.audio (speaker diarization)
- **AI Analysis**: GPT-4 API (quality evaluation)
- **Database**: PostgreSQL 15 (persistent)
- **Deployment**: Docker Compose (4 containers)
- **Sync**: МАНГО FTP service (Python schedule)

## Развернутые компоненты
### Backend API Endpoints
- `POST /api/v1/upload` - Загрузка аудиофайлов с валидацией
- `GET /api/v1/results` - Список результатов с фильтрацией/пагинацией
- `GET /api/v1/results/{id}` - Детали анализа конкретного файла
- `GET /api/v1/status/{id}` - Статус обработки файла
- `GET /api/v1/operators` - Автоcompletion для операторов
- `GET /api/v1/audio/{id}` - Streaming аудиофайла
- `WS /api/v1/ws` - WebSocket для live-прогресса
- `GET /api/v1/health` - Health check

### Frontend Components
- `UploadZone` - Drag-drop загрузка файлов
- `OperatorSelector` - Autocomplete выбор оператора
- `ProgressView` - 5-stage pipeline visualization (WebSocket)
- `ResultsTable` - Таблица результатов с фильтрацией
- `AnalysisDetail` - Детальный просмотр оценок + транскрибация + плеер
- `FilterBar` - Фильтры по оператору/дате/оценке
- `SummaryCards` - Средние оценки по выбранному фильтру

### Database Schema (Alembic migrations)
- `operators` - Список операторов
- `files` - Метаданные загруженных файлов
- `transcriptions` - Результаты транскрибации
- `diarizations` - Результаты speaker diarization
- `analyses` - Результаты AI анализа (Standards/Loyalty/Kindness scores)

## Конфигурация сервера
```
Server IP: 23.94.143.122
User: root
App Directory: /app/call-analytics
Database: /app/call-analytics/data/db
Uploads: /app/call-analytics/data/uploads
МАНГО Sync: /app/call-analytics/data/mango_sync
```

##环境переменные (.env)
```
MANGO_FTP_HOST=your-ftp-server.com
MANGO_FTP_USER=your_username
MANGO_FTP_PASSWORD=your_password
OPENAI_API_KEY=sk-xxx (опционально)
POSTGRES_USER=call_analytics
POSTGRES_PASSWORD=SecureDBPassword123!
DB_NAME=call_analytics
FASTAPI_PORT=8001
FRONTEND_PORT=3000
```

## Git История
```
✅ 8b20206 - fix: use Node.js 20 for frontend build
✅ 0418ac2 - fix: add missing frontend Dockerfile
✅ 1b85d44 - chore: add docker-compose configuration
✅ 4c6978e - docs: add GitHub deployment guide
✅ 5dd055f - Initial commit: full system with design docs
```

## SFTP Сервер — Конфигурация ✅ ЗАВЕРШЕНО

### Статус настройки (2026-02-26)
✅ **SFTP сервер полностью настроен и готов к работе**

- ✅ Пользователь `mango_sftp` создан
- ✅ SSH конфиг обновлен с chroot для безопасности
- ✅ `.env` обновлен с SFTP реквизитами
- ✅ Docker-compose контейнеры перезагружены
- ✅ Синхронизация записей активирована

### Реквизиты подключения SFTP
```
Хост:     23.94.143.122
Порт:     22 (SFTP)
User:     mango_sftp
Password: Mango@SFTP2024!
Path:     /uploads (chroot в /app/call-analytics/data/mango_sftp)
```

### Текущий .env на сервере
```
MANGO_FTP_HOST=23.94.143.122
MANGO_FTP_PORT=22
MANGO_FTP_USER=mango_sftp
MANGO_FTP_PASSWORD=Mango@SFTP2024!
MANGO_FTP_TYPE=sftp
MANGO_FTP_PATH=/uploads
```

### Проверка работы
```bash
# Проверить подключение
sftp mango_sftp@23.94.143.122

# Загрузить тестовый файл
echo "test" | sftp mango_sftp@23.94.143.122 -b - << EOF
put test.wav
quit
EOF

# Проверить логи синхро
docker-compose logs -f mango-sync
```

**Подробнее**: см. [SFTP_CONFIG.md](./SFTP_CONFIG.md)

---

## Следующие шаги

1. **Опционально**: Добавить OpenAI API key для GPT-4 анализа
   ```bash
   ssh root@23.94.143.122
   echo "OPENAI_API_KEY=sk-xxx" >> /app/call-analytics/.env
   docker-compose restart backend
   ```

2. **Мониторинг**: Проверить логи
   ```bash
   docker-compose logs -f backend
   docker-compose logs -f mango-sync
   ```

3. **Тестирование**: Загрузить тестовый файл
   ```bash
   curl -X POST http://23.94.143.122:8001/api/v1/upload \
     -F "files=@test_call.mp3" \
     -F "operator_name=John"
   ```

## Известные ограничения & TODO
- [ ] GitHub integration (готов, ждет PAT для push)
- [ ] SSL/HTTPS (необходимо для production)
- [ ] Monitoring & alerting
- [ ] Backup strategy для PostgreSQL
- [ ] Load balancing (если >1000 files/day)
- [ ] Error handling graceful degradation (4 levels) - реализовано в pipeline
- [x] SFTP сервер для загрузки записей — ✅ ГОТОВО

## Файлы документации
- `VISION.md` - Vision team видение проекта
- `ARCHITECTURE.md` - Детальная архитектура системы
- `CONVENTIONS.md` - Code style конвенции
- `GITHUB_DEPLOYMENT.md` - Гайд по GitHub и CD/CD
- `TEST_REPORT.md` - Результаты E2E тестирования
- `PROJECT_DOCS.md` - Полная документация проекта

## Контакты для поддержки
- Сервер: 23.94.143.122 (root/660t8mCNQ0Slf5KxjL)
- Frontend: http://23.94.143.122:3000
- API Docs: http://23.94.143.122:8001/docs
- GitHub: https://github.com/g.slanov/call-analytics (待推送)

---
**Статус**: ✅ ГОТОВО К PRODUCTION
**Дата развертывания**: 2026-02-25
**Версия**: 1.0.0-production
