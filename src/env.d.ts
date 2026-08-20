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

interface Window {
  outocut?: {
    engineConnection(): Promise<EngineConnection>
    engineLogs(): Promise<string[]>
    clearEngineLogs(): Promise<boolean>
    selectDirectory(): Promise<string | null>
    selectFile(filters?: Array<{ name: string; extensions: string[] }>): Promise<string | null>
    selectFiles(filters?: Array<{ name: string; extensions: string[] }>): Promise<string[]>
    listImageFiles(directory: string): Promise<string[]>
    listAudioFiles(directory: string): Promise<string[]>
    previewBatchRename(request: { directory: string; templateName: string; rule: string }): Promise<BatchRenamePlan>
    executeBatchRename(request: { directory: string; templateName: string; rule: string }): Promise<BatchRenameResult>
    openPath(path: string): Promise<string>
    pathToFileUrl(path: string): Promise<string>
    changeWorkspaceRoot(): Promise<(EngineConnection & { dataRoot: string }) | null>
    readFontBytes(path: string): Promise<ArrayBuffer | null>
  }
}
