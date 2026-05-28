import { parseHttpError, networkError, type ParsedError } from './errors'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'

export interface AcceptedFile {
  file_id: string
  original_name: string
  is_duplicate: boolean
}

export interface UploadResponse {
  // accepted — новый формат от бэка после фикса mapping bug; старый бэк его не пришлёт
  accepted?: AcceptedFile[]
  file_ids: string[]
  operator: string
  status: string
  total_files: number
  validation_errors?: Array<{ file: string; error: string }>
}

export interface ValidationError {
  error: string
  details: Array<{ file: string; error: string }>
}

export class ApiError extends Error {
  parsed: ParsedError
  constructor(parsed: ParsedError) {
    super(parsed.message)
    this.name = 'ApiError'
    this.parsed = parsed
  }
}

export async function uploadFiles(
  files: File[],
  operatorName: string,
  onProgress?: (percent: number) => void,
  signal?: AbortSignal,
): Promise<UploadResponse> {
  return new Promise((resolve, reject) => {
    const formData = new FormData()
    files.forEach((file) => formData.append('files', file))
    formData.append('operator_name', operatorName)

    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_BASE_URL}/upload`)

    if (signal) {
      const onAbort = () => xhr.abort()
      signal.addEventListener('abort', onAbort, { once: true })
    }

    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          onProgress(Math.round((e.loaded / e.total) * 100))
        }
      }
    }

    xhr.onload = () => {
      if (xhr.status === 200) {
        try {
          resolve(JSON.parse(xhr.responseText) as UploadResponse)
        } catch {
          reject(new ApiError({ message: 'Сервер вернул некорректный ответ' }))
        }
      } else {
        reject(new ApiError(parseHttpError(xhr.status, xhr.responseText)))
      }
    }

    xhr.onerror = () => reject(new ApiError(networkError()))
    xhr.onabort = () => reject(new ApiError({ message: 'Загрузка отменена' }))
    xhr.send(formData)
  })
}

// Чанковый upload: режет большой батч на пачки по chunkSize, шлёт их
// последовательно. При сетевой ошибке ретраит чанк (не весь батч).
// Уже залитые чанки сохраняются в БД на сервере (дедуп по SHA-256 не пустит
// дубли при повторной загрузке тех же файлов).

export interface ChunkedUploadOptions {
  chunkSize?: number          // default 20
  maxRetries?: number         // default 2 — только при сетевых ошибках, не при HTTP 4xx
  retryDelayMs?: number       // default 1000 — стартовая задержка, удваивается на каждый ретрай
  onOverallProgress?: (percent: number) => void
  onChunkComplete?: (chunkIdx: number, totalChunks: number, result: UploadResponse) => void
  signal?: AbortSignal
}

// Сетевая ошибка: ApiError без status (см. networkError() в errors.ts).
// Abort: ApiError с message === 'Загрузка отменена'.
// HTTP 4xx/5xx: ApiError с status — не ретраим.
function isRetryableNetworkError(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false
  if (err.parsed.status !== undefined) return false
  if (err.parsed.message === 'Загрузка отменена') return false
  return true
}

function sleepWithAbort(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new ApiError({ message: 'Загрузка отменена' }))
      return
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    const onAbort = () => {
      clearTimeout(timer)
      reject(new ApiError({ message: 'Загрузка отменена' }))
    }
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

export async function uploadFilesChunked(
  files: File[],
  operatorName: string,
  opts: ChunkedUploadOptions = {},
): Promise<UploadResponse> {
  const chunkSize = opts.chunkSize ?? 20
  const maxRetries = opts.maxRetries ?? 2
  const baseDelay = opts.retryDelayMs ?? 1000
  const signal = opts.signal

  const chunks: File[][] = []
  for (let i = 0; i < files.length; i += chunkSize) {
    chunks.push(files.slice(i, i + chunkSize))
  }

  // Если пачка одна — нет смысла в чанкинге, используем uploadFiles напрямую,
  // прогресс будет точный как раньше.
  if (chunks.length <= 1) {
    return uploadFiles(files, operatorName, opts.onOverallProgress, signal)
  }

  const aggregated: UploadResponse = {
    accepted: [],
    file_ids: [],
    operator: operatorName,
    status: 'queued',
    total_files: 0,
    validation_errors: [],
  }

  let filesDoneBefore = 0

  for (let chunkIdx = 0; chunkIdx < chunks.length; chunkIdx++) {
    if (signal?.aborted) throw new ApiError({ message: 'Загрузка отменена' })

    const chunk = chunks[chunkIdx]
    let attempt = 0

    while (true) {
      try {
        const result = await uploadFiles(
          chunk,
          operatorName,
          (chunkPercent) => {
            if (!opts.onOverallProgress) return
            const overallFilesDone = filesDoneBefore + (chunkPercent / 100) * chunk.length
            opts.onOverallProgress(Math.round((overallFilesDone / files.length) * 100))
          },
          signal,
        )

        aggregated.file_ids.push(...result.file_ids)
        aggregated.total_files += result.total_files
        if (result.accepted && result.accepted.length > 0) {
          aggregated.accepted = [...(aggregated.accepted ?? []), ...result.accepted]
        }
        if (result.validation_errors && result.validation_errors.length > 0) {
          aggregated.validation_errors = [
            ...(aggregated.validation_errors ?? []),
            ...result.validation_errors,
          ]
        }
        if (result.operator) aggregated.operator = result.operator

        opts.onChunkComplete?.(chunkIdx, chunks.length, result)
        filesDoneBefore += chunk.length
        opts.onOverallProgress?.(Math.round((filesDoneBefore / files.length) * 100))
        break
      } catch (err) {
        if (signal?.aborted) throw err
        if (!isRetryableNetworkError(err) || attempt >= maxRetries) throw err

        attempt += 1
        await sleepWithAbort(baseDelay * attempt, signal)  // 1s, 2s
      }
    }
  }

  return aggregated
}

export async function fetchOperators(query: string): Promise<string[]> {
  const url = `${API_BASE_URL}/operators?q=${encodeURIComponent(query)}`
  const response = await fetch(url)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const data = await response.json()
  // Normalise: backend may return string[] OR {id, name}[]
  if (Array.isArray(data) && data.length > 0 && typeof data[0] === 'object' && data[0] !== null) {
    return (data as Array<{ id?: string; name?: string; [k: string]: unknown }>).map(
      (op) => String(op.name ?? op.id ?? '')
    )
  }
  return data as string[]
}

export interface FileStatusResponse {
  file_id: string
  status: string
  stage: number
  stage_name: string
  progress: number
  error_message?: string
}

export async function fetchFileStatus(fileId: string): Promise<FileStatusResponse> {
  const response = await fetch(`${API_BASE_URL}/status/${fileId}`)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json() as Promise<FileStatusResponse>
}

export const WS_URL = import.meta.env.VITE_WS_URL ??
  `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/api/v1/ws`

import type { ResultFilters, ResultsPage, AnalysisDetailResult, CriteriaReportData } from '../types'

function buildResultsParams(filters: ResultFilters): URLSearchParams {
  const params = new URLSearchParams()
  if (filters.operator) params.set('operator', filters.operator)
  if (filters.date_from) params.set('date_from', filters.date_from)
  if (filters.date_to) params.set('date_to', filters.date_to)
  if (filters.score_min != null) params.set('score_min', String(filters.score_min))
  if (filters.score_max != null) params.set('score_max', String(filters.score_max))
  if (filters.sort) params.set('sort', filters.sort)
  if (filters.order) params.set('order', filters.order)
  return params
}

export function buildResultsExportUrl(filters: ResultFilters): string {
  const params = buildResultsParams(filters)
  const qs = params.toString()
  return `${API_BASE_URL}/results/export${qs ? '?' + qs : ''}`
}

export interface CriteriaReportFilters {
  date_from?: string
  date_to?: string
  operator?: string
}

export async function fetchCriteriaReport(
  filters: CriteriaReportFilters
): Promise<CriteriaReportData> {
  const params = new URLSearchParams()
  if (filters.date_from) params.set('date_from', filters.date_from)
  if (filters.date_to) params.set('date_to', filters.date_to)
  if (filters.operator) params.set('operator', filters.operator)
  const qs = params.toString()
  const response = await fetch(`${API_BASE_URL}/reports/criteria${qs ? '?' + qs : ''}`)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json() as Promise<CriteriaReportData>
}

export function buildCriteriaReportDownloadUrl(filters: CriteriaReportFilters): string {
  const params = new URLSearchParams()
  if (filters.date_from) params.set('date_from', filters.date_from)
  if (filters.date_to) params.set('date_to', filters.date_to)
  if (filters.operator) params.set('operator', filters.operator)
  const qs = params.toString()
  return `${API_BASE_URL}/reports/criteria/download${qs ? '?' + qs : ''}`
}

export async function fetchResults(
  filters: ResultFilters,
  page: number,
  limit: number
): Promise<ResultsPage> {
  const params = buildResultsParams(filters)
  params.set('page', String(page))
  params.set('limit', String(limit))

  const response = await fetch(`${API_BASE_URL}/results?${params.toString()}`)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json() as Promise<ResultsPage>
}

export async function fetchResultDetail(fileId: string): Promise<AnalysisDetailResult> {
  const response = await fetch(`${API_BASE_URL}/results/${fileId}`)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json() as Promise<AnalysisDetailResult>
}

export async function deleteResult(fileId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/results/${fileId}`, { method: 'DELETE' })
  if (!response.ok) throw new Error(`Delete failed: ${response.statusText}`)
}

