import { useState, useEffect } from 'react'
import type { AnalysisDetailResult, TranscriptSegment, Quote } from '../types'
import { fetchResultDetail, audioUrl } from '../lib/api'
import { ScoreCard } from './ScoreCard'
import { TranscriptView } from './TranscriptView'
import { AudioPlayer } from './AudioPlayer'

interface AnalysisDetailProps {
  fileId: string
  onBack: () => void
}

// ── Mock detail data ──────────────────────────────────────────────────────────
function buildMockDetail(fileId: string): AnalysisDetailResult {
  const segments: TranscriptSegment[] = [
    { speaker: 'operator', start: 0.0, end: 4.2, text: 'Добрый день, компания Альфа, Иван, чем могу помочь?' },
    { speaker: 'client', start: 4.5, end: 9.0, text: 'Здравствуйте, я звоню по поводу моего заказа, он ещё не пришёл.' },
    { speaker: 'operator', start: 9.3, end: 14.5, text: 'Конечно, давайте проверим. Назовите, пожалуйста, номер заказа.' },
    { speaker: 'client', start: 14.8, end: 18.2, text: 'Да, это номер 1234567.' },
    { speaker: 'operator', start: 18.5, end: 28.0, text: 'Вижу ваш заказ — он отправлен три дня назад и сейчас находится на сортировочном центре. Ожидайте доставку в течение 1-2 дней.' },
    { speaker: 'client', start: 28.3, end: 32.0, text: 'Хорошо, спасибо. А можно как-то ускорить?' },
    { speaker: 'operator', start: 32.3, end: 42.0, text: 'К сожалению, ускорить доставку на данном этапе не представляется возможным, но я зафиксирую ваш запрос. Есть ли ещё вопросы, которые я могу решить прямо сейчас?' },
    { speaker: 'client', start: 42.5, end: 45.0, text: 'Нет, всё понятно. Спасибо.' },
    { speaker: 'operator', start: 45.2, end: 50.0, text: 'Пожалуйста! Хорошего дня. Всего доброго!' },
  ]

  const quotes: Quote[] = [
    { text: 'Добрый день, компания Альфа, Иван, чем могу помочь?', criterion: 'standard', timestamp: 0.0 },
    { text: 'Конечно, давайте проверим.', criterion: 'loyalty', timestamp: 9.3 },
    { text: 'Пожалуйста! Хорошего дня. Всего доброго!', criterion: 'kindness', timestamp: 45.2 },
  ]

  return {
    file_id: fileId,
    original_name: `demo_${fileId}.mp3`,
    operator_name: 'Иван Петров',
    duration_sec: 50,
    status: 'done',
    created_at: new Date().toISOString(),
    diarization: {
      method: 'pyannote',
      confidence: 88.5,
      num_speakers: 2,
      segments,
    },
    analysis: {
      standard: 87,
      loyalty: 79,
      kindness: 94,
      overall: 86,
      summary: 'Оператор соблюдает стандарты приветствия и завершения звонка. Клиентоориентированность на хорошем уровне — предложил решение, уточнил наличие других вопросов. Тон вежливый, доброжелательный на протяжении всего разговора.',
      quotes,
    },
  }
}

