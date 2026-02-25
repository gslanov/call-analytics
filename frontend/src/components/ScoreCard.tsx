interface ScoreCardProps {
  label: string
  score?: number
  size?: 'sm' | 'lg'
}

function scoreColors(score?: number) {
  if (score == null) return { stroke: '#d1d5db', text: 'text-gray-400', bg: 'bg-gray-50' }
  if (score >= 85) return { stroke: '#22c55e', text: 'text-green-600', bg: 'bg-green-50' }
  if (score >= 60) return { stroke: '#eab308', text: 'text-yellow-600', bg: 'bg-yellow-50' }
  return { stroke: '#ef4444', text: 'text-red-600', bg: 'bg-red-50' }
}

export function ScoreCard({ label, score, size = 'sm' }: ScoreCardProps) {
  const { stroke, text, bg } = scoreColors(score)
  const isLg = size === 'lg'
  const dim = isLg ? 96 : 72
  const r = (dim - (isLg ? 16 : 12)) / 2
  const sw = isLg ? 8 : 6
  const circ = 2 * Math.PI * r
  const dash = score != null ? (score / 100) * circ : 0

  return (
    <div className={`flex flex-col items-center gap-2 rounded-xl p-4 ${bg}`}>
      {/* Ring with score centered */}
      <div className="relative" style={{ width: dim, height: dim }}>
        <svg width={dim} height={dim} style={{ transform: 'rotate(-90deg)' }}>
          <circle cx={dim / 2} cy={dim / 2} r={r} fill="none" stroke="#e5e7eb" strokeWidth={sw} />
          <circle
            cx={dim / 2} cy={dim / 2} r={r}
            fill="none" stroke={stroke} strokeWidth={sw}
            strokeLinecap="round"
            strokeDasharray={`${dash} ${circ}`}
          />
        </svg>
        {/* Centered text */}
        <div className="absolute inset-0 flex items-center justify-center">
          <span className={`font-bold ${isLg ? 'text-2xl' : 'text-base'} ${text}`}>
            {score != null ? `${score}` : '—'}
          </span>
        </div>
      </div>

      <p className={`font-medium text-gray-600 text-center leading-tight ${isLg ? 'text-sm' : 'text-xs'}`}>
        {label}
      </p>
    </div>
  )
}
