import { useCallback } from 'react'
import { useDropzone } from 'react-dropzone'

const ACCEPTED_FORMATS = {
  'audio/wav': ['.wav'],
  'audio/mpeg': ['.mp3'],
  'audio/ogg': ['.ogg'],
  'audio/flac': ['.flac'],
  'audio/mp4': ['.m4a'],
  'audio/webm': ['.webm'],
}

const MAX_FILE_SIZE = 500 * 1024 * 1024 // 500 MB

interface UploadZoneProps {
  onFilesSelected: (files: File[]) => void
  disabled?: boolean
  acceptedFormats?: string[]
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function UploadZone({ onFilesSelected, disabled }: UploadZoneProps) {
  const onDrop = useCallback(
    (acceptedFiles: File[]) => {
      if (acceptedFiles.length > 0) {
        onFilesSelected(acceptedFiles)
      }
    },
    [onFilesSelected]
  )

  const { getRootProps, getInputProps, isDragActive, isDragReject } = useDropzone({
    onDrop,
    accept: ACCEPTED_FORMATS,
    maxSize: MAX_FILE_SIZE,
    disabled,
    multiple: true,
  })

  const borderColor = isDragReject
    ? 'border-red-400 bg-red-50'
    : isDragActive
    ? 'border-blue-400 bg-blue-50'
    : 'border-gray-300 hover:border-blue-400 hover:bg-blue-50'

  return (
    <div
      {...getRootProps()}
      className={`
        border-2 border-dashed rounded-xl p-10 text-center cursor-pointer
        transition-all duration-200 select-none
        ${borderColor}
        ${disabled ? 'opacity-50 cursor-not-allowed' : ''}
      `}
    >
      <input {...getInputProps()} />

      <div className="flex flex-col items-center gap-3">
        <div className="text-5xl">
          {isDragActive ? '📂' : '🎵'}
        </div>

        {isDragReject ? (
          <p className="text-red-600 font-medium">Формат не поддерживается</p>
        ) : isDragActive ? (
          <p className="text-blue-600 font-medium text-lg">Отпустите файлы здесь...</p>
        ) : (
          <>
            <p className="text-gray-700 font-medium text-lg">
              Перетащите аудиофайлы или нажмите для выбора
            </p>
            <p className="text-gray-400 text-sm">
              WAV, MP3, OGG, FLAC, M4A, WebM — до {formatBytes(MAX_FILE_SIZE)} каждый
            </p>
          </>
        )}
      </div>
    </div>
  )
}
