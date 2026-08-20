import { app, BrowserWindow, dialog, ipcMain, Menu, net, protocol, shell } from 'electron'
import { randomBytes } from 'node:crypto'
import { createServer } from 'node:net'
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { appendFileSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, renameSync, writeFileSync } from 'node:fs'
import { extname, join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

let mainWindow: BrowserWindow | null = null
let engine: ChildProcessWithoutNullStreams | null = null
let enginePort = 0
let engineToken = ''
let engineReady = false
let workspaceRoot = ''
const recentLogs: string[] = []
const hasSingleInstanceLock = app.requestSingleInstanceLock()

if (!hasSingleInstanceLock) app.quit()

app.on('second-instance', () => {
  if (!mainWindow) return
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
})

interface BatchRenameRequest {
  directory: string
  templateName: string
  rule: string
}

interface BatchRenameItem {
  original_name: string
  new_name: string
}

function renderBatchRenameName(rule: string, templateName: string, sequence: number, extension: string): string {
  const suffix = extension.replace(/^\./, '')
  return rule
    .replaceAll('{模板名称}', templateName)
    .replaceAll('{递增序号}', String(sequence))
    .replaceAll('.{后缀}', suffix ? `.${suffix}` : '')
    .replaceAll('.后缀', suffix ? `.${suffix}` : '')
    .replaceAll('{后缀}', suffix)
    .replaceAll('后缀', suffix)
}

function planBatchRename(request: BatchRenameRequest): { directory: string; items: BatchRenameItem[] } {
  const rawDirectory = request.directory.trim()
  const templateName = request.templateName.trim()
  const rule = request.rule.trim()
  if (!rawDirectory) throw new Error('请选择目标文件夹')
  const directory = resolve(rawDirectory)
  if (!existsSync(directory) || !lstatSync(directory).isDirectory()) {
    throw new Error('请选择有效的文件夹')
  }
  if (!templateName) throw new Error('请输入文件模板名称')
  if (!rule.includes('{模板名称}') || !rule.includes('{递增序号}') || !rule.includes('后缀')) {
    throw new Error('组合规则必须包含 {模板名称}、{递增序号} 和 后缀')
  }
  const entries = readdirSync(directory, { withFileTypes: true })
    .filter((entry) => entry.isFile())
    .sort((left, right) => left.name.localeCompare(right.name, 'zh-CN', { numeric: true, sensitivity: 'base' }))
  const items = entries.map((entry, index) => ({
    original_name: entry.name,
    new_name: renderBatchRenameName(rule, templateName, index + 1, extname(entry.name)),
  }))
  const invalidName = items.find(({ new_name }) => (
    !new_name
    || /[<>:"/\\|?*\u0000-\u001F]/.test(new_name)
    || /[. ]$/.test(new_name)
    || /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?$/i.test(new_name)
  ))
  if (invalidName) throw new Error(`生成了无效文件名：${invalidName.new_name || '空文件名'}`)
  const normalizedNames = new Set<string>()
  const sourceNames = new Set(items.map((item) => item.original_name.toLocaleLowerCase('zh-CN')))
  for (const item of items) {
    const normalized = item.new_name.toLocaleLowerCase('zh-CN')
    if (normalizedNames.has(normalized)) throw new Error(`组合规则生成了重复文件名：${item.new_name}`)
    if (existsSync(join(directory, item.new_name)) && !sourceNames.has(normalized)) {
      throw new Error(`目标名称已被文件夹或其他项目占用：${item.new_name}`)
    }
    normalizedNames.add(normalized)
  }
  return { directory, items }
}

function executeBatchRename(request: BatchRenameRequest): { renamed: number; items: BatchRenameItem[] } {
  const plan = planBatchRename(request)
  if (!plan.items.length) return { renamed: 0, items: [] }
  const temporaryPrefix = `.__outocut_rename_${randomBytes(10).toString('hex')}_`
  const operations = plan.items.map((item, index) => ({
    ...item,
    originalPath: join(plan.directory, item.original_name),
    temporaryPath: join(plan.directory, `${temporaryPrefix}${index}${extname(item.original_name)}`),
    targetPath: join(plan.directory, item.new_name),
  }))
  const movedToTemporary: typeof operations = []
  try {
    for (const operation of operations) {
      renameSync(operation.originalPath, operation.temporaryPath)
      movedToTemporary.push(operation)
    }
  } catch (error) {
    for (const operation of movedToTemporary.reverse()) {
      try { renameSync(operation.temporaryPath, operation.originalPath) } catch { /* Best-effort rollback. */ }
    }
    throw new Error(`创建临时文件名失败，已回滚：${error instanceof Error ? error.message : String(error)}`)
  }
  const completed: typeof operations = []
  try {
    for (const operation of operations) {
      renameSync(operation.temporaryPath, operation.targetPath)
      completed.push(operation)
    }
  } catch (error) {
    for (const operation of completed) {
      try { renameSync(operation.targetPath, operation.temporaryPath) } catch { /* Best-effort rollback. */ }
    }
    for (const operation of operations) {
      try { renameSync(operation.temporaryPath, operation.originalPath) } catch { /* Best-effort rollback. */ }
    }
    throw new Error(`批量重命名失败，已尝试回滚：${error instanceof Error ? error.message : String(error)}`)
  }
  return { renamed: operations.length, items: plan.items }
}

protocol.registerSchemesAsPrivileged([
  {
    scheme: 'outocut-media',
    privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true },
  },
])

function appendLog(source: string, chunk: Buffer): void {
  const timestamp = new Date().toISOString()
  const lines = chunk.toString('utf8').split(/\r?\n/).filter(Boolean)
  for (const line of lines) {
    recentLogs.push(`[${source}] ${line}`)
  }
  if (recentLogs.length > 300) recentLogs.splice(0, recentLogs.length - 300)
  try {
    const logDir = join(app.getPath('userData'), 'logs')
    mkdirSync(logDir, { recursive: true })
    appendFileSync(
      join(logDir, 'desktop.log'),
      lines.map((line) => `${timestamp} [${source}] ${line}\n`).join(''),
      'utf8',
    )
  } catch {
    // Logging must never prevent the desktop shell from starting.
  }
}

async function reservePort(): Promise<number> {
  return new Promise((resolvePort, reject) => {
    const server = createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (!address || typeof address === 'string') {
        server.close()
        reject(new Error('无法分配本地端口'))
        return
      }
      const port = address.port
      server.close(() => resolvePort(port))
    })
  })
}

