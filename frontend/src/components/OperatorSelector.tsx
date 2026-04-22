import { useState, useEffect, useRef, useCallback } from 'react'
import { fetchOperators } from '../lib/api'

interface OperatorSelectorProps {
  value: string
  onChange: (name: string) => void
  disabled?: boolean
  /** Показывать ли кнопку «× очистить» когда выбран оператор. По умолчанию — true. */
  clearable?: boolean
}

function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}

export function OperatorSelector({
  value,
  onChange,
  disabled,
  clearable = true,
}: OperatorSelectorProps) {
  const [query, setQuery] = useState(value)
  const [allOperators, setAllOperators] = useState<string[]>([])
  const [suggestions, setSuggestions] = useState<string[]>([])
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const [loading, setLoading] = useState(false)
  const [allLoaded, setAllLoaded] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const debouncedQuery = useDebounce(query, 250)

  // Sync query when value is set externally (e.g. reset)
  useEffect(() => {
    if (value !== query) setQuery(value)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  // Load full list of operators once (для dropdown при пустом запросе).
  useEffect(() => {
    let cancelled = false
    fetchOperators('')
      .then((list) => {
        if (cancelled) return
        setAllOperators(list)
        setAllLoaded(true)
      })
      .catch(() => {
        if (!cancelled) setAllLoaded(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Обновляем suggestions: либо по запросу с бэка, либо фильтруем локально по загруженному списку
  useEffect(() => {
    const trimmed = debouncedQuery.trim()
    if (trimmed.length === 0) {
      setSuggestions(allOperators)
      setActiveIndex(-1)
      return
    }
    // Если полный список загружен — фильтруем его локально (быстро, без сетевого запроса)
    if (allLoaded) {
      const lower = trimmed.toLowerCase()
      setSuggestions(allOperators.filter((n) => n.toLowerCase().includes(lower)))
      setActiveIndex(-1)
      return
    }
    // Fallback: идём на бэк
    let cancelled = false
    setLoading(true)
    fetchOperators(trimmed)
      .then((list) => {
        if (!cancelled) {
          setSuggestions(list)
          setActiveIndex(-1)
        }
      })
      .catch(() => {
        if (!cancelled) setSuggestions([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [debouncedQuery, allOperators, allLoaded])

  // Закрытие по клику вне компонента
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const select = useCallback(
    (name: string) => {
      setQuery(name)
      onChange(name)
      setOpen(false)
      setActiveIndex(-1)
    },
    [onChange],
  )

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value
    setQuery(val)
    onChange(val)
    setOpen(true)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'Enter') {
        setOpen(true)
        e.preventDefault()
      }
      return
    }

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex((i) => Math.min(i + 1, suggestions.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter' && activeIndex >= 0 && suggestions[activeIndex]) {
      e.preventDefault()
      select(suggestions[activeIndex])
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  const handleClear = () => {
    setQuery('')
    onChange('')
    setSuggestions(allOperators)
    inputRef.current?.focus()
    setOpen(true)
  }

  return (
    <div ref={containerRef} className="flex flex-col gap-1 relative">
      <label className="text-sm font-medium text-gray-700">Оператор</label>

      <div className="relative">
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={handleInputChange}
          onFocus={() => setOpen(true)}
          onClick={() => setOpen(true)}
          onKeyDown={handleKeyDown}
          placeholder="Все операторы"
          disabled={disabled}
          autoComplete="off"
          title="Выберите оператора из списка или начните вводить имя"
          className="w-full border border-gray-300 rounded-lg pl-3 pr-16 py-2 text-sm
                     focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent
                     disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
        />

        {/* Правый блок: loading / clear / chevron */}
        <div className="absolute right-1 top-1/2 -translate-y-1/2 flex items-center gap-0.5">
          {loading && (
            <div className="w-4 h-4 border-2 border-blue-400 border-t-transparent rounded-full animate-spin mr-1" />
          )}
          {clearable && query && !disabled && (
            <button
              type="button"
              onClick={handleClear}
              className="w-6 h-6 flex items-center justify-center text-gray-400 hover:text-gray-700 rounded transition-colors"
              title="Сбросить"
              aria-label="Сбросить"
            >
              ×
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              if (disabled) return
              setOpen((o) => !o)
              inputRef.current?.focus()
            }}
            className="w-6 h-6 flex items-center justify-center text-gray-400 hover:text-gray-700 rounded transition-colors"
            title="Показать/скрыть список"
            aria-label="Открыть список"
            tabIndex={-1}
          >
            <svg
              className={`w-4 h-4 transition-transform ${open ? 'rotate-180' : ''}`}
              viewBox="0 0 20 20"
              fill="currentColor"
            >
              <path
                fillRule="evenodd"
                d="M5.23 7.21a.75.75 0 011.06.02L10 11.06l3.71-3.83a.75.75 0 111.08 1.04l-4.25 4.39a.75.75 0 01-1.08 0L5.21 8.27a.75.75 0 01.02-1.06z"
                clipRule="evenodd"
              />
            </svg>
          </button>
        </div>
      </div>

      {open && suggestions.length > 0 && (
        <ul className="absolute top-full left-0 right-0 z-50 mt-1 bg-white border border-gray-200
                       rounded-lg shadow-lg overflow-hidden max-h-60 overflow-y-auto">
          {suggestions.map((name, i) => (
            <li
              key={name}
              onMouseDown={(e) => {
                e.preventDefault()
                select(name)
              }}
              onMouseEnter={() => setActiveIndex(i)}
              className={`px-3 py-2 text-sm cursor-pointer transition-colors
                ${
                  i === activeIndex
                    ? 'bg-blue-50 text-blue-700'
                    : 'text-gray-800 hover:bg-gray-50'
                }
                ${name === value ? 'font-semibold' : ''}`}
            >
              {name}
            </li>
          ))}
        </ul>
      )}

      {open && suggestions.length === 0 && allLoaded && (
        <div className="absolute top-full left-0 right-0 z-50 mt-1 bg-white border border-gray-200
                        rounded-lg shadow-lg px-3 py-2 text-xs text-gray-400">
          {query.trim()
            ? 'Не найдено — будет создан при загрузке'
            : 'Нет операторов в системе'}
        </div>
      )}
    </div>
  )
}
