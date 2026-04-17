export interface UploadedFile {
  file: File
  id: string
  status: 'pending' | 'uploading' | 'done' | 'error'
  progress: number
}

export interface UploadResponse {
  file_ids: string[]
  operator: string
  status: string
  total_files: number
}

export interface AnalysisResult {
  file_id: string
  original_name: string
  operator_name: string
  duration_sec: number
  status: string
  analysis?: {
    standard: number
    loyalty: number
    kindness: number
    overall: number
    summary: string
    llm_model?: string
    rejected?: boolean
    rejection_reason?: string | null
  }
  created_at: string
  diarization_method?: string | null
  call_date?: string | null    // "04.04"
  call_time?: string | null    // "19:51"
  caller_phone?: string | null // "**3351"
  // Markers (informational, not scored)
  order_confirmation?: boolean | null  // звонок для подтверждения заказа
  prepayment_20k?: boolean | null      // заказ ≥20k → озвучена предоплата
}

export type AppState = 'empty' | 'files_picked' | 'uploading' | 'processing' | 'results' | 'reports' | 'ftp_files' | 'settings'

export type ProcessingStatus =
  | 'queued'
  | 'transcribing'
  | 'diarizing'
  | 'analyzing'
  | 'done'
  | 'failed'

export interface FileProgress {
  file_id: string
  file_name: string
  status: ProcessingStatus
  stage: number      // 0-4
  stage_name: string
  progress: number   // 0-100
  error?: string
}

export interface ProcessingFile {
  file_id: string
  file_name: string
}

export interface ResultFilters {
  operator?: string
  date_from?: string
  date_to?: string
  score_min?: number
  score_max?: number
  sort?: 'created_at' | 'operator_name' | 'overall' | 'standard' | 'loyalty' | 'kindness'
  order?: 'asc' | 'desc'
}

export interface ResultsPage {
  items: AnalysisResult[]
  total: number
  page: number
  limit: number
  pages: number
}

export interface TranscriptSegment {
  speaker: 'operator' | 'client'
  start: number
  end: number
  text: string
}

export interface Quote {
  text: string
  criterion: 'standard' | 'loyalty' | 'kindness'
  timestamp?: number
}

export interface CriteriaGroup {
  [key: string]: boolean | null
}

export interface CriteriaReasons {
  standard?: Record<string, string>
  loyalty?: Record<string, string>
  kindness?: Record<string, string>
  markers?: Record<string, string>
  standard_timestamps?: Record<string, number>
  loyalty_timestamps?: Record<string, number>
  kindness_timestamps?: Record<string, number>
  markers_timestamps?: Record<string, number>
}

export interface CriteriaDetails {
  standard: CriteriaGroup
  loyalty: CriteriaGroup
  kindness: CriteriaGroup
  markers?: CriteriaGroup
  reasons?: CriteriaReasons
}

export interface AnalysisDetailResult extends AnalysisResult {
  audio_url?: string
  transcription?: {
    full_text: string
    word_timestamps?: Array<{ word: string; start: number; end: number }>
  }
  diarization?: {
    method: string
    confidence: number
    num_speakers: number
    segments: TranscriptSegment[]
  }
  analysis?: {
    standard: number
    loyalty: number
    kindness: number
    overall: number
    summary: string
    quotes?: Quote[]
    criteria_details?: CriteriaDetails
    llm_model?: string
    rejected?: boolean
    rejection_reason?: string | null
  }
}
