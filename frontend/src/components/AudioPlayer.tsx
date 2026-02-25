import { useRef, useState, useEffect } from 'react'

interface AudioPlayerProps {
  src?: string
  currentTime?: number   // externally driven seek (from TranscriptView)
  onSeek?: (time: number) => void
}

function fmt(secs: number): string {
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

export function AudioPlayer({ src, currentTime: seekTo, onSeek }: AudioPlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [unavailable, setUnavailable] = useState(false)

  // External seek
  useEffect(() => {
    if (seekTo != null && audioRef.current) {
      audioRef.current.currentTime = seekTo
      audioRef.current.play().catch(() => null)
    }
  }, [seekTo])

  const progress = duration > 0 ? (time / duration) * 100 : 0

  const togglePlay = () => {
    if (!audioRef.current) return
    if (playing) {
      audioRef.current.pause()
    } else {
      audioRef.current.play().catch(() => setUnavailable(true))
    }
  }

  const handleBarClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!audioRef.current || duration === 0) return
    const rect = e.currentTarget.getBoundingClientRect()
    const ratio = (e.clientX - rect.left) / rect.width
    const newTime = ratio * duration
    audioRef.current.currentTime = newTime
    onSeek?.(newTime)
  }

  if (!src) {
    return (
      <div className="bg-gray-100 rounded-xl px-5 py-4 flex items-center gap-3 text-gray-400 text-sm">
        <span className="text-2xl">🎵</span>
        <span>Аудиоплеер будет доступен после обработки файла</span>
      </div>
    )
  }

  return (
    <div className="bg-gray-900 rounded-xl px-5 py-4 flex flex-col gap-3">
      <audio
        ref={audioRef}
        src={src}
        onTimeUpdate={() => setTime(audioRef.current?.currentTime ?? 0)}
        onLoadedMetadata={() => setDuration(audioRef.current?.duration ?? 0)}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onError={() => setUnavailable(true)}
        preload="metadata"
      />

      {unavailable ? (
        <p className="text-gray-400 text-sm text-center">
          Аудиофайл недоступен или ещё не готов
        </p>
      ) : (
        <>
          {/* Progress bar */}
          <div
            className="w-full h-2 bg-gray-700 rounded-full cursor-pointer overflow-hidden"
            onClick={handleBarClick}
          >
            <div
              className="h-2 bg-blue-500 rounded-full transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>

          {/* Controls row */}
          <div className="flex items-center gap-4">
            <button
              onClick={togglePlay}
              className="w-9 h-9 rounded-full bg-blue-600 hover:bg-blue-700 flex items-center justify-center text-white text-sm transition-colors flex-shrink-0"
            >
              {playing ? '⏸' : '▶'}
            </button>
            <span className="text-gray-400 text-xs tabular-nums">
              {fmt(time)} / {fmt(duration)}
            </span>
          </div>
        </>
      )}
    </div>
  )
}