function enginePaths(): { command: string; args: string[]; cwd: string } {
  if (app.isPackaged) {
    const executable = join(process.resourcesPath, 'engine', 'outocut-engine.exe')
    return { command: executable, args: [], cwd: join(process.resourcesPath, 'engine') }
  }
  const command = process.env.OUTOCUT_PYTHON || 'python'
  return {
    command,
    args: ['-m', 'outocut_engine'],
    cwd: resolve(app.getAppPath(), 'engine'),
  }
}

async function waitForEngine(timeoutMs = 20_000): Promise<void> {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    try {
      const response = await fetch(`http://127.0.0.1:${enginePort}/health`, {
        headers: { Authorization: `Bearer ${engineToken}` },
      })
      if (response.ok) {
        engineReady = true
        return
      }
    } catch {
      // Engine may still be importing modules.
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 250))
  }
  throw new Error('本地视频引擎启动超时')
}

async function startEngine(): Promise<void> {
  if (engine) return
  enginePort = await reservePort()
  engineToken = randomBytes(32).toString('hex')
  const runtime = enginePaths()
  const dataRoot = workspaceRoot || join(app.getPath('userData'), 'workspace')
  engine = spawn(runtime.command, [...runtime.args, '--host', '127.0.0.1', '--port', String(enginePort)], {
    cwd: runtime.cwd,
    windowsHide: true,
    shell: false,
    env: {
      ...process.env,
      OUTOCUT_SESSION_TOKEN: engineToken,
      OUTOCUT_DATA_ROOT: dataRoot,
      OUTOCUT_FFMPEG: app.isPackaged ? join(process.resourcesPath, 'ffmpeg', 'ffmpeg.exe') : process.env.OUTOCUT_FFMPEG || 'ffmpeg',
      OUTOCUT_FFPROBE: app.isPackaged ? join(process.resourcesPath, 'ffmpeg', 'ffprobe.exe') : process.env.OUTOCUT_FFPROBE || 'ffprobe',
    },
  })
  engine.stdout.on('data', (chunk: Buffer) => appendLog('engine', chunk))
  engine.stderr.on('data', (chunk: Buffer) => appendLog('engine:error', chunk))
  engine.once('exit', (code) => {
    appendLog('electron', Buffer.from(`引擎进程退出（代码 ${code}）`))
    engine = null
    engineReady = false
  })
  await waitForEngine()
}

function bootstrapFile(): string {
  return join(app.getPath('userData'), 'bootstrap.json')
}

function loadWorkspaceRoot(): string {
  try {
    const parsed = JSON.parse(readFileSync(bootstrapFile(), 'utf8')) as { workspaceRoot?: string }
    if (parsed.workspaceRoot) return resolve(parsed.workspaceRoot)
  } catch {
    // First launch or invalid bootstrap file: use the safe per-user default.
  }
  return join(app.getPath('userData'), 'workspace')
}

function saveWorkspaceRoot(path: string): void {
  mkdirSync(app.getPath('userData'), { recursive: true })
  writeFileSync(bootstrapFile(), JSON.stringify({ workspaceRoot: resolve(path) }, null, 2), 'utf8')
}

async function restartEngine(nextRoot: string): Promise<void> {
  engineReady = false
  if (engine && !engine.killed) {
    const current = engine
    current.kill()
    await new Promise<void>((resolveStop) => {
      const timeout = setTimeout(resolveStop, 3_000)
      current.once('exit', () => {
        clearTimeout(timeout)
        resolveStop()
      })
    })
  }
  engine = null
  workspaceRoot = resolve(nextRoot)
  saveWorkspaceRoot(workspaceRoot)
  await startEngine()
}

