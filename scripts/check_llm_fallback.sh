#!/usr/bin/env bash
# Алерт: ловим момент, когда анализ звонков идёт НЕ на основной модели (Gemini),
# а на запасной (gpt-5-mini / gpt-5.4) — значит у Gemini кончились кредиты / упал прокси.
#
# Корень инцидента 01.06.2026: prepay Gemini в Google AI Studio кончились, весь поток
# молча ушёл на gpt-5-mini, качество просело, узнали об этом от РОП через 3 дня.
# Этот скрипт ловит такое за час, а не за неделю.
#
# Запуск по cron на ПРОДЕ (хост, не контейнер), напр. каждый час:
#   0 * * * * ALERT_WEBHOOK_URL='https://...' /root/call-analytics/scripts/check_llm_fallback.sh >> /var/log/llm_fallback.log 2>&1
#
# ALERT_WEBHOOK_URL (опц.) — любой webhook, принимающий JSON {"text": "..."}
#   (Telegram через прокси / Slack / Discord / mattermost). Если не задан — только лог + exit code.
#
# Логика: смотрим последние 30 анализов. Если доля основной модели < порога — ALERT.
set -euo pipefail

PRIMARY_MODEL="${PRIMARY_MODEL:-gemini-3-flash-preview}"
DB_CONTAINER="${DB_CONTAINER:-call-analytics-db}"
DB_USER="${DB_USER:-callanalytics}"
DB_NAME="${DB_NAME:-callanalytics}"
SAMPLE="${SAMPLE:-30}"          # сколько последних анализов смотрим
MIN_PRIMARY_PCT="${MIN_PRIMARY_PCT:-50}"  # ниже этого % основной модели = тревога
NOW="$(date '+%Y-%m-%d %H:%M:%S')"

# Запрос с ЯВНОЙ проверкой rc: если БД/контейнер упал — это отдельная тревога,
# а не тихий «нет анализов → OK» (иначе скрипт прозевал бы саму аварию БД).
if ! out="$(docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t -A -F' ' -c \
  "SELECT count(*),
          count(*) FILTER (WHERE llm_model = '${PRIMARY_MODEL}')
   FROM (SELECT llm_model FROM analyses ORDER BY created_at DESC LIMIT ${SAMPLE}) s;" 2>&1)"; then
  echo "[$NOW] ALERT: psql/БД недоступна — $out"
  if [ -n "${ALERT_WEBHOOK_URL:-}" ]; then
    curl -s -m 15 -X POST "$ALERT_WEBHOOK_URL" -H 'Content-Type: application/json' \
      -d '{"text": "⚠ Call-analytics: psql/БД недоступна — мониторинг модели не смог отработать"}' >/dev/null || true
  fi
  exit 2
fi

read -r total primary <<<"$out"
total="${total:-0}"; primary="${primary:-0}"

if [ "$total" -eq 0 ]; then
  echo "[$NOW] check_llm_fallback: нет свежих анализов — пропуск"
  exit 0
fi

pct=$(( primary * 100 / total ))

if [ "$pct" -ge "$MIN_PRIMARY_PCT" ]; then
  echo "[$NOW] OK: основная модель ${PRIMARY_MODEL} = ${pct}% из последних ${total}"
  exit 0
fi

# --- ТРЕВОГА: работаем на запасной модели ---
# какая модель реально доминирует сейчас
fallback="$(docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t -A -c \
  "SELECT llm_model FROM (SELECT llm_model FROM analyses ORDER BY created_at DESC LIMIT ${SAMPLE}) s
   GROUP BY llm_model ORDER BY count(*) DESC LIMIT 1;")"

MSG="⚠ Call-analytics: анализ идёт на ЗАПАСНОЙ модели «${fallback}» (основная ${PRIMARY_MODEL} = ${pct}% из ${total}). Вероятно у Gemini кончились кредиты в Google AI Studio. Качество распознавания просело — пополнить баланс."
echo "[$NOW] ALERT: $MSG"

if [ -n "${ALERT_WEBHOOK_URL:-}" ]; then
  curl -s -m 15 -X POST "$ALERT_WEBHOOK_URL" \
    -H 'Content-Type: application/json' \
    -d "$(printf '{"text": %s}' "$(printf '%s' "$MSG" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")" \
    >/dev/null || echo "[$NOW] webhook POST failed"
fi

exit 1
