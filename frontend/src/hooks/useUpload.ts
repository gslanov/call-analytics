import { useState, useCallback } from 'react'
import { uploadFiles } from '../lib/api'
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
  reset: () => void
}

export function useUpload(): UseUploadReturn {
  const [files, setFiles] = useState<UploadedFile[]>([])
  const [operatorName, setOperatorName] = useState('')
  const [uploadProgress, setUploadProgress] = useState(0)
  const [isUploading, setIsUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)

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

  const startUpload = useCallback(async (): Promise<UploadResponse | null> => {
    if (files.length === 0) return null
    if (!operatorName.trim()) {
      setError('Введите имя оператора')
      return null
    }

    setIsUploading(true)
    setError(null)
    setUploadProgress(0)

    setFiles((prev) => prev.map((f) => ({ ...f, status: 'uploading' })))

    try {
      const result = await uploadFiles(
        files.map((f) => f.file),
        operatorName.trim(),
        setUploadProgress
      )
      setFiles((prev) => prev.map((f) => ({ ...f, status: 'done', progress: 100 })))
      return result
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Ошибка загрузки'
      setError(message)
      setFiles((prev) => prev.map((f) => ({ ...f, status: 'error' })))
      return null
    } finally {
      setIsUploading(false)
    }
  }, [files, operatorName])

  const reset = useCallback(() => {
    setFiles([])
    setOperatorName('')
    setUploadProgress(0)
    setIsUploading(false)
    setError(null)
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
    reset,
  }
}
