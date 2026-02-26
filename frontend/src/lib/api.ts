const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'

export interface UploadResponse {
  file_ids: string[]
  operator: string
  status: string
  total_files: number
}

export interface ValidationError {
  error: string
  details: Array<{ file: string; error: string }>
}

export async function uploadFiles(
  files: File[],
  operatorName: string,
  onProgress?: (percent: number) => void
): Promise<UploadResponse> {
  return new Promise((resolve, reject) => {
    const formData = new FormData()
    files.forEach((file) => formData.append('files', file))
    formData.append('operator_name', operatorName)

    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_BASE_URL}/upload`)

    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          onProgress(Math.round((e.loaded / e.total) * 100))
        }
      }
    }

    xhr.onload = () => {
      if (xhr.status === 200) {
        resolve(JSON.parse(xhr.responseText) as UploadResponse)
      } else {
        reject(new Error(xhr.responseText || `HTTP ${xhr.status}`))
      }
    }

    xhr.onerror = () => reject(new Error('Network error during upload'))
    xhr.send(formData)
  })
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

export const WS_URL = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8001/api/v1/ws'

import type { ResultFilters, ResultsPage, AnalysisDetailResult } from '../types'

export async function fetchResults(
  filters: ResultFilters,
  page: number,
  limit: number
): Promise<ResultsPage> {
  const params = new URLSearchParams()
  if (filters.operator) params.set('operator', filters.operator)
  if (filters.date_from) params.set('date_from', filters.date_from)
  if (filters.date_to) params.set('date_to', filters.date_to)
  if (filters.score_min != null) params.set('score_min', String(filters.score_min))
  if (filters.score_max != null) params.set('score_max', String(filters.score_max))
  if (filters.sort) params.set('sort', filters.sort)
  if (filters.order) params.set('order', filters.order)
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

export interface CallMetadata {
  callerphone?: string
  calledphone?: string
  operatorphone?: string
  calltouch_duration?: number
  order_id?: string
  lead_name?: string
}

export async function fetchCallMetadata(fileId: string): Promise<CallMetadata> {
  const response = await fetch(`${API_BASE_URL}/sftp/files/${encodeURIComponent(fileId)}/metadata`)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json() as Promise<CallMetadata>
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
): Promise<{ queued: string[] }> {
  const response = await fetch(`${API_BASE_URL}/sftp/process`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filenames, operator_name: operatorName }),
  })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json() as Promise<{ queued: string[] }>
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
