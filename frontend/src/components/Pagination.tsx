interface PaginationProps {
  page: number
  total: number
  limit: number
  onPageChange: (page: number) => void
  onLimitChange: (limit: number) => void
}

const LIMIT_OPTIONS = [10, 20, 50]

export function Pagination({ page, total, limit, onPageChange, onLimitChange }: PaginationProps) {
  const pages = Math.max(1, Math.ceil(total / limit))
  const isFirst = page <= 1
  const isLast = page >= pages

  // Generate page number buttons (up to 5 visible)
  const getPageNumbers = (): (number | '…')[] => {
    if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1)
    const nums: (number | '…')[] = [1]
    if (page > 3) nums.push('…')
    for (let p = Math.max(2, page - 1); p <= Math.min(pages - 1, page + 1); p++) nums.push(p)
    if (page < pages - 2) nums.push('…')
    nums.push(pages)
    return nums
  }

  return (
    <div className="flex flex-col sm:flex-row items-center justify-between gap-3 px-1 py-2">
      {/* Results count + limit selector */}
      <div className="flex items-center gap-2 text-sm text-gray-500">
        <span>Показывать по</span>
        <select
          value={limit}
          onChange={(e) => onLimitChange(Number(e.target.value))}
          className="border border-gray-300 rounded px-2 py-1 text-sm
                     focus:outline-none focus:ring-2 focus:ring-blue-400"
        >
          {LIMIT_OPTIONS.map((l) => (
            <option key={l} value={l}>{l}</option>
          ))}
        </select>
        <span>из {total} результатов</span>
      </div>

      {/* Page navigation */}
      <div className="flex items-center gap-1">
        <PageBtn onClick={() => onPageChange(page - 1)} disabled={isFirst} label="←" />

        {getPageNumbers().map((p, i) =>
          p === '…' ? (
            <span key={`ellipsis-${i}`} className="px-2 text-gray-400 select-none">…</span>
          ) : (
            <PageBtn
              key={p}
              onClick={() => onPageChange(p as number)}
              disabled={p === page}
              active={p === page}
              label={String(p)}
            />
          )
        )}

        <PageBtn onClick={() => onPageChange(page + 1)} disabled={isLast} label="→" />
      </div>
    </div>
  )
}

function PageBtn({
  onClick,
  disabled,
  active,
  label,
}: {
  onClick: () => void
  disabled: boolean
  active?: boolean
  label: string
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`
        w-8 h-8 rounded text-sm font-medium transition-colors
        ${active
          ? 'bg-blue-600 text-white cursor-default'
          : disabled
          ? 'text-gray-300 cursor-not-allowed'
          : 'text-gray-600 hover:bg-gray-100'
        }
      `}
    >
      {label}
    </button>
  )
}
