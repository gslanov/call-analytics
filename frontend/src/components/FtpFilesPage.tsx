import { useState, useEffect, useCallback, Fragment } from 'react'
import { AudioPlayer } from './AudioPlayer'
import { Pagination } from './Pagination'
import { fetchFtpFiles, ftpStreamUrl, ftpDownloadUrl, sendFtpToWhisper } from '../lib/api'
import type { FtpFile, FtpFilesPage as FtpFilesPageData, FtpFilters } from '../lib/api'

const MOCK_FILES: FtpFile[] = [
  { id: 'ftp-1', filename: 'call_001_ivanov.mp3', size: 2500000, date: '2026-02-26T14:30:00Z', duration_sec: 125, callerphone: '+79161234567', operatorphone: '+74951234567', order_id: 'ORD-1001', lead_name: 'Иванов' },
  { id: 'ftp-2', filename: 'call_002_petrov.mp3', size: 1800000, date: '2026-02-26T13:15:00Z', duration_sec: 87, callerphone: '+79035559988', operatorphone: '+74951234568' },
  { id: 'ftp-3', filename: 'call_003_sidorov.wav', size: 4200000, date: '2026-02-25T17:45:00Z', duration_sec: 210, callerphone: '+79261112233', operatorphone: '+74951234569', order_id: 'ORD-1002' },
  { id: 'ftp-4', filename: 'call_004_ivanova.mp3', size: 3100000, date: '2026-02-25T11:00:00Z', duration_sec: 155, callerphone: '+79099876543' },
  { id: 'ftp-5', filename: 'call_005_novikov.mp3', size: 980000, date: '2026-02-24T09:30:00Z', duration_sec: 48 },
  { id: 'ftp-6', filename: 'mango_call_20260224_150022.mp3', size: 5400000, date: '2026-02-24T15:00:00Z', duration_sec: 270, callerphone: '+79161111111', operatorphone: '+74951234567', order_id: 'ORD-1003', lead_name: 'Сидоров' },
  { id: 'ftp-7', filename: 'mango_call_20260223_090100.mp3', size: 2100000, date: '2026-02-23T09:01:00Z', duration_sec: 103, callerphone: '+79052223344' },
  { id: 'ftp-8', filename: 'call_008_kuzmin.mp3', size: 1650000, date: '2026-02-22T16:20:00Z', duration_sec: 82, callerphone: '+79167778899', operatorphone: '+74951234568', order_id: 'ORD-1004' },
]

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} КБ`
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`
}

