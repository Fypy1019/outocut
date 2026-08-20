import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('outocut', {
  engineConnection: () => ipcRenderer.invoke('engine:connection'),
  engineLogs: () => ipcRenderer.invoke('engine:logs'),
  clearEngineLogs: () => ipcRenderer.invoke('engine:clearLogs'),
  selectDirectory: () => ipcRenderer.invoke('dialog:selectDirectory'),
  selectFile: (filters?: Electron.FileFilter[]) => ipcRenderer.invoke('dialog:selectFile', filters),
  selectFiles: (filters?: Electron.FileFilter[]) => ipcRenderer.invoke('dialog:selectFiles', filters),
  listImageFiles: (directory: string) => ipcRenderer.invoke('folder:listImageFiles', directory),
  listAudioFiles: (directory: string) => ipcRenderer.invoke('folder:listAudioFiles', directory),
  previewBatchRename: (request: { directory: string; templateName: string; rule: string }) => ipcRenderer.invoke('folder:previewBatchRename', request),
  executeBatchRename: (request: { directory: string; templateName: string; rule: string }) => ipcRenderer.invoke('folder:executeBatchRename', request),
  openPath: (path: string) => ipcRenderer.invoke('shell:openPath', path),
  pathToFileUrl: (path: string) => ipcRenderer.invoke('path:toFileUrl', path),
  changeWorkspaceRoot: () => ipcRenderer.invoke('workspace:changeRoot'),
  readFontBytes: (path: string) => ipcRenderer.invoke('font:readBytes', path),
})
