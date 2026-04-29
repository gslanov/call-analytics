import { useState, useCallback, useRef } from 'react'
import { ApiError, uploadFiles } from '../lib/api'
import type { UploadResponse } from '../lib/api'
import type { UploadedFile } from '../types'

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

    try {
      const result = await uploadFiles(
        files.map((f) => f.file),
        operatorName.trim(),
        setUploadProgress,
        abortRef.current.signal,
      )

      // Карта per-file ошибок (если бэк прислал validation_errors на 200)
      const errorByName = new Map<string, string>()
      for (const ve of result.validation_errors ?? []) {
        errorByName.set(ve.file, ve.error)
      }
      // Карта дубликатов из accepted[] — бэк помечает is_duplicate=true для уже существующих хешей
      const duplicateNames = new Set<string>()
      for (const a of result.accepted ?? []) {
        if (a.is_duplicate) duplicateNames.add(a.original_name)
      }

      setFiles((prev) =>
        prev.map((f) => {
          const errMsg = errorByName.get(f.file.name)
          if (errMsg) {
            return { ...f, status: 'error', error: errMsg, progress: 0 }
          }
          if (duplicateNames.has(f.file.name)) {
            return { ...f, status: 'duplicate', progress: 100 }
          }
          return { ...f, status: 'done', progress: 100 }
        })
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
      setFiles((prev) =>
        prev.map((f) => ({
          ...f,
          status: 'error',
          error: errorByName.get(f.file.name) ?? mainMessage,
          progress: 0,
        }))
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
