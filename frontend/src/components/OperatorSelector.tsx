import { useState, useEffect, useRef, useCallback } from 'react'
import { fetchOperators } from '../lib/api'

interface OperatorSelectorProps {
  value: string
  onChange: (name: string) => void
  disabled?: boolean
}

function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}

export function OperatorSelector({ value, onChange, disabled }: OperatorSelectorProps) {
  const [query, setQuery] = useState(value)
  const [suggestions, setSuggestions] = useState<string[]>([])
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const [loading, setLoading] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const debouncedQuery = useDebounce(query, 250)

  // Sync query when value is set externally (e.g. reset)
  useEffect(() => {
    if (value !== query) setQuery(value)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  // Fetch suggestions on debounced query change
  useEffect(() => {
    if (debouncedQuery.trim().length === 0) {
      setSuggestions([])
      return
    }
    let cancelled = false
    setLoading(true)
    fetchOperators(debouncedQuery)
      .then((list) => {
        if (!cancelled) {
          setSuggestions(list)
          setOpen(list.length > 0)
          setActiveIndex(-1)
        }
      })
      .catch(() => {
        if (!cancelled) setSuggestions([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [debouncedQuery])

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const select = useCallback((name: string) => {
    setQuery(name)
    onChange(name)
    setOpen(false)
    setActiveIndex(-1)
  }, [onChange])

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value
    setQuery(val)
    onChange(val)
    if (val.trim().length === 0) {
      setSuggestions([])
      setOpen(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!open || suggestions.length === 0) return

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex((i) => Math.min(i + 1, suggestions.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter' && activeIndex >= 0) {
      e.preventDefault()
      select(suggestions[activeIndex])
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div ref={containerRef} className="flex flex-col gap-1 relative">
      <label className="text-sm font-medium text-gray-700">
        Оператор <span className="text-red-500">*</span>
      </label>

      <div className="relative">
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={handleInputChange}
          onFocus={() => suggestions.length > 0 && setOpen(true)}
          onKeyDown={handleKeyDown}
          placeholder="Иван Петров"
          disabled={disabled}
          autoComplete="off"
          className="w-full border border-gray-300 rounded-lg px-3 py-2 pr-8 text-sm
                     focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent
                     disabled:opacity-50 disabled:cursor-not-allowed"
        />
        {loading && (
          <div className="absolute right-2 top-1/2 -translate-y-1/2">
            <div className="w-4 h-4 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
          </div>
        )}
      </div>

      {open && suggestions.length > 0 && (
        <ul className="absolute top-full left-0 right-0 z-50 mt-1 bg-white border border-gray-200
                       rounded-lg shadow-lg overflow-hidden max-h-48 overflow-y-auto">
          {suggestions.map((name, i) => (
            <li
              key={name}
              onMouseDown={() => select(name)}
              onMouseEnter={() => setActiveIndex(i)}
              className={`px-3 py-2 text-sm cursor-pointer transition-colors
                ${i === activeIndex
                  ? 'bg-blue-50 text-blue-700'
                  : 'text-gray-800 hover:bg-gray-50'
                }`}
            >
              {name}
            </li>
          ))}
        </ul>
      )}

      {query.trim().length > 0 && suggestions.length === 0 && !loading && (
        <p className="text-xs text-gray-400 mt-0.5">Новый оператор — будет создан при загрузке</p>
      )}
    </div>
  )
}
