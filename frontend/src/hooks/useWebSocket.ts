import { useEffect, useRef, useState, useCallback } from 'react'
import type { FileProgress, ProcessingFile, ProcessingStatus } from '../types'
import { fetchFileStatus, WS_URL } from '../lib/api'

const STAGE_NAMES = ['Очередь', 'Валидация', 'Транскрибация', 'Диаризация', 'Анализ']
const POLL_INTERVAL_MS = 3000
const MAX_WS_FAILURES = 3
const BASE_RECONNECT_MS = 1000
const MAX_RECONNECT_MS = 30000

function makeInitialProgress(files: ProcessingFile[]): Record<string, FileProgress> {
  return Object.fromEntries(
    files.map((f) => [
      f.file_id,
      {
        file_id: f.file_id,
        file_name: f.file_name,
        status: 'queued' as ProcessingStatus,
        stage: 0,
        stage_name: STAGE_NAMES[0],
        progress: 0,
      },
    ])
  )
}

export function useWebSocket(files: ProcessingFile[]) {
  const [progress, setProgress] = useState<Record<string, FileProgress>>(() =>
    makeInitialProgress(files)
  )
  const [wsConnected, setWsConnected] = useState(false)
  const [usingPolling, setUsingPolling] = useState(false)

  const wsRef = useRef<WebSocket | null>(null)
  const wsFailuresRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const mountedRef = useRef(true)
  const fileIdsRef = useRef(files.map((f) => f.file_id))

  const applyUpdate = useCallback((update: Partial<FileProgress> & { file_id: string }) => {
    setProgress((prev) => {
      const existing = prev[update.file_id]
      if (!existing) return prev
      return {
        ...prev,
        [update.file_id]: { ...existing, ...update },
      }
    })
  }, [])

  // ── Polling fallback ──────────────────────────────────────────────────────
  const startPolling = useCallback(() => {
    if (pollTimerRef.current) return
    setUsingPolling(true)

    const poll = async () => {
      for (const fileId of fileIdsRef.current) {
        try {
          const s = await fetchFileStatus(fileId)
          if (!mountedRef.current) return
          applyUpdate({
            file_id: s.file_id,
            status: s.status as ProcessingStatus,
            stage: s.stage,
            stage_name: s.stage_name || STAGE_NAMES[s.stage] || '',
            progress: s.progress,
            error: s.error_message,
          })
        } catch {
          // backend not ready yet — silent
        }
      }
    }

    poll()
    pollTimerRef.current = setInterval(poll, POLL_INTERVAL_MS)
  }, [applyUpdate])

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current)
      pollTimerRef.current = null
    }
    setUsingPolling(false)
  }, [])

  // ── WebSocket ─────────────────────────────────────────────────────────────
  const connect = useCallback(() => {
    if (!mountedRef.current) return
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    let ws: WebSocket
    try {
      ws = new WebSocket(WS_URL)
    } catch {
      wsFailuresRef.current += 1
      if (wsFailuresRef.current >= MAX_WS_FAILURES) startPolling()
      return
    }

    wsRef.current = ws

    ws.onopen = () => {
      if (!mountedRef.current) { ws.close(); return }
      wsFailuresRef.current = 0
      setWsConnected(true)
      stopPolling()
      // Subscribe to all file IDs
      fileIdsRef.current.forEach((id) =>
        ws.send(JSON.stringify({ file_id: id }))
      )
    }

    ws.onmessage = (event) => {
      if (!mountedRef.current) return
      try {
        const msg = JSON.parse(event.data as string) as Record<string, unknown>
        if (!msg.file_id) return

        if (msg.type === 'progress') {
          applyUpdate({
            file_id: msg.file_id as string,
            status: (msg.status as ProcessingStatus) ?? 'queued',
            stage: (msg.stage as number) ?? 0,
            stage_name: (msg.stage_name as string) ?? '',
            progress: (msg.progress as number) ?? 0,
          })
        } else if (msg.type === 'complete') {
          applyUpdate({
            file_id: msg.file_id as string,
            status: 'done',
            stage: 4,
            stage_name: 'Готово',
            progress: 100,
          })
        } else if (msg.type === 'error') {
          applyUpdate({
            file_id: msg.file_id as string,
            status: 'failed',
            error: (msg.error as string) ?? 'Ошибка обработки',
          })
        }
      } catch {
        // ignore malformed message
      }
    }

    ws.onerror = () => {
      // onclose fires after onerror automatically
    }

    ws.onclose = () => {
      if (!mountedRef.current) return
      setWsConnected(false)
      wsFailuresRef.current += 1

      if (wsFailuresRef.current >= MAX_WS_FAILURES) {
        startPolling()
        return
      }

      // Exponential backoff reconnect
      const delay = Math.min(
        BASE_RECONNECT_MS * 2 ** (wsFailuresRef.current - 1),
        MAX_RECONNECT_MS
      )
      reconnectTimerRef.current = setTimeout(connect, delay)
    }
  }, [applyUpdate, startPolling, stopPolling])

  useEffect(() => {
    mountedRef.current = true
    fileIdsRef.current = files.map((f) => f.file_id)
    setProgress(makeInitialProgress(files))

    connect()

    return () => {
      mountedRef.current = false
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
      if (pollTimerRef.current) clearInterval(pollTimerRef.current)
      wsRef.current?.close()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])   // run once on mount; files are stable after upload

  return { progress, wsConnected, usingPolling }
}
