import { useState } from 'react'
import type { AnalysisResult, ResultFilters } from '../types'
import { Pagination } from './Pagination'
import { SummaryCards } from './SummaryCards'

interface ResultsTableProps {
  results: AnalysisResult[]
  total: number
  page: number
  limit: number
  filters: ResultFilters
  isLoading: boolean
  error: string | null
  useMock: boolean
  onFiltersChange: (f: ResultFilters) => void
  onPageChange: (p: number) => void
  onLimitChange: (l: number) => void
  onRowDetail?: (fileId: string) => void
}

type SortableCol = 'created_at' | 'operator_name' | 'overall' | 'standard' | 'loyalty' | 'kindness'

function scoreColor(score?: number): string {
  if (score == null) return 'text-gray-400'
  if (score >= 85) return 'text-green-600'
  if (score >= 60) return 'text-yellow-600'
  return 'text-red-600'
}

function rowBg(score?: number): string {
  if (score == null) return ''
  if (score >= 85) return 'bg-green-50 hover:bg-green-100'
  if (score >= 60) return 'bg-yellow-50 hover:bg-yellow-100'
  return 'bg-red-50 hover:bg-red-100'
}

function ScorePill({ value }: { value?: number }) {
  if (value == null) return <span className="text-gray-400 text-xs">—</span>
  return (
    <span className={`text-sm font-semibold ${scoreColor(value)}`}>
      {value}%
    </span>
  )
}

function SortIcon({ col, sort, order }: { col: SortableCol; sort?: string; order?: string }) {
  if (sort !== col) return <span className="text-gray-300 ml-0.5">↕</span>
  return <span className="text-blue-500 ml-0.5">{order === 'asc' ? '↑' : '↓'}</span>
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('ru-RU', {
    day: '2-digit', month: '2-digit', year: '2-digit',
  })
}

