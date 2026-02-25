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

  return (
    <div className="flex flex-col gap-2 max-h-96 overflow-y-auto pr-1">
      {segments.map((seg, i) => {
        const isOperator = seg.speaker === 'operator'
        const matchedQuote = findQuoteMatch(seg.text, quotes)

        return (
          <div
            key={i}
            className={`flex gap-3 ${isOperator ? 'flex-row' : 'flex-row-reverse'}`}
          >
            {/* Speaker label */}
            <div className="flex-shrink-0 pt-1">
              <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${
                isOperator
                  ? 'bg-blue-100 text-blue-700'
                  : 'bg-gray-100 text-gray-500'
              }`}>
                {isOperator ? 'Оп' : 'Кл'}
              </span>
            </div>

            {/* Bubble */}
            <div
              className={`max-w-[80%] rounded-xl px-3 py-2 cursor-pointer transition-all ${
                matchedQuote
                  ? 'ring-2 ring-yellow-400 bg-yellow-50'
                  : isOperator
                  ? 'bg-blue-50 hover:bg-blue-100'
                  : 'bg-gray-100 hover:bg-gray-200'
              }`}
              onClick={() => onTimestampClick?.(seg.start)}
              title={`${fmt(seg.start)} — ${fmt(seg.end)}`}
            >
              <p className={`text-sm ${isOperator ? 'font-medium text-gray-800' : 'text-gray-600'}`}>
                {seg.text}
              </p>

              <div className={`flex items-center gap-2 mt-0.5 ${isOperator ? '' : 'justify-end'}`}>
                <span className="text-xs text-gray-400">{fmt(seg.start)}</span>
                {matchedQuote && (
                  <span className="text-xs text-yellow-600 font-medium">
                    ★ {CRITERION_LABEL[matchedQuote.criterion] ?? matchedQuote.criterion}
                  </span>
                )}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
