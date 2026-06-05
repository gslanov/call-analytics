import { useState, useEffect } from 'react'
import type { AnalysisDetailResult, TranscriptSegment, Quote, CriteriaGroup } from '../types'
import { fetchResultDetail, audioUrl, updateCriterion, setReviewed } from '../lib/api'
import { ScoreCard } from './ScoreCard'
import { TranscriptView } from './TranscriptView'
import { AudioPlayer } from './AudioPlayer'

// ── Criteria labels ──────────────────────────────────────────────────────────
const CRITERIA_LABELS: Record<string, Record<string, string>> = {
  standard: {
    introduced_self: 'Представился',
    named_company: 'Назвал компанию',
    clarified_delivery_date: 'Уточнил дату доставки',
    stated_delivery_time: 'Проговорил время доставки',
    stated_full_address: 'Проговорил адрес полностью',
    named_metro: 'Назвал метро',
    stated_order_contents: 'Проговорил состав заказа',
    offered_upsell: 'Предложил апсейл',
    explained_upsell_benefit: 'Рассказал про выгоду апсейла',
    named_order_total: 'Назвал сумму заказа',
    clarified_courier_comment: 'Уточнил комментарий для курьера',
    clarified_portion_sufficiency: 'Уточнил кол-во человек / хватит ли пирогов',
    clarified_cash_change: 'Уточнил сдачу при оплате наличными',
  },
  loyalty: {
    addressed_by_name: 'Обращался по имени',
    did_not_raise_voice: 'Не повышал тон',
    friendly_calm_confident_tone: 'Дружелюбный, спокойный и уверенный тон',
    did_not_interrupt: 'Не перебивал клиента',
    calm_in_conflict: 'Спокойствие в конфликте',
    answered_all_questions: 'Ответил на все вопросы',
  },
  kindness: {
    no_profanity_filler_words: 'Нет мата/слов-паразитов',
    polite_goodbye: 'Вежливо попрощался',
    no_sarcasm_irony_aggression: 'Нет сарказма/иронии/агрессии',
  },
  markers: {
    prepayment_20k: 'Предоплата заказа ≥20 000 ₽',
    order_confirmation: 'Звонок для подтверждения заказа',
  },
}

const GROUP_LABELS: Record<string, string> = {
  standard: 'Стандарты',
  loyalty: 'Лояльность',
  kindness: 'Доброжелательность',
  markers: 'Маркеры',
}

function groupStats(items: CriteriaGroup) {
  const entries = Object.values(items)
  const applicable = entries.filter((v) => v !== null)
  const passed = applicable.filter((v) => v === true).length
  return { passed, total: applicable.length }
}

interface AnalysisDetailProps {
  fileId: string
  onBack: () => void
  onReject?: (fileId: string, reason: string) => void
  onDelete?: (fileId: string) => void
  onReviewed?: (fileId: string, reviewed: boolean) => void
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
export function AnalysisDetail({ fileId, onBack, onReject, onDelete, onReviewed }: AnalysisDetailProps) {
  const [detail, setDetail] = useState<AnalysisDetailResult | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isMock, setIsMock] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [seekTime, setSeekTime] = useState<number | undefined>()
  const [quotesOpen, setQuotesOpen] = useState(true)
  const [savingCriterion, setSavingCriterion] = useState<string | null>(null)
  const [savingReview, setSavingReview] = useState(false)

