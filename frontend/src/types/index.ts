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
  }
  created_at: string
}

export type AppState = 'empty' | 'files_picked' | 'uploading' | 'processing' | 'results' | 'ftp_files'

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
    llm_model?: string
  }
}
