import { useState, useCallback, useRef } from 'react'
import { ApiError, uploadFilesChunked } from '../lib/api'
import type { UploadResponse } from '../lib/api'
import type { UploadedFile } from '../types'

// Размер пачки: 20 файлов. Достаточно крупно, чтобы накладные расходы
// HTTP-handshake были незаметны, и достаточно мало, чтобы один POST
// успевал пройти даже на нестабильном канале без таймаута.
const CHUNK_SIZE = 20

interface UseUploadReturn {
  files: UploadedFile[]
  operatorName: string
  uploadProgress: number
  isUploading: boolean
  error: string | null
  addFiles: (newFiles: File[]) => void
  removeFile: (id: string) => void
  setOperatorName: (name: string) => void
  startUpload: () => Promise<UploadResponse | null>
  cancelUpload: () => void
  reset: () => void
}

export function useUpload(): UseUploadReturn {
  const [files, setFiles] = useState<UploadedFile[]>([])
  const [operatorName, setOperatorName] = useState('')
  const [uploadProgress, setUploadProgress] = useState(0)
  const [isUploading, setIsUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const addFiles = useCallback((newFiles: File[]) => {
    const uploaded: UploadedFile[] = newFiles.map((file) => ({
      file,
      id: `${file.name}-${file.size}-${Date.now()}-${Math.random()}`,
      status: 'pending',
      progress: 0,
    }))
    setFiles((prev) => [...prev, ...uploaded])
    setError(null)
  }, [])

  const removeFile = useCallback((id: string) => {
    setFiles((prev) => prev.filter((f) => f.id !== id))
  }, [])

  const cancelUpload = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const startUpload = useCallback(async (): Promise<UploadResponse | null> => {
    if (files.length === 0) return null
    if (!operatorName.trim()) {
      setError('Введи имя оператора')
      return null
    }

    setIsUploading(true)
    setError(null)
    setUploadProgress(0)

    abortRef.current = new AbortController()
    setFiles((prev) => prev.map((f) => ({ ...f, status: 'uploading', error: undefined })))

    const totalFiles = files.length

    try {
      const result = await uploadFilesChunked(
        files.map((f) => f.file),
        operatorName.trim(),
        {
          chunkSize: CHUNK_SIZE,
          maxRetries: 2,
          signal: abortRef.current.signal,
          onOverallProgress: setUploadProgress,
          // Обновляем статусы файлов сразу по приходу ответа на каждый чанк,
          // чтобы РОП видела реальное движение и могла начать слушать
          // уже загруженные звонки до окончания всей партии.
          onChunkComplete: (chunkIdx, _total, chunkResult) => {
            const start = chunkIdx * CHUNK_SIZE
            const end = Math.min(start + CHUNK_SIZE, totalFiles)

            const errorByName = new Map<string, string>()
            for (const ve of chunkResult.validation_errors ?? []) {
              errorByName.set(ve.file, ve.error)
            }
            const duplicateNames = new Set<string>()
            for (const a of chunkResult.accepted ?? []) {
              if (a.is_duplicate) duplicateNames.add(a.original_name)
            }

            setFiles((prev) =>
              prev.map((f, idx) => {
                if (idx < start || idx >= end) return f
                const errMsg = errorByName.get(f.file.name)
                if (errMsg) return { ...f, status: 'error', error: errMsg, progress: 0 }
                if (duplicateNames.has(f.file.name)) return { ...f, status: 'duplicate', progress: 100 }
                return { ...f, status: 'done', progress: 100 }
              }),
            )
          },
        },
      )

      // Финальный проход — закрываем хвост: если для какого-то файла бэк не
      // прислал ни accepted, ни validation_errors (на практике не бывает,
      // но подстрахуемся), помечаем как done. Уже выставленные статусы
      // (done/duplicate/error через onChunkComplete) не трогаем.
      setFiles((prev) =>
        prev.map((f) => (f.status === 'uploading' ? { ...f, status: 'done', progress: 100 } : f)),
      )

      const acceptedCount = result.file_ids.length
      const failedCount = result.validation_errors?.length ?? 0
      if (acceptedCount === 0) {
        setError(failedCount > 0 ? `Не загружено файлов: ${failedCount}` : 'Не загружено ни одного файла')
      }
      return result
    } catch (err) {
      // Парсим ApiError на main + per-file ошибки
      let mainMessage = err instanceof Error ? err.message : 'Ошибка загрузки'
      let perFile: Array<{ file: string; error: string }> | undefined
      if (err instanceof ApiError) {
        mainMessage = err.parsed.message
        perFile = err.parsed.perFile
      }

      const errorByName = new Map<string, string>()
      for (const ve of perFile ?? []) {
        errorByName.set(ve.file, ve.error)
      }

      setError(mainMessage)
      // Важно: уже успешно загруженные чанки (status === 'done' / 'duplicate')
      // оставляем как есть. Помечаем как error только то, что ещё в полёте.
      setFiles((prev) =>
        prev.map((f) => {
          if (f.status === 'done' || f.status === 'duplicate' || f.status === 'error') return f
          return {
            ...f,
            status: 'error',
            error: errorByName.get(f.file.name) ?? mainMessage,
            progress: 0,
          }
        }),
      )
      return null
    } finally {
      setIsUploading(false)
      abortRef.current = null
    }
  }, [files, operatorName])

  const reset = useCallback(() => {
    setFiles([])
    setOperatorName('')
    setUploadProgress(0)
    setIsUploading(false)
    setError(null)
    abortRef.current = null
  }, [])

  return {
    files,
    operatorName,
    uploadProgress,
    isUploading,
    error,
    addFiles,
    removeFile,
    setOperatorName,
    startUpload,
    cancelUpload,
    reset,
  }
}
