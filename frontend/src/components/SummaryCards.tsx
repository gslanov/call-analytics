import { useMemo } from 'react'
import type { AnalysisResult } from '../types'
import { ScoreCard } from './ScoreCard'

interface SummaryCardsProps {
  results: AnalysisResult[]
}

interface Averages {
  standard?: number
  loyalty?: number
  kindness?: number
  overall?: number
  count: number
}

function computeAverages(results: AnalysisResult[]): Averages {
  const withAnalysis = results.filter((r) => r.analysis != null)
  if (withAnalysis.length === 0) return { count: 0 }

  const n = withAnalysis.length
  const sum = withAnalysis.reduce(
    (acc, r) => ({
      standard: acc.standard + (r.analysis!.standard ?? 0),
      loyalty: acc.loyalty + (r.analysis!.loyalty ?? 0),
      kindness: acc.kindness + (r.analysis!.kindness ?? 0),
    }),
    { standard: 0, loyalty: 0, kindness: 0 }
  )

  const standard = Math.round(sum.standard / n)
  const loyalty = Math.round(sum.loyalty / n)
  const kindness = Math.round(sum.kindness / n)
  const overall = Math.round(standard * 0.4 + loyalty * 0.3 + kindness * 0.3)

  return { standard, loyalty, kindness, overall, count: n }
}

export function SummaryCards({ results }: SummaryCardsProps) {
  const avg = useMemo(() => computeAverages(results), [results])

  return (
    <div className="bg-white rounded-2xl border border-gray-200 shadow-sm px-6 py-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide">
          Средние оценки
        </h3>
        <span className="text-xs text-gray-400">
          {avg.count > 0
            ? `По ${avg.count} ${avg.count === 1 ? 'результату' : avg.count < 5 ? 'результатам' : 'результатам'}`
            : 'Нет данных'}
        </span>
      </div>

      {avg.count === 0 ? (
        <div className="flex items-center justify-center py-6 text-gray-400 text-sm">
          Нет результатов для расчёта статистики
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <ScoreCard label="Общий средний балл" score={avg.overall} size="lg" />
          <ScoreCard label="Стандарты" score={avg.standard} />
          <ScoreCard label="Лояльность" score={avg.loyalty} />
          <ScoreCard label="Доброжелательность" score={avg.kindness} />
        </div>
      )}
    </div>
  )
}