function formatDuration(secs: number) {
  const m = Math.floor(secs / 60)
  const s = Math.round(secs % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function ExpandedRow({ result, onDetail }: { result: AnalysisResult; onDetail?: () => void }) {
  const a = result.analysis
  return (
    <tr>
      <td colSpan={7} className="px-6 py-4 bg-gray-50 border-b border-gray-200">
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {(['standard', 'loyalty', 'kindness', 'overall'] as const).map((key) => (
              <div key={key} className="bg-white rounded-lg p-3 border border-gray-100 text-center">
                <p className="text-xs text-gray-400 capitalize mb-1">
                  {{ standard: 'Стандарты', loyalty: 'Лояльность', kindness: 'Доброжел.', overall: 'Итого' }[key]}
                </p>
                <p className={`text-2xl font-bold ${scoreColor(a?.[key])}`}>
                  {a?.[key] ?? '—'}
                  {a?.[key] != null && <span className="text-sm">%</span>}
                </p>
              </div>
            ))}
          </div>

          {a?.summary && (
            <div className="bg-white rounded-lg p-3 border border-gray-100">
              <p className="text-xs text-gray-400 mb-1">Резюме</p>
              <p className="text-sm text-gray-700">{a.summary}</p>
            </div>
          )}

          <div className="flex items-center justify-between flex-wrap gap-2">
            <div className="flex gap-4 text-xs text-gray-400">
              <span>Файл: <span className="text-gray-600">{result.original_name}</span></span>
              <span>Длительность: <span className="text-gray-600">{formatDuration(result.duration_sec)}</span></span>
              <span>ID: <span className="text-gray-600 font-mono">{result.file_id}</span></span>
            </div>
            {onDetail && (
              <button
                onClick={(e) => { e.stopPropagation(); onDetail() }}
                className="text-xs text-blue-600 hover:text-blue-800 font-medium transition-colors"
              >
                Подробный анализ →
              </button>
            )}
          </div>
        </div>
      </td>
    </tr>
  )
}

function ActiveFilterBadges({
  filters,
  onRemove,
}: {
  filters: ResultFilters
  onRemove: (f: ResultFilters) => void
}) {
  const badges: { label: string; key: keyof ResultFilters }[] = []

  if (filters.operator) badges.push({ label: `Оператор: ${filters.operator}`, key: 'operator' })
  if (filters.date_from) badges.push({ label: `С ${filters.date_from}`, key: 'date_from' })
  if (filters.date_to) badges.push({ label: `По ${filters.date_to}`, key: 'date_to' })
  if (filters.score_min != null) badges.push({ label: `Мин. оценка: ${filters.score_min}%`, key: 'score_min' })
  if (filters.score_max != null) badges.push({ label: `Макс. оценка: ${filters.score_max}%`, key: 'score_max' })

  if (badges.length === 0) return null

  return (
    <div className="flex flex-wrap gap-2 mt-3">
      {badges.map(({ label, key }) => (
        <span
          key={key}
          className="flex items-center gap-1 bg-blue-50 text-blue-700 text-xs font-medium px-2.5 py-1 rounded-full"
        >
          {label}
          <button
            onClick={() => {
              const next = { ...filters }
              delete next[key]
              onRemove(next)
            }}
            className="ml-0.5 text-blue-400 hover:text-blue-700 transition-colors leading-none"
            title="Удалить фильтр"
          >
            ✕
          </button>
        </span>
      ))}
    </div>
  )
}

export function ResultsTable({
  results,
  total,
  page,
  limit,
  filters,
  isLoading,
  error,
  useMock,
  onFiltersChange,
  onPageChange,
  onLimitChange,
  onRowDetail,
}: ResultsTableProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const toggleSort = (col: SortableCol) => {
    const isActive = filters.sort === col
    onFiltersChange({
      ...filters,
      sort: col,
      order: isActive && filters.order === 'desc' ? 'asc' : 'desc',
    })
  }

  const toggleRow = (id: string) =>
    setExpandedId((prev) => (prev === id ? null : id))

  const colClass = 'px-4 py-2 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide cursor-pointer hover:text-gray-700 select-none whitespace-nowrap'

  return (
    <>
    <SummaryCards results={results} />
    <div className="bg-white rounded-2xl shadow-sm border border-gray-200 overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 border-b border-gray-100">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div>
            <h2 className="text-lg font-semibold text-gray-800">Результаты анализа</h2>
            <p className="text-sm text-gray-400 mt-0.5">
              {total} {total === 1 ? 'запись' : 'записей'}
              {useMock && <span className="ml-2 text-yellow-500 text-xs">(demo-данные)</span>}
            </p>
          </div>
        </div>

        {/* Active filter badges */}
        <ActiveFilterBadges filters={filters} onRemove={onFiltersChange} />
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        {isLoading ? (
          <div className="flex justify-center items-center py-16">
            <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : error ? (
          <div className="py-12 text-center text-red-500 text-sm">{error}</div>
        ) : results.length === 0 ? (
          <div className="py-12 text-center text-gray-400 text-sm">Нет результатов</div>
        ) : (
          <table className="w-full">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className={colClass} onClick={() => toggleSort('created_at')}>
                  Дата <SortIcon col="created_at" sort={filters.sort} order={filters.order} />
                </th>
                <th className={colClass} onClick={() => toggleSort('operator_name')}>
                  Оператор <SortIcon col="operator_name" sort={filters.sort} order={filters.order} />
                </th>
                <th className={colClass} onClick={() => toggleSort('standard')}>
                  Стандарты <SortIcon col="standard" sort={filters.sort} order={filters.order} />
                </th>
                <th className={colClass} onClick={() => toggleSort('loyalty')}>
                  Лояльность <SortIcon col="loyalty" sort={filters.sort} order={filters.order} />
                </th>
                <th className={colClass} onClick={() => toggleSort('kindness')}>
                  Доброжел. <SortIcon col="kindness" sort={filters.sort} order={filters.order} />
                </th>
                <th className={colClass} onClick={() => toggleSort('overall')}>
                  Итого <SortIcon col="overall" sort={filters.sort} order={filters.order} />
                </th>
                <th className="px-4 py-2 w-8" />
              </tr>
            </thead>
            <tbody>
              {results.map((r) => {
                const isExpanded = expandedId === r.file_id
                return (
                  <>
                    <tr
                      key={r.file_id}
                      onClick={() => toggleRow(r.file_id)}
                      className={`border-b border-gray-100 cursor-pointer transition-colors ${rowBg(r.analysis?.overall)}`}
                    >
                      <td className="px-4 py-3 text-sm text-gray-500 whitespace-nowrap">
                        {formatDate(r.created_at)}
                      </td>
                      <td className="px-4 py-3 text-sm font-medium text-gray-800 whitespace-nowrap">
                        {r.operator_name}
                      </td>
                      <td className="px-4 py-3"><ScorePill value={r.analysis?.standard} /></td>
                      <td className="px-4 py-3"><ScorePill value={r.analysis?.loyalty} /></td>
                      <td className="px-4 py-3"><ScorePill value={r.analysis?.kindness} /></td>
                      <td className="px-4 py-3"><ScorePill value={r.analysis?.overall} /></td>
                      <td className="px-4 py-3 text-gray-400 text-xs">
                        {isExpanded ? '▲' : '▼'}
                      </td>
                    </tr>
                    {isExpanded && (
                      <ExpandedRow
                        key={`${r.file_id}-exp`}
                        result={r}
                        onDetail={onRowDetail ? () => onRowDetail(r.file_id) : undefined}
                      />
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {!isLoading && !error && total > 0 && (
        <div className="border-t border-gray-100 px-4 py-2">
          <Pagination
            page={page}
            total={total}
            limit={limit}
            onPageChange={onPageChange}
            onLimitChange={onLimitChange}
          />
        </div>
      )}
    </div>
    </>
  )
}
