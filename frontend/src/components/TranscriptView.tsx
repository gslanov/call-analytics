import type { TranscriptSegment, Quote } from '../types'

interface TranscriptViewProps {
  segments: TranscriptSegment[]
  quotes?: Quote[]
  onTimestampClick?: (time: number) => void
}

function fmt(secs: number): string {
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

/** Check if segment text contains (or is contained in) any quote text */
function findQuoteMatch(text: string, quotes: Quote[]): Quote | undefined {
  const t = text.toLowerCase()
  return quotes.find(
    (q) => t.includes(q.text.toLowerCase()) || q.text.toLowerCase().includes(t)
  )
}

const CRITERION_LABEL: Record<string, string> = {
  standard: 'Стандарты',
  loyalty: 'Лояльность',
  kindness: 'Доброжел.',
}

export function TranscriptView({ segments, quotes = [], onTimestampClick }: TranscriptViewProps) {
  if (segments.length === 0) {
    return (
      <div className="text-gray-400 text-sm text-center py-6">
        Транскрипт не доступен
      </div>
    )
  }

  // Определяем количество уникальных спикеров
  const speakers = new Set(segments.map((s) => s.speaker))
  const isMono = speakers.size <= 1

  if (isMono) {
    // Моно: компактный текст без разбивки по ролям
    return (
      <div className="max-h-[500px] overflow-y-auto pr-1">
        <div className="bg-gray-50 rounded-lg px-4 py-3">
          {segments.map((seg, i) => {
            const matchedQuote = findQuoteMatch(seg.text, quotes)
            return (
              <span
                key={i}
                className={`cursor-pointer hover:bg-blue-100 rounded transition-colors ${
                  matchedQuote ? 'bg-yellow-100 border-b-2 border-yellow-400' : ''
                }`}
                onClick={() => onTimestampClick?.(seg.start)}
                title={`${fmt(seg.start)} — ${fmt(seg.end)}${
                  matchedQuote ? ` | ${CRITERION_LABEL[matchedQuote.criterion] ?? matchedQuote.criterion}` : ''
                }`}
              >
                {seg.text}{' '}
              </span>
            )
          })}
        </div>
      </div>
    )
  }

  // Стерео/диаризация: компактный диалог
  return (
    <div className="flex flex-col gap-1 max-h-[500px] overflow-y-auto pr-1">
      {segments.map((seg, i) => {
        const isOperator = seg.speaker === 'operator'
        const matchedQuote = findQuoteMatch(seg.text, quotes)

        return (
          <div
            key={i}
            className={`flex items-start gap-2 ${isOperator ? '' : 'flex-row-reverse'}`}
          >
            {/* Speaker + time */}
            <div className={`flex-shrink-0 w-12 text-center ${isOperator ? '' : 'text-right'}`}>
              <span className={`text-[10px] font-bold ${
                isOperator ? 'text-blue-600' : 'text-gray-400'
              }`}>
                {isOperator ? 'Оп' : 'Кл'}
              </span>
              <div className="text-[10px] text-gray-300">{fmt(seg.start)}</div>
            </div>

            {/* Text */}
            <div
              className={`flex-1 rounded-lg px-2.5 py-1 cursor-pointer transition-colors text-sm leading-snug ${
                matchedQuote
                  ? 'bg-yellow-50 border-l-2 border-yellow-400'
                  : isOperator
                  ? 'bg-blue-50 hover:bg-blue-100'
                  : 'bg-gray-50 hover:bg-gray-100'
              } ${isOperator ? 'font-medium text-gray-800' : 'text-gray-600'}`}
              onClick={() => onTimestampClick?.(seg.start)}
              title={matchedQuote ? `★ ${CRITERION_LABEL[matchedQuote.criterion] ?? matchedQuote.criterion}` : undefined}
            >
              {seg.text}
              {matchedQuote && (
                <span className="ml-1 text-[10px] text-yellow-600 font-medium">
                  ★ {CRITERION_LABEL[matchedQuote.criterion] ?? matchedQuote.criterion}
                </span>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