export async function rejectAnalysis(fileId: string, reason: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/results/${fileId}/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  })
  if (!response.ok) throw new Error(`Reject failed: ${response.statusText}`)
}

export interface UpdateCriterionResponse {
  file_id: string
  standard: number
  loyalty: number
  kindness: number
  overall: number
  criteria_details: Record<string, unknown>
}

export async function updateCriterion(
  fileId: string,
  group: string,
  key: string,
  value: boolean | null
): Promise<UpdateCriterionResponse> {
  const response = await fetch(`${API_BASE_URL}/results/${fileId}/criteria`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ group, key, value }),
  })
  if (!response.ok) throw new Error(`Update failed: ${response.statusText}`)
  return response.json() as Promise<UpdateCriterionResponse>
}

export function audioUrl(fileId: string): string {
  return `${API_BASE_URL}/audio/${fileId}`
}

export interface FtpFile {
  id: string
  filename: string
  size: number
  date: string
  duration_sec: number | null
  // Calltouch metadata
  callerphone?: string
  calledphone?: string
  operatorphone?: string
  calltouch_duration?: number
  order_id?: string
  lead_name?: string
}

export interface FtpFilesPage {
  items: FtpFile[]
  total: number
  page: number
  limit: number
}

export interface FtpFilters {
  q?: string
  date_from?: string
  date_to?: string
  duration_min?: number
  duration_max?: number
  callerphone?: string
  operatorphone?: string
  order_id?: string
}

