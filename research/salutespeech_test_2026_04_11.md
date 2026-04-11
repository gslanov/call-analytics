# SaluteSpeech API — тест 2026-04-11

## Credentials
- Client ID: 019d7dc3-a15b-7014-9ad2-94d4dd551b0a
- Scope: SALUTE_SPEECH_PERS
- Authorization Key (base64): MDE5ZDdkYzMtYTE1Yi03MDE0LTlhZDItOTRkNGRkNTUxYjBhOjlkZDQ1OWMwLTc1ZDctNGJhMy05MzQzLWQ4YWYwYWI3ZjQ4Yw==
- OAuth: POST https://ngw.devices.sberbank.ru:9443/api/v2/oauth
- Токен живет 30 минут
- ВАЖНО: credentials перестали работать после нескольких запросов (code 6: "credentials doesn't match db data"). Возможно rate limit или одноразовость.

## Тестовый файл
`callsrec/stereo/2026-04-04__19-51-19__79252463351__Менеджер Галина.mp3` (2 МБ, ~8.5 мин, стерео)

## API endpoints
- Получить токен: `POST https://ngw.devices.sberbank.ru:9443/api/v2/oauth`
- Синхронное (до 1 мин): `POST https://smartspeech.sber.ru/rest/v1/speech:recognize`
- Загрузить файл: `POST https://smartspeech.sber.ru/rest/v1/data:upload`
- Асинхронное: `POST https://smartspeech.sber.ru/rest/v1/speech:async_recognize`
- Статус задачи: `GET https://smartspeech.sber.ru/rest/v1/task:get?id=...`
- Скачать результат: `GET https://smartspeech.sber.ru/rest/v1/data:download?response_file_id=...`
- ВАЖНО: для всех запросов нужен `-k` (skip SSL verify)

## Результаты

### Модель
- Автоматически выбрана: `callcenter M-03.006.00-callcenter-01`
- Сервер: `03.007.01-rh8-trt10-cuda12-01`

### Качество текста (моно, без стерео-разделения)
Ошибки:
- "Пироги номер один" → "прогноз" (hints НЕ помогли)
- "осетинский" → "Просперо Матинский" (hints НЕ помогли)
- "облепиха" → "2 лепиха" (hints НЕ помогли)
- "именинника" → "винника"
- "please hold on for a while" → "плиз, холд он фо э вай" (английский транслитерирован в русский)
- "хачапури" → "глухой сыр" в одном месте (но правильно в синхронном)

Правильно:
- "Галина" ✓
- "Варшавское шоссе" ✓
- "QR-коду" ✓
- "бонусы" ✓
- "доставку" ✓
- Пунктуация в normalized_text ✓
- Word timestamps ✓

### Диаризация
- speaker_separation_options: enable=true, count=2 — НЕ СРАБОТАЛА
- Все сегменты: channel=0, speaker_id=-1
- Стерео не помогло — API смешал в моно
- Возможно нужен channels_count=2 в options (2й тест отправлен, но токен истёк до получения результата)

### Эмоции
- emotions_result есть в каждом сегменте (positive/neutral/negative)
- insight_models=["sentiment"] вызывает ошибку на PERS-тарифе
- Но emotions_result приходит и без insight_models!

### Структура ответа
- 58 сегментов, 34 с текстом
- Каждый сегмент: text (raw), normalized_text (с пунктуацией), start/end, word_alignments[], emotions_result, channel, speaker_info, eou_reason
- word_alignments — посимвольные таймстемпы каждого слова
- eou_reason: "ORGANIC" (естественная пауза)

## Сравнение с текущим gpt-4o-transcribe
| Параметр | gpt-4o-transcribe | SaluteSpeech |
|----------|-------------------|-------------|
| "Пироги номер один" | ✓ (domain prompt) | ✗ (hints не помогли) |
| "осетинский" | ✓ | ✗ ("Просперо Матинский") |
| "хачапури" | ✓ | ± (иногда "глухой сыр") |
| "облепиха" | ✓ | ✗ ("2 лепиха") |
| "Варшавское шоссе" | ✓ | ✓ |
| Word timestamps | ✗ (gpt-4o-transcribe не дает) | ✓ |
| Пунктуация | ✓ | ✓ |
| Цена за 11,700 мин/мес | ~6,500₽ | ~14,000₽ |
| Модель | general | callcenter-специализированная |

## Выводы
1. **Hints не работают** для ключевых доменных слов — это критично для нашего кейса
2. **Диаризация не сработала** — нужно разбираться с channels_count
3. **Word timestamps — большой плюс** (gpt-4o-transcribe их не дает)
4. **Модель callcenter** автоматически выбрана — хорошо
5. **Цена 2x от OpenAI** — 14,000₽ vs 6,500₽/мес
6. **Качество ХУЖЕ gpt-4o-transcribe** для нашего кейса из-за доменной лексики
7. Нужно: попробовать erase_personal_data, channels_count, другие форматы hints

## Незавершённые задачи
- [ ] Скачать результат стерео-задачи 6f03011b8a0d58f6df8c122550d3d5a7 (токен истёк)
- [ ] Разобраться почему credentials перестали работать
- [ ] Протестировать с channels_count=2 для стерео
