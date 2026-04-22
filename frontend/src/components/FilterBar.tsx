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

  const inputBase = "border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent"

  return (
    <div className="bg-white rounded-2xl border border-gray-200 shadow-sm px-5 py-4">
      <div className="flex flex-col sm:flex-row sm:items-end gap-x-6 gap-y-4 flex-wrap">

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
              className={`flex-1 min-w-0 ${inputBase}`}
            />
            <span className="text-gray-400 text-sm flex-shrink-0">—</span>
            <input
              type="date"
              value={draft.date_to ?? ''}
              min={draft.date_from}
              onChange={(e) => set('date_to', e.target.value || undefined)}
              className={`flex-1 min-w-0 ${inputBase}`}
            />
          </div>
        </div>

        {/* Score range — simple two inputs, no sliders */}
        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-gray-700">Оценка, %</label>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={0}
              max={100}
              value={draft.score_min ?? ''}
              placeholder="0"
              onChange={(e) => {
                const v = e.target.value === '' ? undefined : Math.max(0, Math.min(100, Number(e.target.value)))
                set('score_min', v)
              }}
              className={`w-20 text-center ${inputBase}`}
            />
            <span className="text-gray-400 text-sm">—</span>
            <input
              type="number"
              min={0}
              max={100}
              value={draft.score_max ?? ''}
              placeholder="100"
              onChange={(e) => {
                const v = e.target.value === '' ? undefined : Math.max(0, Math.min(100, Number(e.target.value)))
                set('score_max', v)
              }}
              className={`w-20 text-center ${inputBase}`}
            />
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-2 flex-shrink-0">
          <button
            onClick={handleApply}
            className={`px-4 py-2 rounded-lg text-sm font-medium active:scale-95 transition-all duration-150 ${
              hasChanges
                ? 'bg-blue-600 text-white hover:bg-blue-700 hover:shadow-md'
                : 'bg-blue-100 text-blue-600 hover:bg-blue-200'
            }`}
          >
            🔍 Применить
          </button>
          <button
            onClick={handleReset}
            className="px-4 py-2 rounded-lg text-sm font-medium text-gray-500 hover:bg-gray-100 hover:text-gray-700 active:scale-95 transition-all duration-150"
          >
            ⟲ Сброс
          </button>
        </div>
      </div>
    </div>
  )
}
