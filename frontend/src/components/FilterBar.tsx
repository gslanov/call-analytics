import { useState, useEffect } from 'react'
import type { ResultFilters } from '../types'
import { OperatorSelector } from './OperatorSelector'

interface FilterBarProps {
  activeFilters: ResultFilters
  onApply: (filters: ResultFilters) => void
  onReset: () => void
}

const EMPTY: ResultFilters = {}

export function FilterBar({ activeFilters, onApply, onReset }: FilterBarProps) {
  // Local draft — only committed on "Apply"
  const [draft, setDraft] = useState<ResultFilters>(activeFilters)

  // Keep draft in sync if external reset clears filters
  useEffect(() => {
    setDraft(activeFilters)
  }, [activeFilters])

  const set = <K extends keyof ResultFilters>(key: K, value: ResultFilters[K]) =>
    setDraft((prev) => ({ ...prev, [key]: value || undefined }))

  const handleApply = () => onApply(draft)

  const handleReset = () => {
    setDraft(EMPTY)
    onReset()
  }

  const hasChanges = JSON.stringify(draft) !== JSON.stringify(activeFilters)

  return (
    <div className="bg-white rounded-2xl border border-gray-200 shadow-sm px-5 py-4">
      <div className="flex flex-col sm:flex-row gap-4 flex-wrap">

        {/* Operator */}
        <div className="flex-1 min-w-48">
          <OperatorSelector
            value={draft.operator ?? ''}
            onChange={(v) => set('operator', v || undefined)}
          />
        </div>

        {/* Date range */}
        <div className="flex-1 min-w-64 flex flex-col gap-1">
          <label className="text-sm font-medium text-gray-700">Дата</label>
          <div className="flex items-center gap-2">
            <input
              type="date"
              value={draft.date_from ?? ''}
              onChange={(e) => set('date_from', e.target.value || undefined)}
              className="flex-1 border border-gray-300 rounded-lg px-2 py-2 text-sm
                         focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent"
            />
            <span className="text-gray-400 text-sm flex-shrink-0">—</span>
            <input
              type="date"
              value={draft.date_to ?? ''}
              min={draft.date_from}
              onChange={(e) => set('date_to', e.target.value || undefined)}
              className="flex-1 border border-gray-300 rounded-lg px-2 py-2 text-sm
                         focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent"
            />
          </div>
        </div>

        {/* Score range */}
        <div className="flex-1 min-w-48 flex flex-col gap-1">
          <label className="text-sm font-medium text-gray-700">
            Оценка: {draft.score_min ?? 0}% — {draft.score_max ?? 100}%
          </label>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={0}
              max={draft.score_max ?? 100}
              value={draft.score_min ?? ''}
              placeholder="0"
              onChange={(e) => {
                const v = e.target.value === '' ? undefined : Math.max(0, Math.min(100, Number(e.target.value)))
                set('score_min', v)
              }}
              className="w-20 border border-gray-300 rounded-lg px-2 py-2 text-sm text-center
                         focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent"
            />
            <div className="flex-1 flex flex-col gap-1">
              <input
                type="range"
                min={0}
                max={100}
                value={draft.score_min ?? 0}
                onChange={(e) => set('score_min', Number(e.target.value) || undefined)}
                className="w-full accent-blue-500"
              />
              <input
                type="range"
                min={0}
                max={100}
                value={draft.score_max ?? 100}
                onChange={(e) => {
                  const v = Number(e.target.value)
                  set('score_max', v < 100 ? v : undefined)
                }}
                className="w-full accent-blue-500"
              />
            </div>
            <input
              type="number"
              min={draft.score_min ?? 0}
              max={100}
              value={draft.score_max ?? ''}
              placeholder="100"
              onChange={(e) => {
                const v = e.target.value === '' ? undefined : Math.max(0, Math.min(100, Number(e.target.value)))
                set('score_max', v)
              }}
              className="w-20 border border-gray-300 rounded-lg px-2 py-2 text-sm text-center
                         focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent"
            />
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex items-end gap-2 flex-shrink-0">
          <button
            onClick={handleApply}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              hasChanges
                ? 'bg-blue-600 text-white hover:bg-blue-700'
                : 'bg-blue-100 text-blue-600 hover:bg-blue-200'
            }`}
          >
            🔍 Применить
          </button>
          <button
            onClick={handleReset}
            className="px-4 py-2 rounded-lg text-sm font-medium text-gray-500 hover:bg-gray-100 transition-colors"
          >
            ⟲ Сброс
          </button>
        </div>
      </div>
    </div>
  )
}
