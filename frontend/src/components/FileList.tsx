import type { UploadedFile } from '../types'
import { OperatorSelector } from './OperatorSelector'

interface FileListProps {
  files: UploadedFile[]
  operatorName: string
  onRemove: (id: string) => void
  onOperatorChange: (name: string) => void
  onAnalyze: () => void
  isUploading: boolean
  uploadProgress: number
  error: string | null
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function StatusBadge({ status }: { status: UploadedFile['status'] }) {
  const map = {
    pending: { label: 'Ожидает', cls: 'bg-gray-100 text-gray-600' },
    uploading: { label: 'Загружается', cls: 'bg-blue-100 text-blue-700' },
    done: { label: 'Загружен', cls: 'bg-green-100 text-green-700' },
    error: { label: 'Ошибка', cls: 'bg-red-100 text-red-700' },
  }
  const { label, cls } = map[status]
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>{label}</span>
  )
}

export function FileList({
  files,
  operatorName,
  onRemove,
  onOperatorChange,
  onAnalyze,
  isUploading,
  uploadProgress,
  error,
}: FileListProps) {
  const totalSize = files.reduce((sum, f) => sum + f.file.size, 0)
  const canAnalyze = files.length > 0 && !isUploading && operatorName.trim().length > 0

  return (
    <div className="flex flex-col gap-4">
      {/* Operator selector */}
      <OperatorSelector
        value={operatorName}
        onChange={onOperatorChange}
        disabled={isUploading}
      />

      {/* File table */}
      <div className="border border-gray-200 rounded-lg overflow-hidden">
        <div className="bg-gray-50 px-4 py-2 flex justify-between items-center text-sm text-gray-500">
          <span>{files.length} файл(ов)</span>
          <span>{formatBytes(totalSize)} всего</span>
        </div>

        <div className="divide-y divide-gray-100 max-h-64 overflow-y-auto">
          {files.map((f) => (
            <div key={f.id} className="flex items-center px-4 py-3 gap-3 hover:bg-gray-50">
              <span className="text-lg">🎵</span>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-800 truncate">{f.file.name}</p>
                <p className="text-xs text-gray-400">{formatBytes(f.file.size)}</p>
              </div>
              <StatusBadge status={f.status} />
              <button
                onClick={() => onRemove(f.id)}
                disabled={isUploading}
                className="text-gray-400 hover:text-red-500 transition-colors
                           disabled:opacity-30 disabled:cursor-not-allowed p-1 rounded"
                title="Удалить"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Upload progress bar */}
      {isUploading && (
        <div className="flex flex-col gap-1">
          <div className="flex justify-between text-xs text-gray-500">
            <span>Загрузка...</span>
            <span>{uploadProgress}%</span>
          </div>
          <div className="w-full bg-gray-200 rounded-full h-2">
            <div
              className="bg-blue-500 h-2 rounded-full transition-all duration-300"
              style={{ width: `${uploadProgress}%` }}
            />
          </div>
        </div>
      )}

      {/* Error message */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Analyze button */}
      <button
        onClick={onAnalyze}
        disabled={!canAnalyze}
        className={`
          w-full py-3 rounded-xl font-semibold text-white transition-all duration-200
          ${canAnalyze
            ? 'bg-blue-600 hover:bg-blue-700 active:scale-95 cursor-pointer shadow-sm'
            : 'bg-gray-300 cursor-not-allowed'
          }
        `}
      >
        {isUploading ? 'Загружается...' : `Анализировать (${files.length})`}
      </button>
    </div>
  )
}
