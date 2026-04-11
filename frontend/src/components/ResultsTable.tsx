import { Fragment } from 'react'
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
  onDelete?: (fileId: string) => void
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

function formatDateMsk(iso: string) {
  const d = new Date(iso)
  return d.toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit',
    hour: '2-digit', minute: '2-digit',
    timeZone: 'Europe/Moscow',
  })
}

function audioType(method?: string | null): { label: string; color: string } {
  if (method === 'channel_split') return { label: 'стерео', color: 'text-green-600' }
  if (method === 'channel_split+llm') return { label: 'стерео+LLM', color: 'text-green-600' }
  if (method === 'llm_diarization') return { label: 'моно', color: 'text-orange-500' }
  if (method === 'pyannote') return { label: 'моно', color: 'text-orange-500' }
  if (method === 'fallback') return { label: 'без диаризации', color: 'text-gray-400' }
  return { label: '', color: '' }
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
  onDelete,
}: ResultsTableProps) {
  const toggleSort = (col: SortableCol) => {
    const isActive = filters.sort === col
    onFiltersChange({
      ...filters,
      sort: col,
      order: isActive && filters.order === 'desc' ? 'asc' : 'desc',
    })
  }

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
                <th className="px-3 py-2 text-left text-xs font-semibold text-gray-400 uppercase tracking-wide w-8">
                  №
                </th>
                <th className={colClass} onClick={() => toggleSort('created_at')}>
                  Звонок <SortIcon col="created_at" sort={filters.sort} order={filters.order} />
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
              {results.map((r, idx) => (
                  <Fragment key={r.file_id}>
                    <tr
                      className={`border-b border-gray-100 transition-colors ${rowBg(r.analysis?.overall)}`}
                    >
                      <td className="px-3 py-3 text-xs text-gray-400 font-mono">
                        {total - ((page - 1) * limit + idx)}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        <div className="text-sm text-gray-700 font-medium">
                          {r.call_date ?? formatDate(r.created_at)}{' '}
                          <span className="text-gray-400 font-normal">{r.call_time ?? ''}</span>
                        </div>
                        <div className="text-[10px] text-gray-400">
                          загружен {formatDateMsk(r.created_at)}
                          {r.caller_phone && <span> · {r.caller_phone}</span>}
                        </div>
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        <div className="text-sm font-medium text-gray-800">{r.operator_name}</div>
                        <div className="flex items-center gap-1.5">
                          {r.diarization_method && (
                            <span className={`text-[10px] ${audioType(r.diarization_method).color}`}>
                              {audioType(r.diarization_method).label}
                            </span>
                          )}
                          {r.analysis?.llm_model && (
                            <span className="text-[10px] text-purple-500">
                              {r.analysis.llm_model}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3"><ScorePill value={r.analysis?.standard} /></td>
                      <td className="px-4 py-3"><ScorePill value={r.analysis?.loyalty} /></td>
                      <td className="px-4 py-3"><ScorePill value={r.analysis?.kindness} /></td>
                      <td className="px-4 py-3"><ScorePill value={r.analysis?.overall} /></td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-3">
                          {onRowDetail && (
                            <button
                              onClick={() => onRowDetail(r.file_id)}
                              className="text-xs text-blue-600 hover:text-blue-800 font-medium whitespace-nowrap"
                            >
                              Подробнее →
                            </button>
                          )}
                          {r.analysis?.rejected && (
                            <span className="text-[10px] text-orange-500" title={r.analysis.rejection_reason || ''}>
                              отклонено
                            </span>
                          )}
                          {onDelete && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation()
                                if (confirm('Удалить этот звонок? Это действие необратимо.')) {
                                  onDelete(r.file_id)
                                }
                              }}
                              className="text-gray-300 hover:text-red-500 transition-colors"
                              title="Удалить звонок"
                            >
                              <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <polyline points="3 6 5 6 21 6" />
                                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                                <line x1="10" y1="11" x2="10" y2="17" />
                                <line x1="14" y1="11" x2="14" y2="17" />
                              </svg>
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                    {r.analysis?.summary && (
                      <tr className={`border-b border-gray-200 ${rowBg(r.analysis?.overall)}`}>
                        <td colSpan={8} className="px-4 pb-3 pt-0">
                          <p className="text-xs text-gray-500 leading-relaxed">{r.analysis.summary}</p>
                        </td>
                      </tr>
                    )}
                  </Fragment>
              ))}
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
