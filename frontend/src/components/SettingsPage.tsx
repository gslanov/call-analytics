import { useState, useEffect } from 'react'

const API = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'

interface FtpState {
  host: string
  user: string
  password: string
  has_password: boolean
}

export function SettingsPage() {
  const [ftp, setFtp] = useState<FtpState>({ host: '', user: '', password: '', has_password: false })
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [msg, setMsg] = useState<{ type: 'ok' | 'error'; text: string } | null>(null)

  useEffect(() => {
    fetch(`${API}/settings/mango-ftp`)
      .then((r) => r.json())
      .then((data) => setFtp((prev) => ({ ...prev, host: data.host, user: data.user, has_password: data.has_password })))
      .catch(() => {})
  }, [])

  const handleSave = async () => {
    setSaving(true)
    setMsg(null)
    try {
      const res = await fetch(`${API}/settings/mango-ftp`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host: ftp.host, user: ftp.user, password: ftp.password }),
      })
      if (res.ok) {
        setMsg({ type: 'ok', text: 'Настройки сохранены' })
        setFtp((prev) => ({ ...prev, password: '', has_password: true }))
      } else {
        setMsg({ type: 'error', text: 'Ошибка сохранения' })
      }
    } catch {
      setMsg({ type: 'error', text: 'Ошибка сети' })
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setMsg(null)
    try {
      const res = await fetch(`${API}/settings/mango-ftp/test`, { method: 'POST' })
      const data = await res.json()
      setMsg({ type: data.status === 'ok' ? 'ok' : 'error', text: data.message })
    } catch {
      setMsg({ type: 'error', text: 'Ошибка сети' })
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <h2 className="text-xl font-bold text-gray-800">Настройки</h2>

      <div className="bg-white rounded-2xl border border-gray-200 px-6 py-5 shadow-sm">
        <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-4">
          Манго FTP — автоматическая загрузка записей
        </h3>

        <div className="flex flex-col gap-4 max-w-md">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">FTP Хост</label>
            <input
              type="text"
              value={ftp.host}
              onChange={(e) => setFtp((prev) => ({ ...prev, host: e.target.value }))}
              placeholder="ftp.mango-office.ru"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Логин</label>
            <input
              type="text"
              value={ftp.user}
              onChange={(e) => setFtp((prev) => ({ ...prev, user: e.target.value }))}
              placeholder="user@company.ru"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Пароль
              {ftp.has_password && !ftp.password && (
                <span className="ml-2 text-xs text-green-600 font-normal">сохранён</span>
              )}
            </label>
            <input
              type="password"
              value={ftp.password}
              onChange={(e) => setFtp((prev) => ({ ...prev, password: e.target.value }))}
              placeholder={ftp.has_password ? '••••••••' : 'Введите пароль'}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
            />
          </div>

          <div className="flex gap-3 pt-2">
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {saving ? 'Сохранение...' : 'Сохранить'}
            </button>
            <button
              onClick={handleTest}
              disabled={testing}
              className="px-4 py-2 bg-gray-100 text-gray-700 text-sm font-medium rounded-lg hover:bg-gray-200 disabled:opacity-50 transition-colors"
            >
              {testing ? 'Проверка...' : 'Проверить подключение'}
            </button>
          </div>

          {msg && (
            <div className={`text-sm px-3 py-2 rounded-lg ${
              msg.type === 'ok' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
            }`}>
              {msg.text}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
