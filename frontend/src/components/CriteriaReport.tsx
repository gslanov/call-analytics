import { useEffect, useState } from 'react'
import type { CriteriaReportData, CriterionStats } from '../types'
import { buildCriteriaReportDownloadUrl, fetchCriteriaReport } from '../lib/api'
import { OperatorSelector } from './OperatorSelector'

function barColor(rate: number | null): string {
  if (rate == null) return 'bg-gray-300'
  if (rate >= 85) return 'bg-green-500'
  if (rate >= 60) return 'bg-yellow-500'
  return 'bg-red-500'
}

function textColor(rate: number | null): string {
  if (rate == null) return 'text-gray-400'
  if (rate >= 85) return 'text-green-600'
  if (rate >= 60) return 'text-yellow-600'
  return 'text-red-600'
}

function CriterionRow({ c }: { c: CriterionStats }) {
  const { label, applicable_count, pass_count, pass_rate } = c
  const notApplicable = applicable_count === 0

  return (
    <div className="py-3 px-4 border-b border-gray-50 last:border-b-0 hover:bg-gray-50/50 transition-colors">
      <div className="flex items-center gap-4">
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-gray-800 truncate" title={label}>{label}</div>
          <div className="text-xs text-gray-400 mt-0.5">
            {notApplicable
              ? 'Ни в одном звонке не применим'
              : `${pass_count} из ${applicable_count} звонков`}
          </div>
        </div>

        <div className="flex items-center gap-3 shrink-0 w-64">
          <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
            {pass_rate != null && (
              <div
                className={`h-full ${barColor(pass_rate)} transition-all`}
                style={{ width: `${pass_rate}%` }}
              />
            )}
          </div>
          <div className={`text-sm font-bold w-12 text-right ${textColor(pass_rate)}`}>
            {pass_rate != null ? `${pass_rate}%` : '—'}
          </div>
        </div>
      </div>
    </div>
  )
}

function GroupSection({
  title,
  subtitle,
  criteria,
}: {
  title: string
  subtitle?: string
  criteria: CriterionStats[]
}) {
  return (
    <div className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
      <div className="px-6 py-4 border-b border-gray-100">
        <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide">{title}</h3>
        {subtitle && <p className="text-xs text-gray-400 mt-0.5">{subtitle}</p>}
      </div>
      <div>
        {criteria.map((c) => (
          <CriterionRow key={c.key} c={c} />
        ))}
      </div>
    </div>
  )
}

export function CriteriaReport() {
  const [data, setData] = useState<CriteriaReportData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [operator, setOperator] = useState('')

  const buildFilters = () => ({
    date_from: dateFrom ? new Date(dateFrom).toISOString() : undefined,
    date_to: dateTo ? new Date(dateTo + 'T23:59:59').toISOString() : undefined,
    operator: operator.trim() || undefined,
  })

  const reload = async () => {
    setLoading(true)
    setError(null)
    try {
      setData(await fetchCriteriaReport(buildFilters()))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleDownload = () => {
    window.open(buildCriteriaReportDownloadUrl(buildFilters()), '_blank')
  }

  return (
    <div className="space-y-6">
      {/* Filters */}
      <div className="bg-white rounded-2xl border border-gray-200 shadow-sm px-6 py-5">
        <div className="flex items-end justify-between flex-wrap gap-4">
          <div className="flex items-end gap-4 flex-wrap">
            <div className="flex flex-col gap-1">
              <label className="text-sm font-medium text-gray-700">Дата с</label>
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
                className="text-sm border border-gray-300 rounded-lg px-3 py-2"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-sm font-medium text-gray-700">Дата по</label>
              <input
                type="date"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
                className="text-sm border border-gray-300 rounded-lg px-3 py-2"
              />
            </div>
            <div className="w-60">
              <OperatorSelector value={operator} onChange={setOperator} />
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={reload}
              className="text-sm bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700 hover:shadow-md active:scale-95 transition-all duration-150"
            >
              Применить
            </button>
            <button
              onClick={handleDownload}
              disabled={!data || data.total_calls === 0}
              title={
                !data || data.total_calls === 0
                  ? 'Нет данных для выгрузки'
                  : 'Скачать отчёт в Excel'
              }
              className="text-sm border border-gray-300 text-gray-700 px-4 py-2 rounded-lg hover:bg-gray-100 hover:border-gray-400 hover:shadow-sm active:scale-95 transition-all duration-150 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-white disabled:hover:border-gray-300 disabled:hover:shadow-none disabled:active:scale-100"
            >
              ⬇ Скачать в Excel
            </button>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-16">
          <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : error ? (
        <div className="text-center text-red-500 py-12">{error}</div>
      ) : data ? (
        <>
          <div className="bg-blue-50 border border-blue-100 rounded-xl px-5 py-3 text-sm text-blue-900">
            Проанализировано звонков: <strong>{data.total_calls}</strong>
            {data.total_calls === 0 && <span className="text-blue-700"> — измените фильтры</span>}
          </div>

          {data.total_calls > 0 && (
            <>
              <GroupSection
                title={data.groups.standard.label}
                subtitle="13 пунктов — 40% общей оценки"
                criteria={data.groups.standard.criteria}
              />
              <GroupSection
                title={data.groups.loyalty.label}
                subtitle="6 пунктов — 30% общей оценки"
                criteria={data.groups.loyalty.criteria}
              />
              <GroupSection
                title={data.groups.kindness.label}
                subtitle="3 пункта — 30% общей оценки"
                criteria={data.groups.kindness.criteria}
              />
              <GroupSection
                title={data.groups.markers.label}
                subtitle="Информационные флаги, не влияют на оценку"
                criteria={data.groups.markers.criteria}
              />
            </>
          )}
        </>
      ) : null}
    </div>
  )
}