async function createWindow(): Promise<void> {
  mainWindow = new BrowserWindow({
    show: false,
    autoHideMenuBar: true,
    width: 1480,
    height: 920,
    minWidth: 1120,
    minHeight: 720,
    backgroundColor: '#0b0d12',
    titleBarStyle: 'hiddenInset',
    webPreferences: {
      preload: join(app.getAppPath(), 'dist-electron', 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://')) void shell.openExternal(url)
    return { action: 'deny' }
  })
  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
    appendLog(
      'renderer:error',
      Buffer.from(`页面加载失败（错误码 ${errorCode}）：${errorDescription}；地址=${validatedURL}；主页面=${isMainFrame}`),
    )
  })
  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    appendLog('renderer:error', Buffer.from(`渲染进程异常退出：${details.reason}；代码=${details.exitCode}`))
  })
  mainWindow.webContents.on('console-message', (details) => {
    appendLog(
      `renderer:${details.level}`,
      Buffer.from(`${details.message}; source=${details.sourceId}:${details.lineNumber}`),
    )
  })
  mainWindow.once('ready-to-show', () => mainWindow?.show())
  if (process.env.VITE_DEV_SERVER_URL) {
    await mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL)
  } else {
    await mainWindow.loadFile(join(app.getAppPath(), 'dist', 'index.html'))
  }
}

ipcMain.handle('engine:connection', () => ({
  baseUrl: enginePort ? `http://127.0.0.1:${enginePort}` : '',
  token: engineToken,
  ready: engineReady,
}))
ipcMain.handle('engine:logs', () => [...recentLogs])
ipcMain.handle('engine:clearLogs', () => {
  recentLogs.splice(0, recentLogs.length)
  return true
})
ipcMain.handle('dialog:selectDirectory', async () => {
  const result = await dialog.showOpenDialog(mainWindow!, { properties: ['openDirectory', 'createDirectory'] })
  return result.canceled ? null : result.filePaths[0]
})
ipcMain.handle('dialog:selectFile', async (_event, filters?: Electron.FileFilter[]) => {
  const result = await dialog.showOpenDialog(mainWindow!, { properties: ['openFile'], filters })
  return result.canceled ? null : result.filePaths[0]
})
ipcMain.handle('dialog:selectFiles', async (_event, filters?: Electron.FileFilter[]) => {
  const result = await dialog.showOpenDialog(mainWindow!, {
    properties: ['openFile', 'multiSelections'],
    filters,
  })
  return result.canceled ? [] : result.filePaths
})
const MUSIC_SUFFIXES = new Set(['.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg'])

ipcMain.handle('folder:listImageFiles', (_event, directory: string) => {
  const target = resolve(directory)
  try {
    return readdirSync(target, { withFileTypes: true })
      .filter((entry) => entry.isFile() && /\.(png|jpe?g)$/i.test(entry.name))
      .map((entry) => join(target, entry.name))
  } catch {
    return []
  }
})
ipcMain.handle('folder:listAudioFiles', (_event, directory: string) => {
  const target = resolve(directory)
  try {
    return readdirSync(target, { withFileTypes: true })
      .filter((entry) => entry.isFile() && MUSIC_SUFFIXES.has(extname(entry.name).toLowerCase()))
      .map((entry) => join(target, entry.name))
  } catch {
    return []
  }
})
ipcMain.handle('folder:previewBatchRename', (_event, request: BatchRenameRequest) => planBatchRename(request))
ipcMain.handle('folder:executeBatchRename', (_event, request: BatchRenameRequest) => executeBatchRename(request))
ipcMain.handle('shell:openPath', async (_event, path: string) => shell.openPath(path))
ipcMain.handle('path:toFileUrl', (_event, path: string) => {
  const url = new URL('outocut-media://local/video')
  url.searchParams.set('path', resolve(path))
  return url.href
})
ipcMain.handle('workspace:changeRoot', async () => {
  const result = await dialog.showOpenDialog(mainWindow!, { properties: ['openDirectory', 'createDirectory'] })
  if (result.canceled || !result.filePaths[0]) return null
  await restartEngine(result.filePaths[0])
  return {
    baseUrl: `http://127.0.0.1:${enginePort}`,
    token: engineToken,
    ready: engineReady,
    dataRoot: workspaceRoot,
  }
})
ipcMain.handle('font:readBytes', async (_event, path: string) => {
  try {
    const buffer = readFileSync(resolve(path))
    return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength) as ArrayBuffer
  } catch {
    return null
  }
})

app.whenReady().then(async () => {
  if (!hasSingleInstanceLock) return
  Menu.setApplicationMenu(null)
  protocol.handle('outocut-media', (request) => {
    const localPath = new URL(request.url).searchParams.get('path')
    if (!localPath || !existsSync(localPath)) {
      return new Response('Media file not found', { status: 404 })
    }
    return net.fetch(pathToFileURL(localPath).href, { headers: request.headers })
  })
  workspaceRoot = loadWorkspaceRoot()
  try {
    await startEngine()
  } catch (error) {
    appendLog('electron:error', Buffer.from(`操作失败：${error instanceof Error ? error.message : String(error)}`))
  }
  await createWindow()
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  if (engine && !engine.killed) engine.kill()
})
