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

  const speakers = new Set(segments.map((s) => s.speaker))
  const isMono = speakers.size <= 1

  if (isMono) {
    return (
      <div className="max-h-[600px] overflow-y-auto pr-1">
        <div className="bg-gray-50 rounded-lg px-4 py-3 text-sm leading-relaxed text-gray-700">
          {segments.map((seg, i) => {
            const matchedQuote = findQuoteMatch(seg.text, quotes)
            return (
              <span
                key={i}
                className={`cursor-pointer hover:bg-blue-100 rounded transition-colors ${
                  matchedQuote ? 'bg-yellow-100 border-b-2 border-yellow-400' : ''
                }`}
                onClick={() => onTimestampClick?.(seg.start)}
                title={`${fmt(seg.start)}${matchedQuote ? ` · ${CRITERION_LABEL[matchedQuote.criterion] ?? ''}` : ''}`}
              >
                {seg.text}{' '}
              </span>
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-0.5 max-h-[600px] overflow-y-auto pr-1 text-sm">
      {segments.map((seg, i) => {
        const isOperator = seg.speaker === 'operator'
        const matchedQuote = findQuoteMatch(seg.text, quotes)

        return (
          <div
            key={i}
            className={`flex items-start gap-0 cursor-pointer rounded px-1 py-0.5 transition-colors hover:bg-gray-50 ${
              matchedQuote ? 'bg-yellow-50' : ''
            }`}
            onClick={() => onTimestampClick?.(seg.start)}
          >
            <span className="text-[10px] text-gray-300 w-8 flex-shrink-0 pt-0.5 select-none">
              {fmt(seg.start)}
            </span>
            <span className={`w-7 flex-shrink-0 font-bold text-xs pt-0.5 select-none ${
              isOperator ? 'text-blue-600' : 'text-orange-500'
            }`}>
              {isOperator ? 'Оп:' : 'Кл:'}
            </span>
            <span className={`flex-1 leading-snug ${
              isOperator ? 'text-gray-800' : 'text-gray-600'
            }`}>
              {seg.text}
              {matchedQuote && (
                <span className="ml-1 text-[10px] text-yellow-600 font-medium">
                  ★ {CRITERION_LABEL[matchedQuote.criterion] ?? matchedQuote.criterion}
                </span>
              )}
            </span>
          </div>
        )
      })}
    </div>
  )
}