export async function fetchFtpFiles(
  filters: FtpFilters,
  page: number,
  limit: number
): Promise<FtpFilesPage> {
  const params = new URLSearchParams()
  if (filters.q) params.set('q', filters.q)
  if (filters.date_from) params.set('date_from', filters.date_from)
  if (filters.date_to) params.set('date_to', filters.date_to)
  if (filters.duration_min != null) params.set('duration_min', String(filters.duration_min))
  if (filters.duration_max != null) params.set('duration_max', String(filters.duration_max))
  if (filters.callerphone) params.set('callerphone', filters.callerphone)
  if (filters.operatorphone) params.set('operatorphone', filters.operatorphone)
  if (filters.order_id) params.set('order_id', filters.order_id)
  params.set('page', String(page))
  params.set('limit', String(limit))
  const response = await fetch(`${API_BASE_URL}/sftp/files?${params.toString()}`)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json() as Promise<FtpFilesPage>
}

export function ftpStreamUrl(filename: string): string {
  return `${API_BASE_URL}/sftp/files/${encodeURIComponent(filename)}/stream`
}

export function ftpDownloadUrl(filename: string): string {
  return `${API_BASE_URL}/sftp/files/${encodeURIComponent(filename)}/download`
}

export async function sendFtpToWhisper(
  filenames: string[],
  operatorName?: string
): Promise<UploadResponse> {
  const response = await fetch(`${API_BASE_URL}/sftp/process`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filenames, operator_name: operatorName }),
  })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json() as Promise<UploadResponse>
}

// Calltouch JSON параметры
export async function fetchAvailableJsonFields(): Promise<{ fields: string[] }> {
  const response = await fetch(`${API_BASE_URL}/calltouch/available-fields`)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json() as Promise<{ fields: string[] }>
}

export interface CalltouchSearchResult {
  calltouch_id: string
  callerphone?: string
  operatorphone?: string
  order_id?: string
  field: string
  field_value: unknown
  raw_data: Record<string, unknown>
}

export async function searchCalltouchByField(
  field: string,
  value: string,
  limit?: number
): Promise<{ results: CalltouchSearchResult[]; count: number }> {
  const params = new URLSearchParams()
  params.set('field', field)
  params.set('value', value)
  if (limit) params.set('limit', String(limit))
  const response = await fetch(`${API_BASE_URL}/calltouch/search-by-field?${params}`)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json() as Promise<{ results: CalltouchSearchResult[]; count: number }>
}