// ── Main component ────────────────────────────────────────────────────────────
export function AnalysisDetail({ fileId, onBack }: AnalysisDetailProps) {
  const [detail, setDetail] = useState<AnalysisDetailResult | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isMock, setIsMock] = useState(false)
  const [seekTime, setSeekTime] = useState<number | undefined>()
  const [quotesOpen, setQuotesOpen] = useState(true)

  useEffect(() => {
    let cancelled = false
    setIsLoading(true)
    fetchResultDetail(fileId)
      .then((data) => {
        if (!cancelled) { setDetail(data); setIsMock(false) }
      })
      .catch(() => {
        if (!cancelled) { setDetail(buildMockDetail(fileId)); setIsMock(true) }
      })
      .finally(() => { if (!cancelled) setIsLoading(false) })
    return () => { cancelled = true }
  }, [fileId])

  if (isLoading) {
    return (
      <div className="flex justify-center py-20">
        <div className="w-10 h-10 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (!detail) return null

  const a = detail.analysis
  const segments = detail.diarization?.segments ?? []
  const quotes = a?.quotes ?? []
  const src = detail.audio_url ?? (detail.status === 'done' ? audioUrl(fileId) : undefined)

  return (
    <div className="flex flex-col gap-6">
      {/* Back + meta */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <button
          onClick={onBack}
          className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 transition-colors"
        >
          ← Назад к результатам
        </button>
        {isMock && (
          <span className="text-xs text-yellow-600 bg-yellow-50 px-2 py-1 rounded-lg">
            demo-данные
          </span>
        )}
      </div>

      {/* Header */}
      <div className="bg-white rounded-2xl border border-gray-200 px-6 py-5 shadow-sm">
        <div className="flex flex-wrap gap-4 items-start justify-between">
          <div>
            <h2 className="text-xl font-bold text-gray-800">{detail.original_name}</h2>
            <p className="text-sm text-gray-500 mt-1">
              Оператор: <span className="font-medium text-gray-700">{detail.operator_name}</span>
              {' · '}
              {new Date(detail.created_at).toLocaleDateString('ru-RU', {
                day: '2-digit', month: 'long', year: 'numeric',
              })}
            </p>
          </div>
          <span className={`px-3 py-1 rounded-full text-xs font-medium ${
            detail.status === 'done'
              ? 'bg-green-100 text-green-700'
              : 'bg-gray-100 text-gray-500'
          }`}>
            {detail.status === 'done' ? 'Готово' : detail.status}
          </span>
        </div>
      </div>

      {/* Scores */}
      <div className="bg-white rounded-2xl border border-gray-200 px-6 py-5 shadow-sm">
        <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-4">Оценки</h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <ScoreCard label="Итоговая оценка" score={a?.overall} size="lg" />
          <ScoreCard label="Стандарты" score={a?.standard} />
          <ScoreCard label="Лояльность" score={a?.loyalty} />
          <ScoreCard label="Доброжелательность" score={a?.kindness} />
        </div>
      </div>

      {/* Summary */}
      {a?.summary && (
        <div className="bg-white rounded-2xl border border-gray-200 px-6 py-5 shadow-sm">
          <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2">Резюме</h3>
          <p className="text-gray-700 text-sm leading-relaxed">{a.summary}</p>
        </div>
      )}

      {/* Quotes */}
      {quotes.length > 0 && (
        <div className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
          <button
            onClick={() => setQuotesOpen((o) => !o)}
            className="w-full flex items-center justify-between px-6 py-4 text-left hover:bg-gray-50 transition-colors"
          >
            <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide">
              Ключевые цитаты ({quotes.length})
            </h3>
            <span className="text-gray-400">{quotesOpen ? '▲' : '▼'}</span>
          </button>
          {quotesOpen && (
            <div className="px-6 pb-5 flex flex-col gap-2">
              {quotes.map((q, i) => (
                <div
                  key={i}
                  onClick={() => q.timestamp != null && setSeekTime(q.timestamp)}
                  className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 cursor-pointer hover:bg-yellow-100 transition-colors"
                >
                  <p className="text-sm text-gray-800">«{q.text}»</p>
                  <p className="text-xs text-yellow-600 mt-1 font-medium">
                    {{ standard: 'Стандарты', loyalty: 'Лояльность', kindness: 'Доброжелательность' }[q.criterion]}
                    {q.timestamp != null && ` · ${Math.floor(q.timestamp / 60)}:${String(Math.floor(q.timestamp % 60)).padStart(2, '0')}`}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Audio player */}
      <div className="bg-white rounded-2xl border border-gray-200 px-6 py-5 shadow-sm">
        <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">Аудио</h3>
        <AudioPlayer
          src={src}
          currentTime={seekTime}
          onSeek={setSeekTime}
        />
      </div>

      {/* Transcript */}
      {segments.length > 0 && (
        <div className="bg-white rounded-2xl border border-gray-200 px-6 py-5 shadow-sm">
          <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-4">
            Транскрипт
            {detail.diarization && (
              <span className="ml-2 text-xs text-gray-400 normal-case font-normal">
                {detail.diarization.num_speakers} говорящих
                {detail.diarization.confidence && ` · уверенность ${detail.diarization.confidence}%`}
              </span>
            )}
          </h3>
          <TranscriptView
            segments={segments}
            quotes={quotes}
            onTimestampClick={setSeekTime}
          />
        </div>
      )}
    </div>
  )
}
