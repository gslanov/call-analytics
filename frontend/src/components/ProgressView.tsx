import { useWebSocket } from '../hooks/useWebSocket'
import type { ProcessingFile, FileProgress, ProcessingStatus } from '../types'

interface ProgressViewProps {
  files: ProcessingFile[]
  onAddMore: () => void
}

const STATUS_LABEL: Record<ProcessingStatus, string> = {
  queued: 'В очереди',
  transcribing: 'Транскрибация',
  diarizing: 'Диаризация',
  analyzing: 'Анализ',
  done: 'Готово',
  failed: 'Ошибка',
}

const STAGES = ['Очередь', 'Валидация', 'Транскрипт', 'Диаризация', 'Анализ']

function ProgressBar({ value, status }: { value: number; status: ProcessingStatus }) {
  const color =
    status === 'done'
      ? 'bg-green-500'
      : status === 'failed'
      ? 'bg-red-500'
      : 'bg-blue-500'

  return (
    <div className="w-full bg-gray-200 rounded-full h-2 overflow-hidden">
      <div
        className={`h-2 rounded-full transition-all duration-500 ${color}`}
        style={{ width: `${value}%` }}
      />
    </div>
  )
}

function StageSteps({ stage, status }: { stage: number; status: ProcessingStatus }) {
  return (
    <div className="flex gap-0.5">
      {STAGES.map((_, i) => {
        const filled = i < stage || status === 'done'
        const active = i === stage && status !== 'done' && status !== 'failed'
        return (
          <div
            key={i}
            className={`h-1.5 flex-1 rounded-full transition-colors duration-300 ${
              filled
                ? 'bg-green-400'
                : active
                ? 'bg-blue-400 animate-pulse'
                : 'bg-gray-200'
            }`}
          />
        )
      })}
    </div>
  )
}

function FileRow({ fp }: { fp: FileProgress }) {
  const isDone = fp.status === 'done'
  const isFailed = fp.status === 'failed'

  const rowBg = isDone
    ? 'bg-green-50'
    : isFailed
    ? 'bg-red-50'
    : 'bg-white'

  const statusColor = isDone
    ? 'text-green-600'
    : isFailed
    ? 'text-red-600'
    : 'text-blue-600'

  return (
    <tr className={`border-b border-gray-100 last:border-0 transition-colors ${rowBg}`}>
      {/* File name */}
      <td className="px-4 py-3 max-w-xs">
        <p className="text-sm font-medium text-gray-800 truncate" title={fp.file_name}>
          {fp.file_name}
        </p>
      </td>

      {/* Stage steps */}
      <td className="px-4 py-3 w-36">
        <StageSteps stage={fp.stage} status={fp.status} />
        <p className="text-xs text-gray-400 mt-1">{fp.stage_name || STATUS_LABEL[fp.status]}</p>
      </td>

      {/* Progress bar */}
      <td className="px-4 py-3 w-32">
        <ProgressBar value={fp.progress} status={fp.status} />
        <p className="text-xs text-gray-400 mt-1 text-right">{fp.progress}%</p>
      </td>

      {/* Status badge */}
      <td className="px-4 py-3 w-28 text-right">
        <span className={`text-xs font-medium ${statusColor}`}>
          {isDone ? '✓ ' : isFailed ? '✕ ' : ''}
          {STATUS_LABEL[fp.status]}
        </span>
        {fp.error && (
          <p className="text-xs text-red-400 mt-0.5 truncate max-w-[6rem]" title={fp.error}>
            {fp.error}
          </p>
        )}
      </td>
    </tr>
  )
}

export function ProgressView({ files, onAddMore }: ProgressViewProps) {
  const { progress, wsConnected, usingPolling } = useWebSocket(files)

  const rows = Object.values(progress)
  const doneCount = rows.filter((r) => r.status === 'done').length
  const failedCount = rows.filter((r) => r.status === 'failed').length
  const allDone = doneCount + failedCount === rows.length && rows.length > 0

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-gray-200 overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-800">Обработка файлов</h2>
          <p className="text-sm text-gray-400 mt-0.5">
            {doneCount} / {rows.length} завершено
            {failedCount > 0 && <span className="text-red-400"> · {failedCount} с ошибкой</span>}
          </p>
        </div>

        {/* Connection indicator */}
        <div className="flex items-center gap-2 text-xs text-gray-400">
          <span
            className={`w-2 h-2 rounded-full ${
              wsConnected ? 'bg-green-400' : usingPolling ? 'bg-yellow-400' : 'bg-gray-300'
            }`}
          />
          {wsConnected ? 'Live' : usingPolling ? 'Polling' : 'Connecting…'}
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="bg-gray-50 text-xs text-gray-500 uppercase tracking-wide">
              <th className="px-4 py-2 text-left">Файл</th>
              <th className="px-4 py-2 text-left">Этап</th>
              <th className="px-4 py-2 text-left">Прогресс</th>
              <th className="px-4 py-2 text-right">Статус</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((fp) => (
              <FileRow key={fp.file_id} fp={fp} />
            ))}
          </tbody>
        </table>
      </div>

      {/* Footer */}
      {allDone && (
        <div className="px-6 py-4 border-t border-gray-100 flex justify-between items-center bg-gray-50">
          <p className="text-sm text-gray-500">Обработка завершена</p>
          <button
            onClick={onAddMore}
            className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg
                       hover:bg-blue-700 transition-colors font-medium"
          >
            + Загрузить новые файлы
          </button>
        </div>
      )}

      {!allDone && (
        <div className="px-6 py-3 border-t border-gray-100 text-center">
          <button
            onClick={onAddMore}
            className="text-sm text-gray-400 hover:text-gray-600 transition-colors"
          >
            + Загрузить новые файлы
          </button>
        </div>
      )}
    </div>
  )
}