function formatDuration(secs: number | null): string {
  if (secs == null) return '—'
  const m = Math.floor(secs / 60)
  const s = Math.round(secs % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

export function FtpFilesPage() {
  // Filters
  const [search, setSearch] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [durationMin, setDurationMin] = useState('')
  const [durationMax, setDurationMax] = useState('')
  const [callerphone, setCallerphone] = useState('')
  const [operatorphone, setOperatorphone] = useState('')
  const [orderFilter, setOrderFilter] = useState('')

  // Table state
  const [files, setFiles] = useState<FtpFile[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [limit, setLimit] = useState(20)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [useMock, setUseMock] = useState(false)

  // Selection & playback
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [playingId, setPlayingId] = useState<string | null>(null)

  // Whisper status
  const [sendingToWhisper, setSendingToWhisper] = useState(false)
  const [whisperMsg, setWhisperMsg] = useState<string | null>(null)

  const load = useCallback(async (
    q: string, df: string, dt: string, dmin: string, dmax: string,
    caller: string, operator: string, order: string,
    p: number, lim: number
  ) => {
    setIsLoading(true)
    setError(null)
    try {
      const filters: FtpFilters = {}
      if (q) filters.q = q
      if (df) filters.date_from = df
      if (dt) filters.date_to = dt
      if (dmin) filters.duration_min = Number(dmin)
      if (dmax) filters.duration_max = Number(dmax)
      if (caller) filters.callerphone = caller
      if (operator) filters.operatorphone = operator
      if (order) filters.order_id = order
      const data: FtpFilesPageData = await fetchFtpFiles(filters, p, lim)
      setFiles(data.items)
      setTotal(data.total)
      setUseMock(false)
    } catch {
      setFiles(MOCK_FILES)
      setTotal(MOCK_FILES.length)
      setUseMock(true)
    } finally {
      setIsLoading(false)
    }
  }, [])

  // Initial load
  useEffect(() => {
    load('', '', '', '', '', '', '', '', 1, 20)
  }, [load])

  // Debounced search
  useEffect(() => {
    const t = setTimeout(() => {
      setPage(1)
      load(search, dateFrom, dateTo, durationMin, durationMax, callerphone, operatorphone, orderFilter, 1, limit)
    }, 300)
    return () => clearTimeout(t)
  }, [search]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleApplyFilters = () => {
    setPage(1)
    load(search, dateFrom, dateTo, durationMin, durationMax, callerphone, operatorphone, orderFilter, 1, limit)
  }

  const handleResetFilters = () => {
    setSearch('')
    setDateFrom('')
    setDateTo('')
    setDurationMin('')
    setDurationMax('')
    setCallerphone('')
    setOperatorphone('')
    setOrderFilter('')
    setPage(1)
    load('', '', '', '', '', '', '', '', 1, limit)
  }

  const handlePageChange = (p: number) => {
    setPage(p)
    load(search, dateFrom, dateTo, durationMin, durationMax, callerphone, operatorphone, orderFilter, p, limit)
  }

  const handleLimitChange = (l: number) => {
    setLimit(l)
    setPage(1)
    load(search, dateFrom, dateTo, durationMin, durationMax, callerphone, operatorphone, orderFilter, 1, l)
  }

  // Selection
  const allSelected = files.length > 0 && files.every((f) => selected.has(f.id))
  const toggleAll = () => {
    const next = new Set(selected)
    if (allSelected) {
      files.forEach((f) => next.delete(f.id))
    } else {
      files.forEach((f) => next.add(f.id))
    }
    setSelected(next)
  }
  const toggleOne = (id: string) => {
    const next = new Set(selected)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setSelected(next)
  }

  const togglePlay = (id: string) => {
    setPlayingId((prev) => (prev === id ? null : id))
  }

  const handleDownloadFile = (filename: string) => {
    window.open(ftpDownloadUrl(filename), '_blank')
  }

  const handleBulkDownload = () => {
    files.filter((f) => selected.has(f.id)).forEach((f) => {
      window.open(ftpDownloadUrl(f.filename), '_blank')
    })
  }

  const handleSendToWhisper = async (filenames: string[]) => {
    setSendingToWhisper(true)
    setWhisperMsg(null)
    try {
      await sendFtpToWhisper(filenames)
      setWhisperMsg(`${filenames.length} файл(ов) поставлено в очередь на распознавание`)
      setSelected(new Set())
    } catch (e) {
      setWhisperMsg(`Ошибка: ${e instanceof Error ? e.message : 'неизвестная ошибка'}`)
    } finally {
      setSendingToWhisper(false)
    }
  }

  const thClass = 'px-4 py-2 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide whitespace-nowrap'
  const tdClass = 'px-4 py-3 text-sm text-gray-600 whitespace-nowrap'

  return (
    <div className="flex flex-col gap-4">
      {/* Filter panel */}
      <div className="bg-white rounded-2xl shadow-sm border border-gray-200 px-6 py-4">
        <h2 className="text-base font-semibold text-gray-800 mb-3">Фильтры</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <div className="lg:col-span-2">
            <label className="text-xs text-gray-500 mb-1 block">Поиск по имени</label>
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="call_001_ivanov.mp3..."
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                         focus:outline-none focus:ring-2 focus:ring-blue-400"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 mb-1 block">Дата с</label>
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                         focus:outline-none focus:ring-2 focus:ring-blue-400"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 mb-1 block">Дата по</label>
            <input
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                         focus:outline-none focus:ring-2 focus:ring-blue-400"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 mb-1 block">Телефон звонящего</label>
            <input
              type="text"
              value={callerphone}
              onChange={(e) => setCallerphone(e.target.value)}
              placeholder="+79161234567"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                         focus:outline-none focus:ring-2 focus:ring-blue-400"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 mb-1 block">Номер оператора</label>
            <input
              type="text"
              value={operatorphone}
              onChange={(e) => setOperatorphone(e.target.value)}
              placeholder="+74951234567"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                         focus:outline-none focus:ring-2 focus:ring-blue-400"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 mb-1 block">Номер заказа</label>
            <input
              type="text"
              value={orderFilter}
              onChange={(e) => setOrderFilter(e.target.value)}
              placeholder="ORD-1001"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                         focus:outline-none focus:ring-2 focus:ring-blue-400"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 mb-1 block">Мин. длит. (сек)</label>
            <input
              type="number"
              value={durationMin}
              onChange={(e) => setDurationMin(e.target.value)}
              placeholder="0"
              min="0"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                         focus:outline-none focus:ring-2 focus:ring-blue-400"
            />
          </div>
          <div className="flex items-end gap-2">
            <div className="flex-1">
              <label className="text-xs text-gray-500 mb-1 block">Макс. длит. (сек)</label>
              <input
                type="number"
                value={durationMax}
                onChange={(e) => setDurationMax(e.target.value)}
                placeholder="3600"
                min="0"
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                           focus:outline-none focus:ring-2 focus:ring-blue-400"
              />
            </div>
          </div>
          <div className="sm:col-span-2 flex items-end gap-2">
            <button
              onClick={handleApplyFilters}
              className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg
                         hover:bg-blue-700 transition-colors"
            >
              Применить
            </button>
            <button
              onClick={handleResetFilters}
              className="px-4 py-2 bg-gray-100 text-gray-600 text-sm font-medium rounded-lg
                         hover:bg-gray-200 transition-colors"
            >
              Сброс
            </button>
          </div>
        </div>
      </div>

      {/* Whisper notification */}
      {whisperMsg && (
        <div className={`rounded-xl px-4 py-3 text-sm flex items-center justify-between ${
          whisperMsg.startsWith('Ошибка')
            ? 'bg-red-50 text-red-700 border border-red-200'
            : 'bg-green-50 text-green-700 border border-green-200'
        }`}>
          <span>{whisperMsg}</span>
          <button
            onClick={() => setWhisperMsg(null)}
            className="ml-3 opacity-60 hover:opacity-100 transition-opacity"
          >
            ✕
          </button>
        </div>
      )}

      {/* Table card */}
      <div className="bg-white rounded-2xl shadow-sm border border-gray-200 overflow-hidden">
        {/* Card header */}
        <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between gap-3 flex-wrap">
          <div>
            <h2 className="text-lg font-semibold text-gray-800">FTP Файлы</h2>
            <p className="text-sm text-gray-400 mt-0.5">
              {total} {total === 1 ? 'файл' : 'файлов'}
              {useMock && <span className="ml-2 text-yellow-500 text-xs">(demo-данные)</span>}
            </p>
          </div>
          {selected.size > 0 && (
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm text-gray-500">{selected.size} выбрано</span>
              <button
                onClick={handleBulkDownload}
                className="px-3 py-1.5 text-sm font-medium bg-gray-100 text-gray-700
                           rounded-lg hover:bg-gray-200 transition-colors"
              >
                Скачать
              </button>
              <button
                onClick={() =>
                  handleSendToWhisper(
                    files.filter((f) => selected.has(f.id)).map((f) => f.filename)
                  )
                }
                disabled={sendingToWhisper}
                className="px-3 py-1.5 text-sm font-medium bg-blue-600 text-white
                           rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
              >
                {sendingToWhisper ? 'Отправка...' : 'В Whisper'}
              </button>
            </div>
          )}
        </div>

        {/* Table body */}
        <div className="overflow-x-auto">
          {isLoading ? (
            <div className="flex justify-center items-center py-16">
              <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : error ? (
            <div className="py-12 text-center text-red-500 text-sm">{error}</div>
          ) : files.length === 0 ? (
            <div className="py-12 text-center text-gray-400 text-sm">Нет файлов</div>
          ) : (
            <table className="w-full">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-4 py-2 w-8">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleAll}
                      className="rounded border-gray-300"
                    />
                  </th>
                  <th className={thClass}>Файл</th>
                  <th className={thClass}>Размер</th>
                  <th className={thClass}>Дата</th>
                  <th className={thClass}>Длит.</th>
                  <th className={thClass}>Телефон</th>
                  <th className={thClass}>Оператор</th>
                  <th className={thClass}>Заказ</th>
                  <th className="px-4 py-2 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">
                    Действия
                  </th>
                </tr>
              </thead>
              <tbody>
                {files.map((file) => (
                  <Fragment key={file.id}>
                    <tr
                      className={`border-b border-gray-100 hover:bg-gray-50 transition-colors ${
                        selected.has(file.id) ? 'bg-blue-50 hover:bg-blue-50' : ''
                      }`}
                    >
                      <td className="px-4 py-3">
                        <input
                          type="checkbox"
                          checked={selected.has(file.id)}
                          onChange={() => toggleOne(file.id)}
                          onClick={(e) => e.stopPropagation()}
                          className="rounded border-gray-300"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-sm font-medium text-gray-800 font-mono break-all">
                          {file.filename}
                        </span>
                      </td>
                      <td className={tdClass}>{formatSize(file.size)}</td>
                      <td className={tdClass}>{formatDate(file.date)}</td>
                      <td className={tdClass}>{formatDuration(file.duration_sec)}</td>
                      <td className={tdClass}>
                        {file.callerphone ? (
                          <span className="font-mono">{file.callerphone}</span>
                        ) : '—'}
                        {file.lead_name && (
                          <span className="ml-1 text-xs text-gray-400">({file.lead_name})</span>
                        )}
                      </td>
                      <td className={tdClass}>
                        {file.operatorphone ? (
                          <span className="font-mono">{file.operatorphone}</span>
                        ) : '—'}
                      </td>
                      <td className={tdClass}>
                        {file.order_id ? (
                          <span className="text-blue-600 font-medium">{file.order_id}</span>
                        ) : '—'}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1 flex-wrap">
                          <button
                            onClick={() => togglePlay(file.id)}
                            className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
                              playingId === file.id
                                ? 'bg-blue-600 text-white'
                                : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                            }`}
                          >
                            {playingId === file.id ? '⏹ Стоп' : '▶ Играть'}
                          </button>
                          <button
                            onClick={() => handleDownloadFile(file.filename)}
                            className="px-2 py-1 rounded text-xs font-medium bg-gray-100
                                       text-gray-600 hover:bg-gray-200 transition-colors"
                          >
                            Скачать
                          </button>
                          <button
                            onClick={() => handleSendToWhisper([file.filename])}
                            disabled={sendingToWhisper}
                            className="px-2 py-1 rounded text-xs font-medium bg-blue-50
                                       text-blue-700 hover:bg-blue-100 disabled:opacity-50 transition-colors"
                          >
                            В Whisper
                          </button>
                        </div>
                      </td>
                    </tr>
                    {playingId === file.id && (
                      <tr>
                        <td colSpan={9} className="px-6 py-3 bg-gray-900">
                          <AudioPlayer src={ftpStreamUrl(file.filename)} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Pagination */}
        {!isLoading && !error && total > 0 && (
          <div className="border-t border-gray-100 px-4 py-2">
            <Pagination
              page={page}
              total={total}
              limit={limit}
              onPageChange={handlePageChange}
              onLimitChange={handleLimitChange}
            />
          </div>
        )}
      </div>
    </div>
  )
}
