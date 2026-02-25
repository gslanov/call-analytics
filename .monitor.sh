#!/bin/bash
# Docs Agent File Monitor
# Проверяет изменения в проекте каждые 10 сек и обновляет документацию

PROJECT_DIR="/media/cosmos/2TB/neiro2/call-analytics"
STATE_FILE="$PROJECT_DIR/.monitor_state"
POLL_INTERVAL=10

# Инициализировать состояние
if [ ! -f "$STATE_FILE" ]; then
  echo "# Monitor State File" > "$STATE_FILE"
  find "$PROJECT_DIR" -type f -name "*.py" -o -name "*.tsx" -o -name "*.ts" -o -name "*.json" | sort > "$STATE_FILE.files"
fi

# Функция для проверки изменений
check_changes() {
  local files=$(find "$PROJECT_DIR" -type f \( -name "*.py" -o -name "*.tsx" -o -name "*.ts" -o -name "*.json" -o -name "*.md" \) 2>/dev/null | sort)
  local prev_files=$(cat "$STATE_FILE.files" 2>/dev/null)

  if [ "$files" != "$prev_files" ]; then
    echo "$files" > "$STATE_FILE.files"
    return 0
  fi
  return 1
}

# Основной цикл мониторинга
while true; do
  if check_changes; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Обнаружены изменения в файловой системе"
    # Сигнал: есть новые файлы для документирования
    touch "$STATE_FILE.updated"
  fi
  sleep "$POLL_INTERVAL"
done
