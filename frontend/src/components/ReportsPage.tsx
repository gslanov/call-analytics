import { useEffect, useState } from 'react'
import { ScoreCard } from './ScoreCard'

const API = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'

interface OperatorReport {
  name: string
  call_count: number
  avg_standard: number
  avg_loyalty: number
  avg_kindness: number
  avg_overall: number
  min_overall: number
  max_overall: number
}

interface ReportData {
  operators: OperatorReport[]
  overall: {
    call_count: number
    avg_standard: number
    avg_loyalty: number
    avg_kindness: number
    avg_overall: number
    min_overall: number
    max_overall: number
  }
  rejected_count: number
}

function scoreColor(score: number): string {
  if (score >= 85) return 'text-green-600'
  if (score >= 60) return 'text-yellow-600'
  return 'text-red-600'
}

function scoreBg(score: number): string {
  if (score >= 85) return 'bg-green-50'
  if (score >= 60) return 'bg-yellow-50'
  return 'bg-red-50'
}

export function ReportsPage() {
  const [data, setData] = useState<ReportData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')

  const fetchReport = async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams()
      if (dateFrom) params.set('date_from', new Date(dateFrom).toISOString())
      if (dateTo) params.set('date_to', new Date(dateTo + 'T23:59:59').toISOString())
      const url = `${API}/reports${params.toString() ? '?' + params : ''}`
      const res = await fetch(url)
      if (!res.ok) throw new Error(res.statusText)
      setData(await res.json())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchReport() }, [])

  return (
    <div className="space-y-6">
      {/* Header + date filter */}
      <div className="bg-white rounded-2xl border border-gray-200 shadow-sm px-6 py-5">
        <div className="flex items-center justify-between flex-wrap gap-4">
          <h2 className="text-lg font-semibold text-gray-800">Отчёты по отделу продаж</h2>
          <div className="flex items-center gap-3">
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              className="text-sm border border-gray-300 rounded-lg px-3 py-1.5"
            />
            <span className="text-gray-400 text-sm">—</span>
            <input
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              className="text-sm border border-gray-300 rounded-lg px-3 py-1.5"
            />
            <button
              onClick={fetchReport}
              className="text-sm bg-blue-600 text-white px-4 py-1.5 rounded-lg hover:bg-blue-700 transition-colors"
            >
              Обновить
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
          {/* Overall summary */}
          <div className="bg-white rounded-2xl border border-gray-200 shadow-sm px-6 py-5">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide">
                Общий отчёт по отделу
              </h3>
              <div className="flex items-center gap-3">
                <span className="text-xs text-gray-400">
                  {data.overall.call_count} звонков проанализировано
                </span>
                {data.rejected_count > 0 && (
                  <span className="text-xs text-orange-500">
                    {data.rejected_count} отклонено
                  </span>
                )}
              </div>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <ScoreCard label="Общий средний" score={data.overall.avg_overall} size="lg" />
              <ScoreCard label="Стандарты" score={data.overall.avg_standard} />
              <ScoreCard label="Лояльность" score={data.overall.avg_loyalty} />
              <ScoreCard label="Доброжелательность" score={data.overall.avg_kindness} />
            </div>
          </div>

          {/* Per-operator table */}
          <div className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-100">
              <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide">
                По операторам
              </h3>
            </div>
            {data.operators.length === 0 ? (
              <div className="py-12 text-center text-gray-400 text-sm">Нет данных</div>
            ) : (
              <table className="w-full">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase">Оператор</th>
                    <th className="px-4 py-3 text-center text-xs font-semibold text-gray-500 uppercase">Звонков</th>
                    <th className="px-4 py-3 text-center text-xs font-semibold text-gray-500 uppercase">Стандарты</th>
                    <th className="px-4 py-3 text-center text-xs font-semibold text-gray-500 uppercase">Лояльность</th>
                    <th className="px-4 py-3 text-center text-xs font-semibold text-gray-500 uppercase">Доброжел.</th>
                    <th className="px-4 py-3 text-center text-xs font-semibold text-gray-500 uppercase">Средний</th>
                    <th className="px-4 py-3 text-center text-xs font-semibold text-gray-500 uppercase">Мин — Макс</th>
                  </tr>
                </thead>
                <tbody>
                  {data.operators.map((op) => (
                    <tr key={op.name} className={`border-b border-gray-100 ${scoreBg(op.avg_overall)}`}>
                      <td className="px-6 py-3 font-medium text-gray-800">{op.name}</td>
                      <td className="px-4 py-3 text-center text-sm text-gray-600">{op.call_count}</td>
                      <td className={`px-4 py-3 text-center text-sm font-semibold ${scoreColor(op.avg_standard)}`}>{op.avg_standard}%</td>
                      <td className={`px-4 py-3 text-center text-sm font-semibold ${scoreColor(op.avg_loyalty)}`}>{op.avg_loyalty}%</td>
                      <td className={`px-4 py-3 text-center text-sm font-semibold ${scoreColor(op.avg_kindness)}`}>{op.avg_kindness}%</td>
                      <td className={`px-4 py-3 text-center text-sm font-bold ${scoreColor(op.avg_overall)}`}>{op.avg_overall}%</td>
                      <td className="px-4 py-3 text-center text-xs text-gray-500">{op.min_overall}% — {op.max_overall}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      ) : null}
    </div>
  )
}
