import { useState } from 'react'
import { UploadZone } from './components/UploadZone'
import { FileList } from './components/FileList'
import { ProgressView } from './components/ProgressView'
import { ResultsTable } from './components/ResultsTable'
import { FilterBar } from './components/FilterBar'
import { AnalysisDetail } from './components/AnalysisDetail'
import { FtpFilesPage } from './components/FtpFilesPage'
import { useUpload } from './hooks/useUpload'
import { useResults } from './hooks/useResults'
import type { AppState, ProcessingFile } from './types'

function App() {
  const [appState, setAppState] = useState<AppState>('empty')
  const [processingFiles, setProcessingFiles] = useState<ProcessingFile[]>([])
  const [selectedResultId, setSelectedResultId] = useState<string | null>(null)

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
    applyFilters, resetFilters, goToPage, setPageLimit,
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
      <header className="bg-white border-b border-gray-200 px-6 py-4 shadow-sm">
        <div className="max-w-5xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-2xl">📞</span>
            <h1 className="text-xl font-bold text-gray-800">Call Analytics</h1>
          </div>
          <nav className="flex items-center gap-3">
            <button
              onClick={() => setAppState('results')}
              className={`text-sm px-3 py-1.5 rounded-lg transition-colors ${
                appState === 'results'
                  ? 'bg-blue-100 text-blue-700 font-medium'
                  : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
              }`}
            >
              Результаты
            </button>
            <button
              onClick={() => setAppState('ftp_files')}
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
              onAddMore={() => setAppState('results')}
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
            />
            </div>
          )}

          {/* Detail state */}
          {appState === 'results' && selectedResultId && (
            <AnalysisDetail
              fileId={selectedResultId}
              onBack={() => setSelectedResultId(null)}
            />
          )}

          {/* FTP Files state */}
          {appState === 'ftp_files' && <FtpFilesPage />}

        </div>
      </main>
    </div>
  )
}

export default App
