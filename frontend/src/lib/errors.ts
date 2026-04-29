// Парсер ответов сервера в человекочитаемые сообщения об ошибках.
// Используется и в обёртке fetch (api.ts), и в XHR upload.

const STATUS_MESSAGES: Record<number, string> = {
  400: 'Неверный запрос',
  401: 'Требуется авторизация',
  403: 'Доступ запрещён',
  404: 'Не найдено',
  413: 'Файл или запрос слишком большой',
  429: 'Слишком много запросов, попробуй позже',
  500: 'Ошибка сервера',
  502: 'Сервер временно недоступен',
  503: 'Сервис временно недоступен',
  504: 'Сервер не успел ответить, попробуй ещё раз',
}

export interface ParsedError {
  message: string                  // главный текст для отображения
  perFile?: Array<{ file: string; error: string }>  // детали валидации per-file (если есть)
  status?: number
}

export function parseHttpError(status: number, body: string): ParsedError {
  const text = body.trim()

  // 1. HTML-ответ (nginx, веб-сервер) — не показываем сырой HTML
  if (text.startsWith('<') || text.toLowerCase().includes('<html')) {
    return {
      message: STATUS_MESSAGES[status] || `Ошибка соединения (${status})`,
      status,
    }
  }

  // 2. JSON с detail
  try {
    const data = JSON.parse(text) as { detail?: unknown; message?: string }
    const detail = data.detail

    if (detail && typeof detail === 'object' && 'details' in detail) {
      // FastAPI validation_error от /upload
      const d = detail as { error?: string; details?: Array<{ file: string; error: string }> }
      const summary = d.details && d.details.length > 0
        ? `Не загружено файлов: ${d.details.length}`
        : (STATUS_MESSAGES[status] || `Ошибка ${status}`)
      return {
        message: summary,
        perFile: d.details,
        status,
      }
    }

    if (typeof detail === 'string') {
      return { message: detail, status }
    }

    if (typeof data.message === 'string') {
      return { message: data.message, status }
    }
  } catch {
    // not JSON, fall through
  }

  // 3. Plain text fallback
  if (text.length > 0 && text.length < 200) {
    return { message: text, status }
  }

  return {
    message: STATUS_MESSAGES[status] || `Ошибка ${status}`,
    status,
  }
}

export function networkError(): ParsedError {
  return {
    message: 'Нет соединения с сервером. Проверь интернет.',
  }
}
