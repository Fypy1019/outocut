/// <reference types="vite/client" />

interface EngineConnection {
  baseUrl: string
  token: string
  ready: boolean
}

interface FontEntry {
  name: string
  path: string
}

interface BatchRenameItem {
  original_name: string
  new_name: string
}

interface BatchRenamePlan {
  directory: string
  items: BatchRenameItem[]
}

interface BatchRenameResult {
  renamed: number
  items: BatchRenameItem[]
}

type QianchuanRunState = 'idle' | 'waiting_for_user' | 'ready' | 'uploading' | 'paused' | 'login_required' | 'page_changed' | 'completed' | 'stopped' | 'error'
type QianchuanFileState = 'pending' | 'uploading' | 'succeeded' | 'failed' | 'skipped'

interface QianchuanUploadFile {
  id: string
  path: string
  name: string
  size: number
  state: QianchuanFileState
  error: string
  entranceId?: string
  entranceColor?: string
  queueId?: string
}

type QianchuanEntranceState = 'ready' | 'uploading' | 'paused' | 'completed' | 'stopped' | 'unavailable' | 'error'

interface QianchuanUploadEntrance {
  id: string
  name: string
  url: string
  state: QianchuanEntranceState
  currentBatch: number
  fileCount: number
  message: string
  sourceDirectory: string
  sourceDirectories: string[]
  totalFiles: number
  taskRunning: boolean
  color: string
}

interface QianchuanUploadSnapshot {
  state: QianchuanRunState
  taskRunning: boolean
  message: string
  browserOpen: boolean
  currentBatch: number
  totalBatches: number
  config: { batchSize: number; batchTimeoutSeconds: number; maxConcurrentEntrances: number; sharedEntrances: boolean }
  files: QianchuanUploadFile[]
  entrances: QianchuanUploadEntrance[]
  sharedSourceDirectories: string[]
  updatedAt: string
}

interface Window {
  outocut?: {
    engineConnection(): Promise<EngineConnection>
    engineLogs(): Promise<string[]>
    clearEngineLogs(): Promise<boolean>
    selectDirectory(): Promise<string | null>
    selectDirectories(): Promise<string[]>
    selectFile(filters?: Array<{ name: string; extensions: string[] }>): Promise<string | null>
    selectFiles(filters?: Array<{ name: string; extensions: string[] }>): Promise<string[]>
    saveGeneratedImage(request: { source: string; suggestedName: string; outputDirectory?: string; relativeDirectory?: string }): Promise<{ saved: boolean; path?: string }>
    competitorOpen(url: string): Promise<CompetitorBrowserStatus>
    competitorStatus(): Promise<CompetitorBrowserStatus>
    competitorCollect(url?: string): Promise<CompetitorCollection>
    listImageFiles(directory: string): Promise<string[]>
    listAudioFiles(directory: string): Promise<string[]>
    previewBatchRename(request: { directory: string; templateName: string; rule: string }): Promise<BatchRenamePlan>
    executeBatchRename(request: { directory: string; templateName: string; rule: string }): Promise<BatchRenameResult>
    openPath(path: string): Promise<string>
    pathToFileUrl(path: string): Promise<string>
    changeWorkspaceRoot(): Promise<(EngineConnection & { dataRoot: string }) | null>
    readFontBytes(path: string): Promise<ArrayBuffer | null>
    qianchuanStatus(): Promise<QianchuanUploadSnapshot>
    qianchuanScanDirectory(directory: string): Promise<QianchuanUploadSnapshot>
    qianchuanConfigure(config: { batchSize?: number; batchTimeoutSeconds?: number; maxConcurrentEntrances?: number; sharedEntrances?: boolean }): Promise<QianchuanUploadSnapshot>
    qianchuanOpenChrome(): Promise<QianchuanUploadSnapshot>
    qianchuanDiscoverEntrances(): Promise<QianchuanUploadSnapshot>
    qianchuanAssignEntranceDirectory(entranceId: string, directory: string): Promise<QianchuanUploadSnapshot>
    qianchuanAssignEntranceDirectories(entranceId: string, directories: string[]): Promise<QianchuanUploadSnapshot>
    qianchuanAssignSharedDirectories(directories: string[]): Promise<QianchuanUploadSnapshot>
    qianchuanRenameEntrance(entranceId: string, name: string): Promise<QianchuanUploadSnapshot>
    qianchuanFocusEntrance(entranceId: string): Promise<QianchuanUploadSnapshot>
    qianchuanStart(): Promise<QianchuanUploadSnapshot>
    qianchuanStartEntrance(entranceId: string): Promise<QianchuanUploadSnapshot>
    qianchuanPause(): Promise<QianchuanUploadSnapshot>
    qianchuanResume(): Promise<QianchuanUploadSnapshot>
    qianchuanStop(): Promise<QianchuanUploadSnapshot>
    qianchuanRetryFailed(): Promise<QianchuanUploadSnapshot>
    qianchuanConfirmSucceeded(fileIds: string[]): Promise<QianchuanUploadSnapshot>
    onQianchuanStatus(listener: (snapshot: QianchuanUploadSnapshot) => void): () => void
  }
}

interface CompetitorBrowserStatus {
  browserOpen: boolean
  pageUrl: string
  title: string
}

interface CompetitorImage {
  id: string
  url: string
  dataUrl: string
  width: number
  height: number
  kind: 'main' | 'sku' | 'detail'
  alt: string
}

interface CompetitorCollection {
  pageUrl: string
  title: string
  images: CompetitorImage[]
}
