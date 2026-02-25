import { useState, useCallback, useEffect, useRef } from 'react'
import type { AnalysisResult, ResultFilters } from '../types'
import { fetchResults as apiFetchResults } from '../lib/api'

// ── Mock data (25 items) for smoke-test when backend is unavailable ──────────
const OPERATORS = [
  'Иван Петров', 'Анна Смирнова', 'Дмитрий Козлов',
  'Мария Новикова', 'Сергей Федоров',
]

function rnd(min: number, max: number) {
  return Math.floor(Math.random() * (max - min + 1)) + min
}

export const MOCK_RESULTS: AnalysisResult[] = Array.from({ length: 25 }, (_, i) => {
  const standard = rnd(40, 100)
  const loyalty = rnd(40, 100)
  const kindness = rnd(40, 100)
  const overall = Math.round(standard * 0.4 + loyalty * 0.3 + kindness * 0.3)
  const daysAgo = rnd(0, 30)
  const date = new Date(Date.now() - daysAgo * 86400_000).toISOString()
  return {
    file_id: `mock-${i + 1}`,
    original_name: `call_${String(i + 1).padStart(3, '0')}.mp3`,
    operator_name: OPERATORS[i % OPERATORS.length],
    duration_sec: rnd(60, 900),
    status: 'done',
    analysis: { standard, loyalty, kindness, overall, summary: '' },
    created_at: date,
  }
})

// ── Hook ─────────────────────────────────────────────────────────────────────
const DEFAULT_LIMIT = 20

export function useResults() {
  const [results, setResults] = useState<AnalysisResult[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [limit, setLimit] = useState(DEFAULT_LIMIT)
  const [filters, setFilters] = useState<ResultFilters>({})
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [useMock, setUseMock] = useState(false)

  // Abort previous fetch when filters/page/limit change
  const abortRef = useRef<AbortController | null>(null)

  const load = useCallback(
    async (currentFilters: ResultFilters, currentPage: number, currentLimit: number) => {
      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller

      setIsLoading(true)
      setError(null)

      try {
        const data = await apiFetchResults(currentFilters, currentPage, currentLimit)
        if (controller.signal.aborted) return
        setResults(data.items)
        setTotal(data.total)
        setUseMock(false)
      } catch (err) {
        if (controller.signal.aborted) return
        // Fallback to mock when backend not available
        const msg = err instanceof Error ? err.message : 'Ошибка'
        if (msg.includes('Failed to fetch') || msg.startsWith('HTTP')) {
          const sorted = applyMockFilters(MOCK_RESULTS, currentFilters)
          const start = (currentPage - 1) * currentLimit
          setResults(sorted.slice(start, start + currentLimit))
          setTotal(sorted.length)
          setUseMock(true)
        } else {
          setError(msg)
        }
      } finally {
        if (!controller.signal.aborted) setIsLoading(false)
      }
    },
    []
  )

  // Refetch when filters change → reset to page 1
  const applyFilters = useCallback(
    (newFilters: ResultFilters) => {
      setFilters(newFilters)
      setPage(1)
      load(newFilters, 1, limit)
    },
    [limit, load]
  )

  const goToPage = useCallback(
    (p: number) => {
      setPage(p)
      load(filters, p, limit)
    },
    [filters, limit, load]
  )

  const setPageLimit = useCallback(
    (l: number) => {
      setLimit(l)
      setPage(1)
      load(filters, 1, l)
    },
    [filters, load]
  )

  // Initial load
  useEffect(() => {
    load(filters, page, limit)
    return () => abortRef.current?.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const resetFilters = useCallback(() => {
    setFilters({})
    setPage(1)
    load({}, 1, limit)
  }, [limit, load])

  return {
    results, total, page, limit, filters,
    isLoading, error, useMock,
    applyFilters, resetFilters, goToPage, setPageLimit,
  }
}

// ── Mock filtering helper ─────────────────────────────────────────────────────
function applyMockFilters(items: AnalysisResult[], f: ResultFilters): AnalysisResult[] {
  let out = [...items]
  if (f.operator) {
    const q = f.operator.toLowerCase()
    out = out.filter((r) => r.operator_name.toLowerCase().includes(q))
  }
  if (f.date_from) {
    const from = new Date(f.date_from).getTime()
    out = out.filter((r) => new Date(r.created_at).getTime() >= from)
  }
  if (f.date_to) {
    const to = new Date(f.date_to).getTime() + 86_400_000 // include the whole day
    out = out.filter((r) => new Date(r.created_at).getTime() <= to)
  }
  if (f.score_min != null) out = out.filter((r) => (r.analysis?.overall ?? 0) >= f.score_min!)
  if (f.score_max != null) out = out.filter((r) => (r.analysis?.overall ?? 100) <= f.score_max!)

  const sortKey = f.sort ?? 'created_at'
  const dir = f.order === 'asc' ? 1 : -1

  out.sort((a, b) => {
    if (sortKey === 'operator_name') {
      return dir * a.operator_name.localeCompare(b.operator_name)
    }
    if (sortKey === 'created_at') {
      return dir * (new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
    }
    const av = a.analysis?.[sortKey as 'overall' | 'standard' | 'loyalty' | 'kindness'] ?? 0
    const bv = b.analysis?.[sortKey as 'overall' | 'standard' | 'loyalty' | 'kindness'] ?? 0
    return dir * (av - bv)
  })

  return out
}
