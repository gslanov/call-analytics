import { useState, useEffect } from 'react'
import { UploadZone } from './components/UploadZone'
import { FileList } from './components/FileList'
import { ProgressView } from './components/ProgressView'
import { ResultsTable } from './components/ResultsTable'
import { FilterBar } from './components/FilterBar'
import { AnalysisDetail } from './components/AnalysisDetail'
import { FtpFilesPage } from './components/FtpFilesPage'
import { ReportsPage } from './components/ReportsPage'
import { SettingsPage } from './components/SettingsPage'
import { useUpload } from './hooks/useUpload'
import { useResults } from './hooks/useResults'
import { deleteResult, rejectAnalysis } from './lib/api'
import type { AppState, ProcessingFile } from './types'

function parseHash(): { page: AppState; detailId: string | null } {
  const hash = window.location.hash.replace('#', '')
  if (hash.startsWith('detail/')) return { page: 'results', detailId: hash.replace('detail/', '') }
  const valid: AppState[] = ['results', 'reports', 'ftp_files', 'settings', 'empty']
  if (valid.includes(hash as AppState)) return { page: hash as AppState, detailId: null }
  return { page: 'results', detailId: null }
}

function App() {
  const initial = parseHash()
  const [appState, setAppState] = useState<AppState>(initial.page)
  const [prevState, setPrevState] = useState<AppState | null>(null)
  const [processingFiles, setProcessingFiles] = useState<ProcessingFile[]>([])
  const [selectedResultId, setSelectedResultId] = useState<string | null>(initial.detailId)

  const navigate = (to: AppState) => {
    setPrevState(appState)
    setAppState(to)
    if (to === 'results') refresh()
  }

  const goBack = () => {
    if (selectedResultId) {
      setSelectedResultId(null)
    } else if (prevState) {
      setAppState(prevState)
      setPrevState(null)
    }
  }

  const {
    files,
    operatorName,
    uploadProgress,
    isUploading,
    error,
    addFiles,
    removeFile,
    setOperatorName,
    startUpload,
    reset,
  } = useUpload()

  // Sync state → URL hash
  useEffect(() => {
    if (selectedResultId) {
      window.location.hash = `detail/${selectedResultId}`
    } else {
      window.location.hash = appState
    }
  }, [appState, selectedResultId])

  // Предупреждение при попытке закрыть вкладку во время загрузки
  useEffect(() => {
    if (!isUploading) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [isUploading])

  const handleFilesSelected = (newFiles: File[]) => {
    addFiles(newFiles)
    setAppState('files_picked')
  }

  const handleAnalyze = async () => {
    setAppState('uploading')
    const result = await startUpload()
    if (result) {
      // Map file_ids back to original file names (order preserved from request)
      const pf: ProcessingFile[] = result.file_ids.map((id, i) => ({
        file_id: id,
        file_name: files[i]?.file.name ?? id,
      }))
      setProcessingFiles(pf)
      setAppState('processing')
    } else {
      setAppState('files_picked')
    }
  }

  const {
    results, total, page, limit, filters,
    isLoading: resultsLoading, error: resultsError, useMock,
    applyFilters, resetFilters, goToPage, setPageLimit, refresh,
  } = useResults()

  const handleReset = () => {
    reset()
    setProcessingFiles([])
    setSelectedResultId(null)
    setAppState('empty')
  }

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-6 py-4 shadow-sm sticky top-0 z-50">
        <div className="max-w-5xl mx-auto flex items-center justify-between">
          <button
            onClick={handleReset}
            className="flex items-center gap-2 hover:opacity-70 transition-opacity"
          >
            <span className="text-2xl">📞</span>
            <h1 className="text-xl font-bold text-gray-800">Анализ звонков</h1>
          </button>
          <nav className="flex items-center gap-3">
            {(selectedResultId || prevState) && (
              <button
                onClick={goBack}
                className="text-sm px-3 py-1.5 rounded-lg transition-colors bg-gray-100 text-gray-700 hover:bg-gray-200 font-medium"
              >
                ← Назад
              </button>
            )}
            <button
              onClick={() => navigate('results')}
              className={`text-sm px-3 py-1.5 rounded-lg transition-colors ${
                appState === 'results'
                  ? 'bg-blue-100 text-blue-700 font-medium'
                  : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
              }`}
            >
              Результаты
            </button>
            <button
              onClick={() => navigate('reports')}
              className={`text-sm px-3 py-1.5 rounded-lg transition-colors ${
                appState === 'reports'
                  ? 'bg-blue-100 text-blue-700 font-medium'
                  : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
              }`}
            >
              Отчёты
            </button>
            <button
              onClick={() => navigate('ftp_files')}
              className={`text-sm px-3 py-1.5 rounded-lg transition-colors ${
                appState === 'ftp_files'
                  ? 'bg-blue-100 text-blue-700 font-medium'
                  : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
              }`}
            >
              FTP Файлы
            </button>
            <button
              onClick={handleReset}
              className={`text-sm px-3 py-1.5 rounded-lg transition-colors ${
                appState === 'empty'
                  ? 'bg-blue-100 text-blue-700 font-medium'
                  : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
              }`}
            >
              + Загрузить
            </button>
            <button
              onClick={() => navigate('settings')}
              className={`text-sm px-3 py-1.5 rounded-lg transition-colors ${
                appState === 'settings'
                  ? 'bg-blue-100 text-blue-700 font-medium'
                  : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
              }`}
            >
              Настройки
            </button>
          </nav>
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 flex items-start justify-center px-4 py-10">
        <div className="w-full max-w-5xl">

          {/* Empty state */}
          {appState === 'empty' && (
            <div className="flex flex-col gap-6">
              <div className="text-center">
                <h2 className="text-2xl font-bold text-gray-800 mb-1">Анализ звонков операторов</h2>
                <p className="text-gray-500">Загрузите аудиофайлы для автоматической оценки качества</p>
              </div>
              <UploadZone onFilesSelected={handleFilesSelected} />
            </div>
          )}

          {/* Files picked / uploading */}
          {(appState === 'files_picked' || appState === 'uploading') && (
            <div className="flex flex-col gap-6">
              <UploadZone
                onFilesSelected={handleFilesSelected}
                disabled={isUploading}
              />
              <FileList
                files={files}
                operatorName={operatorName}
                onRemove={removeFile}
                onOperatorChange={setOperatorName}
                onAnalyze={handleAnalyze}
                isUploading={isUploading}
                uploadProgress={uploadProgress}
                error={error}
              />
            </div>
          )}

          {/* Processing state */}
          {appState === 'processing' && processingFiles.length > 0 && (
            <ProgressView
              files={processingFiles}
              onAddMore={handleReset}
              onGoToResults={() => {
                refresh()
                navigate('results')
              }}
            />
          )}

          {/* Results state */}
          {appState === 'results' && !selectedResultId && (
            <div className="flex flex-col gap-4">
              <FilterBar
                activeFilters={filters}
                onApply={applyFilters}
                onReset={resetFilters}
              />
              <div className="flex justify-end -mt-2">
                <button
                  onClick={refresh}
                  disabled={resultsLoading}
                  className={`flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-100 hover:border-gray-400 hover:shadow-sm active:scale-95 transition-all duration-150 font-medium ${
                    resultsLoading ? 'opacity-50 cursor-not-allowed hover:bg-white hover:border-gray-300 hover:shadow-none active:scale-100' : ''
                  }`}
                  title="Обновить список"
                >
                  <svg className={`w-4 h-4 ${resultsLoading ? 'animate-spin' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  Обновить
                </button>
              </div>
            <ResultsTable
              results={results}
              total={total}
              page={page}
              limit={limit}
              filters={filters}
              isLoading={resultsLoading}
              error={resultsError}
              useMock={useMock}
              onFiltersChange={applyFilters}
              onPageChange={goToPage}
              onLimitChange={setPageLimit}
              onRowDetail={(id) => setSelectedResultId(id)}
              onBulkDelete={async (ids) => {
                // Параллельно, но с фиксацией ошибок — не ломаемся на первом падении.
                const results = await Promise.allSettled(ids.map((id) => deleteResult(id)))
                const failed = results.filter((r) => r.status === 'rejected')
                refresh()
                if (failed.length > 0) {
                  throw new Error(`Не удалось удалить ${failed.length} из ${ids.length}`)
                }
              }}
            />
            </div>
          )}

          {/* Detail state */}
          {appState === 'results' && selectedResultId && (
            <AnalysisDetail
              fileId={selectedResultId}
              onBack={() => setSelectedResultId(null)}
              onReject={async (id, reason) => {
                try {
                  await rejectAnalysis(id, reason)
                  refresh()
                } catch (e) {
                  alert('Ошибка: ' + (e as Error).message)
                }
              }}
              onDelete={async (id) => {
                try {
                  await deleteResult(id)
                  setSelectedResultId(null)
                  refresh()
                } catch (e) {
                  alert('Ошибка удаления: ' + (e as Error).message)
                }
              }}
            />
          )}

          {/* Reports */}
          {appState === 'reports' && (
            <ReportsPage
              onOperatorClick={(name) => {
                applyFilters({ operator: name })
                navigate('results')
              }}
            />
          )}

          {/* FTP Files state */}
          {appState === 'ftp_files' && <FtpFilesPage />}

          {/* Settings */}
          {appState === 'settings' && <SettingsPage />}

        </div>
      </main>

      {/* Floating back button — always visible when there's somewhere to go back */}
      {(selectedResultId || prevState) && (
        <button
          onClick={goBack}
          className="fixed bottom-6 right-6 bg-white border border-gray-300 shadow-lg rounded-full px-5 py-3 text-sm font-medium text-gray-700 hover:bg-gray-50 hover:shadow-xl transition-all z-50"
        >
          ← Назад
        </button>
      )}
    </div>
  )
}

export default App