  const toggleReview = async () => {
    if (!detail || savingReview || isMock) return
    const next = !detail.reviewed_by_rop
    setSavingReview(true)
    try {
      await setReviewed(fileId, next)
      setDetail({ ...detail, reviewed_by_rop: next })
      onReviewed?.(fileId, next)
    } catch (e) {
      alert('Ошибка: ' + (e as Error).message)
    } finally {
      setSavingReview(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    setIsLoading(true)
    setError(null)
    fetchResultDetail(fileId)
      .then((data) => {
        if (!cancelled) { setDetail(data); setIsMock(false) }
      })
      .catch(() => {
        if (!cancelled) { setDetail(null); setError('Не удалось загрузить данные анализа') }
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

  if (error && !detail) {
    return (
      <div className="flex flex-col items-center gap-4 py-16">
        <p className="text-red-500 text-sm">{error}</p>
        <div className="flex gap-3">
          <button onClick={onBack} className="text-sm text-gray-500 hover:text-gray-700">← Назад</button>
          <button
            onClick={() => { setDetail(buildMockDetail(fileId)); setIsMock(true); setError(null) }}
            className="text-sm text-blue-500 hover:text-blue-700"
          >
            Показать демо-данные
          </button>
        </div>
      </div>
    )
  }

  if (!detail) return null

  const a = detail.analysis
  const segments = detail.diarization?.segments ?? []
  const quotes = a?.quotes ?? []
  const src = detail.audio_url ?? (detail.status === 'done' ? audioUrl(fileId) : undefined)
  const fullText = detail.transcription?.full_text ?? (detail as unknown as Record<string, unknown>).full_text as string | undefined

  return (
    <div className="flex flex-col gap-6">
      {/* Meta */}
      {isMock && (
        <div className="flex items-center justify-end">
          <span className="text-xs text-yellow-600 bg-yellow-50 px-2 py-1 rounded-lg">
            demo-данные
          </span>
        </div>
      )}

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
              {detail.analysis?.llm_model && (
                <span className="ml-2 text-purple-500 text-xs">
                  {detail.analysis.llm_model}
                </span>
              )}
            </p>
          </div>
          <div className="flex items-center gap-3">
            {detail.reviewed_by_rop ? (
              <span className="px-3 py-1 rounded-full text-xs font-medium bg-green-100 text-green-700 flex items-center gap-1.5">
                ✓ Проверено РОП
                <button
                  onClick={toggleReview}
                  disabled={savingReview || isMock}
                  className="text-green-500 hover:text-green-800 leading-none disabled:opacity-40"
                  title="Снять отметку"
                >
                  ✕
                </button>
              </span>
            ) : (
              <button
                onClick={toggleReview}
                disabled={savingReview || isMock}
                className="px-3 py-1.5 rounded-lg text-xs font-medium border border-green-300 text-green-700 hover:bg-green-50 transition-colors disabled:opacity-50"
              >
                {savingReview ? 'Сохраняю…' : '✓ Отметить проверено РОП'}
              </button>
            )}
            {onDelete && (
              <button
                onClick={() => {
                  if (confirm('Удалить этот звонок? Это действие необратимо.')) {
                    onDelete(fileId)
                    onBack()
                  }
                }}
                className="px-3 py-1.5 rounded-lg text-xs font-medium border border-red-300 text-red-600 hover:bg-red-50 transition-colors"
              >
                Удалить
              </button>
            )}
            {onReject && detail.analysis && !detail.analysis.rejected && (
              <button
                onClick={() => {
                  const reason = prompt('Причина отклонения оценки:')
                  if (reason) {
                    onReject(fileId, reason)
                    onBack()
                  }
                }}
                className="px-3 py-1.5 rounded-lg text-xs font-medium border border-orange-300 text-orange-600 hover:bg-orange-50 transition-colors"
              >
                Оспорить оценку
              </button>
            )}
            {detail.analysis?.rejected && (
              <span className="px-3 py-1 rounded-full text-xs font-medium bg-orange-100 text-orange-700" title={detail.analysis.rejection_reason || ''}>
                Оценка отклонена
              </span>
            )}
            <span className={`px-3 py-1 rounded-full text-xs font-medium ${
              detail.status === 'done'
                ? 'bg-green-100 text-green-700'
                : 'bg-gray-100 text-gray-500'
            }`}>
              {detail.status === 'done' ? 'Готово' : detail.status}
            </span>
          </div>
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

      {/* Criteria checklist — clickable to toggle */}
      {a?.criteria_details && (
        <div className="bg-white rounded-2xl border border-gray-200 px-6 py-5 shadow-sm">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide">Чек-лист критериев</h3>
            <span className="text-xs text-gray-400">нажмите на галочку, чтобы изменить</span>
          </div>
          <div className="flex flex-col gap-5">
            {(['standard', 'loyalty', 'kindness', 'markers'] as const).map((group) => {
              // For scored groups — hide if missing. For markers — show even if empty
              // (старые звонки без markers — даём РОП проставить вручную по списку ключей).
              const raw = a.criteria_details![group]
              let items: CriteriaGroup
              if (group === 'markers') {
                const known = CRITERIA_LABELS.markers ?? {}
                const base: CriteriaGroup = {}
                for (const k of Object.keys(known)) base[k] = null
                items = { ...base, ...(raw ?? {}) }
              } else {
                if (!raw) return null
                items = raw
              }
              const labels = CRITERIA_LABELS[group] ?? {}
              const { passed, total } = groupStats(items)
              const isMarkers = group === 'markers'
              return (
                <div key={group} className={isMarkers ? 'pt-4 mt-1 border-t border-gray-100' : ''}>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-sm font-semibold text-gray-700">{GROUP_LABELS[group]}</span>
                    {isMarkers ? (
                      <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-gray-100 text-gray-500">
                        не влияют на оценку
                      </span>
                    ) : (
                      <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                        total === 0 ? 'bg-gray-100 text-gray-500'
                          : passed === total ? 'bg-green-100 text-green-700'
                          : passed / total >= 0.7 ? 'bg-yellow-100 text-yellow-700'
                          : 'bg-red-100 text-red-700'
                      }`}>
                        {total === 0 ? 'Н/П' : `${passed}/${total}`}
                      </span>
                    )}
                  </div>
                  <div className="grid gap-1">
                    {Object.entries(items).map(([key, val]) => {
                      const reason = a.criteria_details?.reasons?.[group]?.[key] as string | undefined
                      const ts = a.criteria_details?.reasons?.[`${group}_timestamps`]?.[key] as number | undefined
                      const fmtTs = ts != null ? `${Math.floor(ts / 60)}:${String(Math.floor(ts % 60)).padStart(2, '0')}` : null
                      const isSaving = savingCriterion === `${group}.${key}`
                      const handleToggle = async () => {
                        if (isMock || isSaving) return
                        const newVal = val === true ? false : true
                        setSavingCriterion(`${group}.${key}`)
                        try {
                          const resp = await updateCriterion(fileId, group, key, newVal)
                          setDetail((prev) => {
                            if (!prev) return prev
                            return {
                              ...prev,
                              analysis: prev.analysis ? {
                                ...prev.analysis,
                                standard: resp.standard,
                                loyalty: resp.loyalty,
                                kindness: resp.kindness,
                                overall: resp.overall,
                                criteria_details: resp.criteria_details as unknown as import('../types').CriteriaDetails,
                              } : prev.analysis,
                            }
                          })
                        } catch (e) {
                          alert('Ошибка сохранения: ' + (e as Error).message)
                        } finally {
                          setSavingCriterion(null)
                        }
                      }
                      return (
                        <div key={key} className="flex items-start gap-2 py-1.5 px-2 rounded hover:bg-gray-50">
                          <button
                            onClick={handleToggle}
                            disabled={isSaving}
                            className={`mt-0.5 flex-shrink-0 cursor-pointer transition-transform hover:scale-110 ${isSaving ? 'opacity-50 animate-pulse' : ''}`}
                            title={val === true ? 'Отметить как невыполненное' : val === false ? 'Отметить как выполненное' : 'Отметить как выполненное'}
                          >
                            {val === true && <span className="w-5 h-5 flex items-center justify-center rounded-full bg-green-100 text-green-600 text-xs font-bold">✓</span>}
                            {val === false && <span className="w-5 h-5 flex items-center justify-center rounded-full bg-red-100 text-red-600 text-xs font-bold">✗</span>}
                            {val === null && <span className="w-5 h-5 flex items-center justify-center rounded-full bg-gray-100 text-gray-400 text-xs">—</span>}
                          </button>
                          <div className="flex-1">
                            <div className="flex items-center gap-2">
                              <span className={`text-sm ${val === false ? 'text-red-700 font-medium' : val === null ? 'text-gray-400' : 'text-gray-700'}`}>
                                {labels[key] ?? key}
                              </span>
                              {fmtTs && (
                                <button
                                  onClick={() => setSeekTime(ts!)}
                                  className={`text-xs px-1.5 py-0.5 rounded font-mono hover:underline ${
                                    val === false ? 'text-red-500 bg-red-50 hover:bg-red-100' : 'text-blue-500 bg-blue-50 hover:bg-blue-100'
                                  }`}
                                >
                                  {fmtTs}
                                </button>
                              )}
                            </div>
                            {reason && (
                              <p className={`text-xs mt-0.5 ${val === false ? 'text-red-500' : 'text-gray-400'}`}>
                                {reason}
                              </p>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

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

      {/* Transcript — diarized segments or full_text fallback */}
      {segments.length > 0 ? (
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
            criteriaIssues={(() => {
              const cd = a?.criteria_details
              if (!cd?.reasons) return []
              const reasons = cd.reasons
              const issues: Array<{timestamp: number; reason: string}> = []
              for (const group of ['standard', 'loyalty', 'kindness'] as const) {
                const items = cd[group] ?? {}
                const groupReasons = reasons[group] ?? {}
                const tsKey = `${group}_timestamps` as keyof typeof reasons
                const groupTs = (reasons[tsKey] ?? {}) as Record<string, number>
                for (const [key, val] of Object.entries(items)) {
                  if (val === false && groupTs[key] != null) {
                    issues.push({ timestamp: groupTs[key], reason: groupReasons[key] ?? '' })
                  }
                }
              }
              return issues
            })()}
          />
        </div>
      ) : fullText ? (
        <div className="bg-white rounded-2xl border border-gray-200 px-6 py-5 shadow-sm">
          <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-4">
            Транскрипт
          </h3>
          <div className="max-h-[600px] overflow-y-auto text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">
            {fullText}
          </div>
        </div>
      ) : null}

      {/* Floating back button */}
      <button
        onClick={onBack}
        className="fixed bottom-6 right-6 bg-white border border-gray-300 shadow-lg rounded-full px-5 py-3 text-sm font-medium text-gray-700 hover:bg-gray-50 hover:shadow-xl transition-all z-50"
      >
        ← Назад
      </button>
    </div>
  )
}
