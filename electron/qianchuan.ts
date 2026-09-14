import { app } from 'electron'
import { existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { basename, extname, join, resolve } from 'node:path'
import { chromium, type BrowserContext, type Frame, type Locator, type Page } from 'playwright-core'
import { ENTRANCE_COLORS, claimUniqueEntranceColor } from './qianchuan-colors.js'

const QIANCHUAN_URL = 'https://qianchuan.jinritemai.com/'
const SHARED_QUEUE_ID = 'shared-entrances'
const VIDEO_SUFFIXES = new Set(['.mp4', '.mov', '.m4v', '.avi', '.mkv', '.webm', '.flv', '.wmv', '.mpeg', '.mpg'])

export type QianchuanRunState =
  | 'idle'
  | 'waiting_for_user'
  | 'ready'
  | 'uploading'
  | 'paused'
  | 'login_required'
  | 'page_changed'
  | 'completed'
  | 'stopped'
  | 'error'

export type QianchuanFileState = 'pending' | 'uploading' | 'succeeded' | 'failed' | 'skipped'

export interface QianchuanUploadFile {
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

export type QianchuanEntranceState = 'ready' | 'uploading' | 'paused' | 'completed' | 'stopped' | 'unavailable' | 'error'

export interface QianchuanUploadEntrance {
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

export interface QianchuanUploadConfig {
  batchSize: number
  batchTimeoutSeconds: number
  maxConcurrentEntrances: number
  sharedEntrances: boolean
}

export interface QianchuanUploadSnapshot {
  state: QianchuanRunState
  taskRunning: boolean
  message: string
  browserOpen: boolean
  currentBatch: number
  totalBatches: number
  config: QianchuanUploadConfig
  files: QianchuanUploadFile[]
  entrances: QianchuanUploadEntrance[]
  sharedSourceDirectories: string[]
  updatedAt: string
}

type StatusListener = (snapshot: QianchuanUploadSnapshot) => void
type QianchuanLogLevel = 'debug' | 'info' | 'warn' | 'error'
type QianchuanLogListener = (level: QianchuanLogLevel, message: string) => void

function chromeExecutable(): string {
  const candidates = [
    join(process.env.PROGRAMFILES || 'C:\\Program Files', 'Google', 'Chrome', 'Application', 'chrome.exe'),
    join(process.env['PROGRAMFILES(X86)'] || 'C:\\Program Files (x86)', 'Google', 'Chrome', 'Application', 'chrome.exe'),
    join(process.env.LOCALAPPDATA || '', 'Google', 'Chrome', 'Application', 'chrome.exe'),
  ]
  const executable = candidates.find((candidate) => candidate && existsSync(candidate))
  if (!executable) throw new Error('未找到 Google Chrome，请先安装 Chrome 后重试')
  return executable
}

function initialSnapshot(): QianchuanUploadSnapshot {
  return {
    state: 'idle',
    taskRunning: false,
    message: '请选择本地素材文件夹',
    browserOpen: false,
    currentBatch: 0,
    totalBatches: 0,
    config: { batchSize: 9, batchTimeoutSeconds: 600, maxConcurrentEntrances: 10, sharedEntrances: false },
    files: [],
    entrances: [],
    sharedSourceDirectories: [],
    updatedAt: new Date().toISOString(),
  }
}

export class QianchuanUploader {
  private context: BrowserContext | null = null
  private snapshot: QianchuanUploadSnapshot
  private running = false
  private paused = false
  private stopRequested = false
  private entrancePages = new Map<string, Page>()
  private pageIds = new WeakMap<Page, string>()
  private entranceRegistry = new Map<string, QianchuanUploadEntrance>()
  private hasDiscoveredInContext = false
  private entranceSequence = 0
  private batchSequence = 0
  private activeWorkers = new Map<string, Promise<void>>()
  private poolEntranceIds = new Set<string>()

  constructor(
    private readonly onStatus: StatusListener,
    private readonly onLog: QianchuanLogListener = () => undefined,
  ) {
    this.snapshot = this.load()
    for (const entrance of this.snapshot.entrances) this.entranceRegistry.set(entrance.id, entrance)
    this.entranceSequence = this.snapshot.entrances.reduce((maximum, entrance) => {
      const sequence = Number(/^entrance-(\d+)$/.exec(entrance.id)?.[1] || 0)
      return Math.max(maximum, sequence)
    }, 0)
    this.log('info', '模块初始化', {
      restoredEntrances: this.snapshot.entrances.length,
      restoredFiles: this.snapshot.files.length,
      batchSize: this.snapshot.config.batchSize,
      batchTimeoutSeconds: this.snapshot.config.batchTimeoutSeconds,
      maxConcurrentEntrances: this.snapshot.config.maxConcurrentEntrances,
      sharedEntrances: this.snapshot.config.sharedEntrances,
    })
  }

  private log(level: QianchuanLogLevel, event: string, details?: Record<string, unknown>): void {
    try {
      const suffix = details && Object.keys(details).length ? ` ${JSON.stringify(details)}` : ''
      this.onLog(level, `${event}${suffix}`)
    } catch {
      // Logging must never interrupt an upload task.
    }
  }

  private entranceDetails(entranceId: string): Record<string, unknown> {
    const entrance = this.snapshot.entrances.find((item) => item.id === entranceId)
    return {
      entranceId,
      entranceName: entrance?.name || '',
      entranceColor: entrance?.color || '',
    }
  }

  getStatus(): QianchuanUploadSnapshot {
    return structuredClone(this.snapshot)
  }

  scanDirectory(directoryInput: string): QianchuanUploadSnapshot {
    void directoryInput
    throw new Error('请先识别上传入口，再为每个入口分别选择素材文件夹')
  }

  assignEntranceDirectory(entranceId: string, directoryInput: string): QianchuanUploadSnapshot {
    return this.assignEntranceDirectories(entranceId, [directoryInput])
  }

  assignEntranceDirectories(entranceId: string, directoryInputs: string[]): QianchuanUploadSnapshot {
    if (this.activeWorkers.has(entranceId) || this.poolEntranceIds.has(entranceId)) throw new Error('该入口正在上传或等待上传，不能更换文件夹')
    const entrance = this.snapshot.entrances.find((item) => item.id === entranceId)
    if (!entrance || !this.entrancePages.has(entranceId)) throw new Error('上传入口不存在，请重新识别全部入口')
    const directories = [...new Map(directoryInputs
      .map((directory) => directory.trim())
      .filter(Boolean)
      .map((directory) => resolve(directory))
      .map((directory) => [directory.toLowerCase(), directory] as const)).values()]
    if (!directories.length) throw new Error('请至少选择一个素材文件夹')
    for (const directory of directories) {
      if (!existsSync(directory) || !lstatSync(directory).isDirectory()) throw new Error(`素材文件夹不存在：${directory}`)
    }
    const files = directories.flatMap((directory) => readdirSync(directory, { withFileTypes: true })
      .filter((entry) => entry.isFile() && VIDEO_SUFFIXES.has(extname(entry.name).toLowerCase()))
      .map((entry) => {
        const path = join(directory, entry.name)
        const info = statSync(path)
        return {
          id: `${entranceId}:${path}:${info.size}:${info.mtimeMs}`,
          path,
          name: entry.name,
          size: info.size,
          state: 'pending' as const,
          error: '',
          entranceId,
          entranceColor: entrance.color,
          queueId: entranceId,
        }
      }))
      .sort((left, right) => left.name.localeCompare(right.name, 'zh-CN', { numeric: true, sensitivity: 'base' }) || left.path.localeCompare(right.path))
    this.snapshot.files = [
      ...this.snapshot.files.filter((file) => file.entranceId !== entranceId),
      ...files,
    ]
    entrance.sourceDirectories = directories
    entrance.sourceDirectory = directories[0] || ''
    entrance.totalFiles = files.length
    if (/^上传入口\s*\d+$/.test(entrance.name)) entrance.name = basename(directories[0]) || entrance.name
    entrance.state = 'ready'
    entrance.message = files.length
      ? `已扫描 ${directories.length} 个文件夹，共 ${files.length} 个视频`
      : `${directories.length} 个文件夹中没有支持的视频文件`
    this.log('info', '入口素材已更新', {
      ...this.entranceDetails(entranceId),
      directoryCount: directories.length,
      directoryNames: directories.map((directory) => basename(directory)),
      videoCount: files.length,
    })
    void this.decorateEntrancePage(entranceId)
    this.recalculateTotalBatches()
    this.commit()
    return this.getStatus()
  }

  assignSharedDirectories(directoryInputs: string[]): QianchuanUploadSnapshot {
    if (this.running) throw new Error('上传任务运行中，不能更换共用文件夹')
    const directories = [...new Map(directoryInputs
      .map((directory) => directory.trim())
      .filter(Boolean)
      .map((directory) => resolve(directory))
      .map((directory) => [directory.toLowerCase(), directory] as const)).values()]
    if (!directories.length) throw new Error('请至少选择一个共用素材文件夹')
    for (const directory of directories) {
      if (!existsSync(directory) || !lstatSync(directory).isDirectory()) throw new Error(`素材文件夹不存在：${directory}`)
    }
    const videoCount = directories.reduce((total, directory) => total + readdirSync(directory, { withFileTypes: true })
      .filter((entry) => entry.isFile() && VIDEO_SUFFIXES.has(extname(entry.name).toLowerCase())).length, 0)
    const files = this.snapshot.entrances.flatMap((entrance) => this.sharedFilesForEntrance(entrance, directories))
    this.snapshot.files = [
      ...this.snapshot.files.filter((file) => !this.isSharedFile(file)),
      ...files,
    ]
    this.snapshot.sharedSourceDirectories = directories
    for (const entrance of this.snapshot.entrances) {
      entrance.totalFiles = files.filter((file) => file.entranceId === entrance.id).length
      entrance.message = `共用素材：${entrance.totalFiles} 个视频等待该入口完整上传`
    }
    this.snapshot.message = videoCount
      ? this.snapshot.entrances.length
        ? `共用素材已扫描 ${directories.length} 个文件夹，${this.snapshot.entrances.length} 个入口各 ${videoCount} 个视频`
        : `共用素材已扫描 ${directories.length} 个文件夹、${videoCount} 个视频；识别入口后将为每个入口建立完整记录`
      : `${directories.length} 个共用文件夹中没有支持的视频文件`
    this.log('info', '共用素材已更新', {
      directoryCount: directories.length,
      directoryNames: directories.map((directory) => basename(directory)),
      entranceCount: this.snapshot.entrances.length,
      videosPerEntrance: videoCount,
    })
    this.recalculateTotalBatches()
    this.commit()
    return this.getStatus()
  }

  renameEntrance(entranceId: string, nameInput: string): QianchuanUploadSnapshot {
    const entrance = this.snapshot.entrances.find((item) => item.id === entranceId)
    if (!entrance) throw new Error('上传入口不存在')
    const name = nameInput.trim()
    if (!name) throw new Error('入口名称不能为空')
    if (name.length > 40) throw new Error('入口名称不能超过 40 个字符')
    entrance.name = name
    entrance.message = entrance.sourceDirectories.length ? `已关联 ${entrance.totalFiles} 个视频` : '请选择该入口的素材文件夹'
    void this.decorateEntrancePage(entranceId)
    this.commit()
    return this.getStatus()
  }

  configure(config: Partial<QianchuanUploadConfig>): QianchuanUploadSnapshot {
    if (this.running && !this.paused) throw new Error('请先暂停任务，再修改上传配置')
    if (config.batchSize !== undefined) {
      if (!Number.isInteger(config.batchSize) || config.batchSize < 1 || config.batchSize > 100) {
        throw new Error('每批文件数必须是 1 到 100 之间的整数')
      }
      this.snapshot.config.batchSize = config.batchSize
    }
    if (config.batchTimeoutSeconds !== undefined) {
      if (!Number.isInteger(config.batchTimeoutSeconds) || config.batchTimeoutSeconds < 30 || config.batchTimeoutSeconds > 3600) {
        throw new Error('单批超时时间必须是 30 到 3600 秒之间的整数')
      }
      this.snapshot.config.batchTimeoutSeconds = config.batchTimeoutSeconds
    }
    if (config.maxConcurrentEntrances !== undefined) {
      if (!Number.isInteger(config.maxConcurrentEntrances) || config.maxConcurrentEntrances < 1 || config.maxConcurrentEntrances > 50) {
        throw new Error('并发入口数必须是 1 到 50 之间的整数')
      }
      this.snapshot.config.maxConcurrentEntrances = config.maxConcurrentEntrances
    }
    if (config.sharedEntrances !== undefined) {
      this.snapshot.config.sharedEntrances = Boolean(config.sharedEntrances)
      this.snapshot.message = this.snapshot.config.sharedEntrances
        ? '已启用多入口共用，请选择共用素材文件夹'
        : '已切换为入口独立素材文件夹模式'
    }
    this.log('info', '上传配置已更新', { ...this.snapshot.config })
    this.recalculateTotalBatches()
    this.commit()
    return this.getStatus()
  }

  async openChrome(): Promise<QianchuanUploadSnapshot> {
    if (this.context) {
      const page = await this.context.newPage()
      await page.goto(QIANCHUAN_URL, { waitUntil: 'domcontentloaded' })
      await page.bringToFront()
      this.log('info', '新增千川标签页', { openPages: this.context.pages().length })
      this.setState('waiting_for_user', '已新增千川标签页；请进入上传视频页面，准备完成后识别全部入口')
      return this.getStatus()
    }
    const profileDirectory = join(app.getPath('userData'), 'qianchuan-chrome-profile')
    mkdirSync(profileDirectory, { recursive: true })
    this.context = await chromium.launchPersistentContext(profileDirectory, {
      executablePath: chromeExecutable(),
      headless: false,
      viewport: null,
      acceptDownloads: false,
      args: ['--start-maximized'],
    })
    this.context.once('close', () => {
      this.log('warn', 'Chrome上下文已关闭', {
        wasRunning: this.running,
        activeWorkers: this.activeWorkers.size,
      })
      this.context = null
      this.running = false
      this.paused = false
      this.stopRequested = true
      this.snapshot.browserOpen = false
      this.entrancePages.clear()
      this.pageIds = new WeakMap<Page, string>()
      this.hasDiscoveredInContext = false
      this.snapshot.entrances = this.snapshot.entrances.map((entrance) => ({
        ...entrance,
        state: 'unavailable',
        message: 'Chrome 已关闭',
        taskRunning: false,
      }))
      for (const entrance of this.snapshot.entrances) this.entranceRegistry.set(entrance.id, entrance)
      if (this.snapshot.state === 'uploading' || this.snapshot.state === 'ready') {
        this.setState('stopped', 'Chrome 已关闭，上传任务已停止')
      } else {
        this.commit()
      }
    })
    const pages = this.context.pages()
    const page = pages[0] || await this.context.newPage()
    if (page.url() === 'about:blank') await page.goto(QIANCHUAN_URL, { waitUntil: 'domcontentloaded' })
    else await page.bringToFront()
    this.snapshot.browserOpen = true
    this.log('info', 'Chrome已启动', { openPages: this.context.pages().length })
    this.setState('waiting_for_user', '请在 Chrome 中登录，并人工进入视频素材上传入口')
    return this.getStatus()
  }

  async discoverEntrances(): Promise<QianchuanUploadSnapshot> {
    if (!this.context) throw new Error('请先打开 Chrome')
    if (this.running) throw new Error('上传任务运行中，不能重新识别入口')
    const discovered: QianchuanUploadEntrance[] = []
    const nextPages = new Map<string, Page>()
    const usedIds = new Set<string>()
    const usedColors = new Set<string>()
    const mayRecoverSavedEntrances = !this.hasDiscoveredInContext
    this.log('info', '开始识别上传入口', {
      openPages: this.context.pages().length,
      mayRecoverSavedEntrances,
    })
    for (const page of this.context.pages()) {
      if (page.isClosed()) continue
      if (!(await this.hasUploadTarget(page))) {
        await this.clearPageDecoration(page)
        continue
      }
      let id = this.pageIds.get(page)
      if (!id) {
        const previousSlot = mayRecoverSavedEntrances
          ? [...this.entranceRegistry.values()].find((entrance) => !usedIds.has(entrance.id))
          : undefined
        if (previousSlot) id = previousSlot.id
        else {
          do {
            this.entranceSequence += 1
            id = `entrance-${this.entranceSequence}`
          } while (usedIds.has(id) || this.entranceRegistry.has(id))
        }
        this.pageIds.set(page, id)
      }
      usedIds.add(id)
      nextPages.set(id, page)
      const previous = this.entranceRegistry.get(id)
      const assignedFiles = this.snapshot.files.filter((file) => file.entranceId === id && !this.isSharedFile(file))
      const sourceDirectories = previous?.sourceDirectories?.length
        ? previous.sourceDirectories
        : previous?.sourceDirectory ? [previous.sourceDirectory] : []
      const discoveredEntrance: QianchuanUploadEntrance = {
        id,
        name: previous?.name || `上传入口 ${discovered.length + 1}`,
        url: page.url(),
        state: 'ready',
        currentBatch: 0,
        fileCount: 0,
        message: sourceDirectories.length ? `已关联 ${sourceDirectories.length} 个文件夹、${assignedFiles.length} 个视频` : '请选择该入口的素材文件夹',
        sourceDirectory: sourceDirectories[0] || '',
        sourceDirectories,
        totalFiles: assignedFiles.length,
        taskRunning: false,
        color: claimUniqueEntranceColor(usedColors, previous?.color),
      }
      for (const file of assignedFiles) file.entranceColor = discoveredEntrance.color
      discovered.push(discoveredEntrance)
      this.entranceRegistry.set(id, discoveredEntrance)
      await this.decoratePage(page, discoveredEntrance)
    }
    if (this.snapshot.config.sharedEntrances && this.snapshot.sharedSourceDirectories.length) {
      const activeEntranceIds = new Set(discovered.map((entrance) => entrance.id))
      this.snapshot.files = this.snapshot.files.filter((file) => (
        !this.isSharedFile(file) || Boolean(file.entranceId && activeEntranceIds.has(file.entranceId))
      ))
      for (const entrance of discovered) {
        const queueId = this.sharedQueueId(entrance.id)
        const hasSharedQueue = this.snapshot.files.some((file) => file.queueId === queueId)
        if (!hasSharedQueue) this.snapshot.files.push(...this.sharedFilesForEntrance(entrance, this.snapshot.sharedSourceDirectories))
        const sharedFiles = this.snapshot.files.filter((file) => file.queueId === queueId)
        for (const file of sharedFiles) file.entranceColor = entrance.color
        entrance.totalFiles = sharedFiles.length
        entrance.message = `共用素材：${entrance.totalFiles} 个视频等待该入口完整上传`
      }
    }
    if (discovered.length) this.hasDiscoveredInContext = true
    this.entrancePages = nextPages
    this.snapshot.entrances = discovered
    this.log(discovered.length ? 'info' : 'warn', '上传入口识别完成', {
      discoveredCount: discovered.length,
      entrances: discovered.map((entrance) => ({
        id: entrance.id,
        name: entrance.name,
        color: entrance.color,
        assignedVideos: entrance.totalFiles,
      })),
    })
    this.setState(
      discovered.length ? 'ready' : 'waiting_for_user',
      discovered.length
        ? `已识别 ${discovered.length} 个上传入口，最多并发使用 ${Math.min(discovered.length, this.snapshot.config.maxConcurrentEntrances)} 个`
        : '未识别到上传入口；请在 Chrome 中打开一个或多个“上传视频”页面',
    )
    return this.getStatus()
  }

  async focusEntrance(entranceId: string): Promise<QianchuanUploadSnapshot> {
    const page = this.entrancePages.get(entranceId)
    const entrance = this.snapshot.entrances.find((item) => item.id === entranceId)
    if (!page || !entrance || page.isClosed()) throw new Error('入口页面已失效，请重新识别全部入口')
    await this.decoratePage(page, entrance)
    await page.bringToFront()
    entrance.message = '已定位到对应 Chrome 页面'
    this.commit()
    return this.getStatus()
  }

  async start(): Promise<QianchuanUploadSnapshot> {
    if (this.running) throw new Error('上传任务已在运行')
    if (!this.context) throw new Error('请先打开 Chrome，并进入千川视频素材上传入口')
    await this.discoverEntrances()
    const sharedMode = this.snapshot.config.sharedEntrances
    const workers = [...this.entrancePages.entries()].filter(([entranceId]) => {
      const entrance = this.snapshot.entrances.find((item) => item.id === entranceId)
      const hasDirectories = sharedMode
        ? this.snapshot.sharedSourceDirectories.length > 0
        : Boolean(entrance?.sourceDirectories.length)
      return hasDirectories && this.snapshot.files.some((file) => (
        file.entranceId === entranceId
        && this.fileMatchesMode(file)
        && (file.state === 'pending' || file.state === 'failed')
      ))
    })
    if (!workers.length) throw new Error('请至少为一个已识别上传入口选择包含待上传视频的素材文件夹')
    const workerIds = new Set(workers.map(([id]) => id))
    this.poolEntranceIds = new Set(workerIds)
    const pending = this.snapshot.files.filter((file) => (
      Boolean(file.entranceId && workerIds.has(file.entranceId) && this.fileMatchesMode(file))
      && (file.state === 'pending' || file.state === 'failed')
    ))
    for (const file of pending) {
      file.state = 'pending'
      file.error = ''
    }
    this.running = true
    this.paused = false
    this.stopRequested = false
    this.snapshot.currentBatch = 0
    this.snapshot.totalBatches = workers.reduce((total, [entranceId]) => (
      total + Math.ceil(pending.filter((file) => file.entranceId === entranceId).length / this.snapshot.config.batchSize)
    ), 0)
    this.log('info', '一键上传任务启动', {
      mode: sharedMode ? 'shared' : 'independent',
      entranceCount: workers.length,
      pendingFiles: pending.length,
      totalBatches: this.snapshot.totalBatches,
      batchSize: this.snapshot.config.batchSize,
      maxConcurrentEntrances: this.snapshot.config.maxConcurrentEntrances,
    })
    this.batchSequence = 0
    this.snapshot.entrances = this.snapshot.entrances.map((entrance) => ({
      ...entrance,
      state: workers.some(([id], index) => id === entrance.id && index < this.snapshot.config.maxConcurrentEntrances) ? 'ready' : 'paused',
      currentBatch: 0,
      fileCount: 0,
      message: workers.some(([id], index) => id === entrance.id && index < this.snapshot.config.maxConcurrentEntrances)
        ? '等待领取自身文件夹的上传批次'
        : workers.some(([id]) => id === entrance.id)
          ? '等待并发槽位'
          : sharedMode
            ? this.snapshot.sharedSourceDirectories.length ? '该入口已完成共用素材' : '尚未选择共用素材文件夹'
            : entrance.sourceDirectories.length ? '所选文件夹没有待上传视频' : '尚未选择素材文件夹',
      taskRunning: workers.some(([id], index) => id === entrance.id && index < this.snapshot.config.maxConcurrentEntrances),
    }))
    this.setState('ready', sharedMode
      ? `${workers.length} 个入口将分别完整上传共用素材，最多同时运行 ${Math.min(workers.length, this.snapshot.config.maxConcurrentEntrances)} 个`
      : `${workers.length} 个入口已有独立队列，最多同时运行 ${Math.min(workers.length, this.snapshot.config.maxConcurrentEntrances)} 个`)
    void this.runPool(workers).catch((error) => {
      this.running = false
      this.poolEntranceIds.clear()
      for (const entrance of this.snapshot.entrances) entrance.taskRunning = false
      const message = error instanceof Error ? error.message : String(error)
      this.log('error', '并发上传池异常退出', { error: message })
      this.setState('error', message)
    })
    return this.getStatus()
  }

  async startEntrance(entranceId: string): Promise<QianchuanUploadSnapshot> {
    if (this.snapshot.config.sharedEntrances) throw new Error('多入口共用模式请使用共用操作框开始上传')
    if (!this.context) throw new Error('请先打开 Chrome 并识别上传入口')
    if (this.activeWorkers.has(entranceId)) throw new Error('该入口已经在上传')
    if (this.poolEntranceIds.has(entranceId)) throw new Error('该入口已加入一键上传队列')
    if (this.activeWorkers.size >= this.snapshot.config.maxConcurrentEntrances) {
      throw new Error(`当前已达到 ${this.snapshot.config.maxConcurrentEntrances} 个并发入口上限`)
    }
    const page = this.entrancePages.get(entranceId)
    const entrance = this.snapshot.entrances.find((item) => item.id === entranceId)
    if (!page || !entrance) throw new Error('入口连接已失效，请重新识别全部入口')
    if (!entrance.sourceDirectories.length) throw new Error('请先为该入口选择素材文件夹')
    const candidates = this.snapshot.files.filter((file) => (
      file.entranceId === entranceId && (file.state === 'pending' || file.state === 'failed')
    ))
    if (!candidates.length) throw new Error('该入口没有等待上传的视频')
    for (const file of candidates) {
      file.state = 'pending'
      file.error = ''
    }
    if (!this.running) {
      this.stopRequested = false
      this.paused = false
      this.batchSequence = 0
    }
    this.running = true
    this.log('info', '单入口上传任务启动', {
      ...this.entranceDetails(entranceId),
      pendingFiles: candidates.length,
      batchSize: this.snapshot.config.batchSize,
      batchTimeoutSeconds: this.snapshot.config.batchTimeoutSeconds,
    })
    entrance.state = 'ready'
    entrance.currentBatch = 0
    entrance.fileCount = 0
    entrance.message = '入口任务已启动，准备上传自身文件夹'
    entrance.taskRunning = true
    this.snapshot.state = 'uploading'
    this.snapshot.message = `${this.activeWorkers.size + 1} 个入口任务正在并发运行`
    this.commit()
    const worker = this.runWorker(entranceId, page)
      .catch((error) => {
        entrance.state = 'error'
        entrance.message = error instanceof Error ? error.message : String(error)
        this.log('error', '单入口任务异常退出', {
          ...this.entranceDetails(entranceId),
          error: entrance.message,
        })
      })
      .finally(() => this.finishEntranceWorker(entranceId))
    this.activeWorkers.set(entranceId, worker)
    this.commit()
    return this.getStatus()
  }

  pause(): QianchuanUploadSnapshot {
    if (!this.running) throw new Error('当前没有运行中的任务')
    this.paused = true
    this.log('info', '收到暂停请求', { activeWorkers: this.activeWorkers.size })
    this.snapshot.entrances = this.snapshot.entrances.map((entrance) => (
      this.activeWorkers.has(entrance.id) && entrance.state === 'ready'
        ? { ...entrance, state: 'paused', message: '等待当前批次完成后暂停' }
        : entrance
    ))
    this.setState('paused', '当前批次完成后暂停')
    return this.getStatus()
  }

  resume(): QianchuanUploadSnapshot {
    if (!this.running || !this.paused) throw new Error('当前任务不处于暂停状态')
    this.paused = false
    this.log('info', '上传任务继续', { activeWorkers: this.activeWorkers.size })
    this.snapshot.entrances = this.snapshot.entrances.map((entrance) => (
      this.activeWorkers.has(entrance.id) && entrance.state === 'paused'
        ? { ...entrance, state: 'ready', message: '任务已继续，等待领取批次' }
        : entrance
    ))
    this.setState('uploading', '任务已继续')
    return this.getStatus()
  }

  stop(): QianchuanUploadSnapshot {
    this.stopRequested = true
    this.paused = false
    const activeIds = new Set([...this.activeWorkers.keys(), ...this.poolEntranceIds])
    this.log('warn', '收到停止请求', { affectedEntrances: activeIds.size })
    this.poolEntranceIds.clear()
    for (const file of this.snapshot.files) {
      if (file.state === 'uploading') {
        file.state = 'pending'
      }
    }
    this.snapshot.entrances = this.snapshot.entrances.map((entrance) => (
      activeIds.has(entrance.id) ? { ...entrance, state: 'stopped', message: '任务已停止', taskRunning: false } : entrance
    ))
    this.running = false
    this.setState('stopped', '任务已停止；已成功文件会保留，未完成文件可继续上传')
    return this.getStatus()
  }

  retryFailed(): QianchuanUploadSnapshot {
    if (this.running) throw new Error('请先停止当前任务')
    let count = 0
    for (const file of this.snapshot.files) {
      if (file.state === 'failed' && this.fileMatchesMode(file)) {
        file.state = 'pending'
        file.error = ''
        count += 1
      }
    }
    this.log('info', '失败文件已重新入队', { fileCount: count })
    this.setState('idle', count ? `已将 ${count} 个失败文件放回待上传队列` : '没有失败文件')
    return this.getStatus()
  }

  confirmSucceeded(fileIds: string[]): QianchuanUploadSnapshot {
    const ids = new Set(fileIds)
    let count = 0
    for (const file of this.snapshot.files) {
      if (ids.has(file.id) && file.state !== 'succeeded') {
        file.state = 'succeeded'
        file.error = ''
        count += 1
      }
    }
    if (!count) throw new Error('没有可确认成功的文件')
    this.log('info', '人工确认上传成功', { fileCount: count })
    this.snapshot.message = `已人工确认 ${count} 个文件上传成功`
    this.commit()
    return this.getStatus()
  }

  async close(): Promise<void> {
    this.stopRequested = true
    await this.context?.close().catch(() => undefined)
    this.context = null
  }

  private finishEntranceWorker(entranceId: string): void {
    this.activeWorkers.delete(entranceId)
    const entrance = this.snapshot.entrances.find((item) => item.id === entranceId)
    if (entrance) entrance.taskRunning = false
    if (this.stopRequested) {
      this.running = false
      this.commit()
      return
    }
    this.running = this.activeWorkers.size > 0
    this.recalculateTotalBatches()
    if (this.running) {
      this.snapshot.state = this.paused ? 'paused' : 'uploading'
      this.snapshot.message = `${this.activeWorkers.size} 个入口任务仍在运行`
      this.commit()
      return
    }
    const remaining = this.snapshot.files.filter((file) => file.state === 'pending' && this.fileMatchesMode(file)).length
    const failures = this.snapshot.files.filter((file) => file.state === 'failed' && this.fileMatchesMode(file)).length
    if (remaining || failures) {
      this.setState('ready', '当前入口任务已结束；其他入口仍可单独启动，失败文件可重新上传或人工确认')
    } else {
      this.setState('completed', '所有已启动入口均已完成；请在各千川页面人工检查并最终提交')
    }
  }

  private async runPool(workers: Array<[string, Page]>): Promise<void> {
    const queue = [...workers]
    const concurrency = Math.min(queue.length, this.snapshot.config.maxConcurrentEntrances)
    this.log('info', '并发上传池开始运行', { queuedEntrances: queue.length, concurrency })
    await Promise.all(Array.from({ length: concurrency }, async () => {
      while (queue.length && !this.stopRequested) {
        const worker = queue.shift()
        if (!worker) return
        const [entranceId, page] = worker
        const entrance = this.snapshot.entrances.find((item) => item.id === entranceId)
        if (entrance) {
          entrance.state = 'ready'
          entrance.message = this.snapshot.config.sharedEntrances
            ? '已获得并发槽位，准备完整上传共用素材'
            : '已获得并发槽位，准备上传自身文件夹'
          entrance.taskRunning = true
          this.commit()
        }
        const task = this.runWorker(entranceId, page)
        this.activeWorkers.set(entranceId, task)
        try {
          await task
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error)
          this.log('error', '入口工作线程异常', {
            ...this.entranceDetails(entranceId),
            error: message,
          })
          if (entrance) {
            entrance.state = 'error'
            entrance.message = message
          }
        } finally {
          this.activeWorkers.delete(entranceId)
          this.poolEntranceIds.delete(entranceId)
          if (entrance) entrance.taskRunning = false
          this.commit()
        }
      }
    }))
    this.running = false
    this.poolEntranceIds.clear()
    if (this.stopRequested) return
    const pending = this.snapshot.files.filter((file) => file.state === 'pending' && this.fileMatchesMode(file)).length
    const failures = this.snapshot.files.filter((file) => file.state === 'failed' && this.fileMatchesMode(file)).length
    this.log(failures || pending ? 'warn' : 'info', '并发上传池运行结束', {
      pendingFiles: pending,
      failedFiles: failures,
      stopped: this.stopRequested,
    })
    if (pending) {
      this.setState('page_changed', `所有可用上传入口均已停止，仍有 ${pending} 个文件等待处理`)
      return
    }
    this.setState(
      'completed',
      failures
        ? `并发上传已结束，${failures} 个文件失败；请在各千川页面人工检查并最终提交`
        : `全部批次已由 ${workers.length} 个入口处理；请在各千川页面人工检查并最终提交`,
    )
  }

  private async runWorker(entranceId: string, page: Page): Promise<void> {
    while (!this.stopRequested) {
      while (this.paused && !this.stopRequested) await new Promise((resolveWait) => setTimeout(resolveWait, 250))
      if (this.stopRequested) return
      const entrance = this.snapshot.entrances.find((item) => item.id === entranceId)
      if (!entrance) return
      if (page.isClosed() || !(await this.hasUploadTarget(page))) {
        this.log('error', '上传入口失效', {
          ...this.entranceDetails(entranceId),
          pageClosed: page.isClosed(),
        })
        entrance.state = 'unavailable'
        entrance.message = '上传入口已关闭或页面结构已变化'
        this.commit()
        return
      }
      const batch = this.claimBatch(entranceId)
      if (!batch.length) {
        this.log('info', '入口队列处理完成', { ...this.entranceDetails(entranceId) })
        entrance.state = 'completed'
        entrance.fileCount = 0
        entrance.message = '没有更多待处理文件'
        this.commit()
        return
      }
      entrance.state = 'uploading'
      entrance.currentBatch = this.batchSequence
      entrance.fileCount = batch.length
      entrance.message = `正在上传 ${batch.length} 个视频`
      this.log('info', '入口领取上传批次', {
        ...this.entranceDetails(entranceId),
        batch: this.batchSequence,
        fileCount: batch.length,
        fileNames: batch.map((file) => file.name),
      })
      this.snapshot.state = 'uploading'
      this.snapshot.message = `${this.snapshot.entrances.filter((item) => item.state === 'uploading').length} 个入口正在并发上传`
      this.commit()
      try {
        await this.uploadBatch(page, batch)
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        this.log('error', '上传批次异常', {
          ...this.entranceDetails(entranceId),
          batch: entrance.currentBatch,
          fileCount: batch.length,
          fileNames: batch.map((file) => file.name),
          error: message,
        })
        for (const file of batch) {
          if (file.state === 'uploading') {
            file.state = 'failed'
            file.error = `${entrance.name}：${message}`
          }
        }
        if (/无法确认|平台未显示|未找到/.test(message)) {
          entrance.state = 'error'
          entrance.message = `${message}；该入口已停用`
          this.commit()
          return
        }
      }
      if (this.stopRequested) return
      entrance.state = this.paused ? 'paused' : 'ready'
      entrance.fileCount = 0
      entrance.message = this.paused ? '本批完成，等待继续' : '本批完成，准备领取下一批'
      this.commit()
    }
  }

  private claimBatch(entranceId: string): QianchuanUploadFile[] {
    const batch = this.snapshot.files
      .filter((file) => file.state === 'pending' && file.entranceId === entranceId && this.fileMatchesMode(file))
      .slice(0, this.snapshot.config.batchSize)
    if (!batch.length) return []
    this.batchSequence += 1
    this.snapshot.currentBatch = this.batchSequence
    for (const file of batch) {
      file.state = 'uploading'
    }
    return batch
  }

  private sharedQueueId(entranceId: string): string {
    return `${SHARED_QUEUE_ID}:${entranceId}`
  }

  private isSharedFile(file: QianchuanUploadFile): boolean {
    return Boolean(file.queueId?.startsWith(`${SHARED_QUEUE_ID}:`))
  }

  private fileMatchesMode(file: QianchuanUploadFile): boolean {
    return this.snapshot.config.sharedEntrances === this.isSharedFile(file)
  }

  private sharedFilesForEntrance(entrance: QianchuanUploadEntrance, directories: string[]): QianchuanUploadFile[] {
    const queueId = this.sharedQueueId(entrance.id)
    return directories.flatMap((directory) => readdirSync(directory, { withFileTypes: true })
      .filter((entry) => entry.isFile() && VIDEO_SUFFIXES.has(extname(entry.name).toLowerCase()))
      .map((entry) => {
        const path = join(directory, entry.name)
        const info = statSync(path)
        return {
          id: `${queueId}:${path}:${info.size}:${info.mtimeMs}`,
          path,
          name: entry.name,
          size: info.size,
          state: 'pending' as const,
          error: '',
          entranceId: entrance.id,
          entranceColor: entrance.color,
          queueId,
        }
      }))
      .sort((left, right) => left.name.localeCompare(right.name, 'zh-CN', { numeric: true, sensitivity: 'base' }) || left.path.localeCompare(right.path))
  }

  private async uploadBatch(page: Page, batch: QianchuanUploadFile[]): Promise<void> {
    const startedAt = Date.now()
    const entranceId = batch[0]?.entranceId || 'unknown'
    const input = await this.uploadInput(page)
    const previousFailureText = await this.visibleFailureText(page)
    const previousSuccessText = await this.visibleSuccessText(page)
    const paths = batch.map((file) => file.path)
    if (input) {
      await input.setInputFiles(paths)
    } else {
      const trigger = await this.uploadTrigger(page)
      if (!trigger) throw new Error('未找到“点击上传”按钮或视频文件入口')
      const chooserPromise = page.waitForEvent('filechooser', { timeout: 10_000 })
      await trigger.click()
      const chooser = await chooserPromise
      await chooser.setFiles(paths)
    }
    this.log('info', '文件已提交给千川页面', {
      ...this.entranceDetails(entranceId),
      fileCount: batch.length,
      submitMethod: input ? 'file-input' : 'file-chooser',
      frameCount: page.frames().length,
    })
    await page.waitForTimeout(1_500)
    const batchTimeoutMs = this.snapshot.config.batchTimeoutSeconds * 1000
    const timeoutAt = Date.now() + batchTimeoutMs
    // 千川页面在多入口并发、网络较慢或 Chrome 负载较高时，文件列表和进度区
    // 可能需要较长时间才完成首次渲染。给首次活动最多 120 秒，同时不超过
    // 用户配置的单批总超时，避免总超时设置失去作用。
    const activityWaitMs = Math.min(120_000, batchTimeoutMs)
    const activityDeadline = Date.now() + activityWaitMs
    let quietChecks = 0
    let sawActivity = false
    let sawProgress = false
    let activityLogged = false
    while (Date.now() < timeoutAt && !this.stopRequested) {
      if (batch.every((file) => file.state === 'succeeded')) return
      const failureText = await this.visibleFailureText(page)
      const newFailureText = failureText && failureText !== previousFailureText ? failureText : ''
      if (newFailureText) {
        sawActivity = true
        this.log('warn', '千川页面出现失败提示', {
          ...this.entranceDetails(entranceId),
          message: newFailureText.slice(0, 500),
        })
        const matched = batch.filter((file) => newFailureText.includes(file.name))
        const failed = matched.length ? matched : batch
        for (const file of failed) {
          file.state = 'failed'
          file.error = newFailureText.slice(0, 500)
        }
      }
      const inProgress = await this.hasVisibleProgress(page)
      if (inProgress) sawProgress = true
      const pageText = await page.locator('body').innerText().catch(() => '')
      const listsSelectedFile = batch.some((file) => pageText.includes(file.name))
      if (inProgress || listsSelectedFile) sawActivity = true
      if (sawActivity && !activityLogged) {
        activityLogged = true
        this.log('info', '已识别到上传活动', {
          ...this.entranceDetails(entranceId),
          sawProgress: inProgress,
          sawFileNameInMainFrame: listsSelectedFile,
          elapsedSeconds: Math.round((Date.now() - startedAt) / 1000),
        })
      }
      if (inProgress) quietChecks = 0
      else quietChecks += 1
      const successText = await this.visibleSuccessText(page)
      const hasNewSuccess = Boolean(successText && successText !== previousSuccessText)
      const completedNames = await this.completedFileNames(page, batch.map((file) => file.name))
      if (hasNewSuccess || completedNames.size === batch.length || (sawProgress && quietChecks >= 5)) {
        const confirmation = hasNewSuccess
          ? 'success-message'
          : completedNames.size === batch.length ? 'all-file-rows-completed' : 'progress-became-quiet'
        for (const file of batch) {
          if (file.state === 'uploading') file.state = 'succeeded'
        }
        this.log('info', '上传批次确认成功', {
          ...this.entranceDetails(entranceId),
          fileCount: batch.length,
          confirmation,
          completedFileNames: completedNames.size,
          sawProgress,
          elapsedSeconds: Math.round((Date.now() - startedAt) / 1000),
        })
        return
      }
      if (!sawActivity && Date.now() >= activityDeadline) {
        this.log('error', '等待上传活动超时', {
          ...this.entranceDetails(entranceId),
          fileCount: batch.length,
          activityWaitSeconds: Math.round(activityWaitMs / 1000),
          completedFileNames: completedNames.size,
          frameCount: page.frames().length,
        })
        throw new Error(`平台在 ${Math.round(activityWaitMs / 1000)} 秒内未显示所选文件或上传进度，无法确认本批是否开始`)
      }
      await page.waitForTimeout(1_000)
    }
    if (this.stopRequested) return
    this.log('error', '上传批次总超时', {
      ...this.entranceDetails(entranceId),
      fileCount: batch.length,
      timeoutSeconds: this.snapshot.config.batchTimeoutSeconds,
      sawActivity,
      sawProgress,
    })
    throw new Error(`本批上传超过 ${this.snapshot.config.batchTimeoutSeconds} 秒，结果未能确认`)
  }

  private async hasUploadTarget(page: Page): Promise<boolean> {
    return (await this.uploadInput(page)) !== null || (await this.uploadTrigger(page)) !== null
  }

  private async uploadInput(page: Page): Promise<Locator | null> {
    for (const frame of this.uploadFrames(page)) {
      const preferred = frame.locator('input[type="file"][accept*="video"], input[type="file"][accept*=".mp4"]')
      if (await preferred.count()) return preferred.first()
    }
    const candidates: Locator[] = []
    for (const frame of this.uploadFrames(page)) {
      const all = frame.locator('input[type="file"]')
      const count = await all.count()
      for (let index = 0; index < count; index += 1) candidates.push(all.nth(index))
    }
    return candidates.length === 1 ? candidates[0] : null
  }

  private async uploadTrigger(page: Page): Promise<Locator | null> {
    for (const frame of this.uploadFrames(page)) {
      const exact = frame.getByText('点击上传', { exact: true })
      const count = Math.min(await exact.count(), 10)
      for (let index = 0; index < count; index += 1) {
        const item = exact.nth(index)
        if (await item.isVisible().catch(() => false)) return item
      }
      const dropZone = frame.getByText(/将文件拖拽到此处/)
      if (await dropZone.first().isVisible().catch(() => false)) return dropZone.first()
    }
    return null
  }

  private uploadFrames(page: Page): Frame[] {
    const frames = page.frames()
    return [...frames].sort((left, right) => {
      const leftScore = /jinritemai\.com|oceanengine\.com/.test(left.url()) ? 0 : 1
      const rightScore = /jinritemai\.com|oceanengine\.com/.test(right.url()) ? 0 : 1
      return leftScore - rightScore
    })
  }

  private async decorateEntrancePage(entranceId: string): Promise<void> {
    const page = this.entrancePages.get(entranceId)
    const entrance = this.snapshot.entrances.find((item) => item.id === entranceId)
    if (!page || !entrance || page.isClosed()) return
    await this.decoratePage(page, entrance)
  }

  private async decoratePage(page: Page, entrance: QianchuanUploadEntrance): Promise<void> {
    await page.evaluate(({ color }) => {
      const root = document.documentElement
      if (root.dataset.outocutOriginalTitle) {
        document.title = root.dataset.outocutOriginalTitle
        delete root.dataset.outocutOriginalTitle
      }
      let marker = document.getElementById('outocut-entrance-marker')
      if (!marker) {
        marker = document.createElement('div')
        marker.id = 'outocut-entrance-marker'
        document.body.appendChild(marker)
      }
      marker.textContent = ''
      marker.setAttribute('aria-label', 'OutoCut 上传入口颜色标识')
      Object.assign(marker.style, {
        position: 'fixed',
        left: '18px',
        bottom: '18px',
        zIndex: '2147483647',
        width: '28px',
        height: '28px',
        padding: '0',
        border: '3px solid #ffffff',
        borderRadius: '50%',
        background: color,
        boxShadow: `0 5px 22px ${color}66`,
        pointerEvents: 'none',
      })
    }, {
      color: entrance.color,
    }).catch(() => undefined)
  }

  private async clearPageDecoration(page: Page): Promise<void> {
    await page.evaluate(() => {
      document.getElementById('outocut-entrance-marker')?.remove()
      const root = document.documentElement
      if (root.dataset.outocutOriginalTitle) {
        document.title = root.dataset.outocutOriginalTitle
        delete root.dataset.outocutOriginalTitle
      }
    }).catch(() => undefined)
  }

  private async hasVisibleProgress(page: Page): Promise<boolean> {
    const selectors = [
      '[role="progressbar"]',
      '[class*="upload"][class*="progress"]',
      '[class*="uploading"]',
      'text=/上传中|正在上传|处理中/',
    ]
    for (const selector of selectors) {
      const locator = page.locator(selector)
      const count = Math.min(await locator.count(), 20)
      for (let index = 0; index < count; index += 1) {
        const item = locator.nth(index)
        if (!(await item.isVisible().catch(() => false))) continue
        const complete = await item.evaluate((element) => {
          const now = Number(element.getAttribute('aria-valuenow'))
          const max = Number(element.getAttribute('aria-valuemax') || 100)
          if (Number.isFinite(now) && Number.isFinite(max) && max > 0 && now >= max) return true
          if (/100\s*%/.test(element.textContent || '')) return true
          const styled = [element, ...element.querySelectorAll<HTMLElement>('[style]')]
          return styled.some((child) => {
            const width = (child as HTMLElement).style?.width || ''
            const transform = (child as HTMLElement).style?.transform || ''
            return width === '100%' || /scaleX\(1(?:\.0+)?\)/.test(transform)
          })
        }).catch(() => false)
        if (!complete) return true
      }
    }
    return false
  }

  private async completedFileNames(page: Page, names: string[]): Promise<Set<string>> {
    const completed = new Set<string>()
    for (const frame of this.uploadFrames(page)) {
      for (const name of names) {
        if (completed.has(name)) continue
        const matches = frame.getByText(name, { exact: true })
        const count = Math.min(await matches.count(), 10)
        for (let index = 0; index < count; index += 1) {
          const item = matches.nth(index)
          if (!(await item.isVisible().catch(() => false))) continue
          const done = await item.evaluate((element, fileName) => {
            let node: Element | null = element
            for (let depth = 0; node && depth < 9; depth += 1, node = node.parentElement) {
              const text = node.textContent || ''
              if (!text.includes(fileName)) continue
              if (/上传成功|上传完成|处理完成/.test(text)) return true
              const markers = node.querySelectorAll<HTMLElement>(
                '[role="progressbar"], [class*="progress"], [class*="success"], [class*="check"], [class*="complete"], [aria-label*="成功"], [title*="成功"]',
              )
              for (const marker of markers) {
                if (/success|check|complete/i.test(marker.className) || /成功|完成/.test(marker.getAttribute('aria-label') || marker.getAttribute('title') || '')) return true
                const now = Number(marker.getAttribute('aria-valuenow'))
                const max = Number(marker.getAttribute('aria-valuemax') || 100)
                if (Number.isFinite(now) && Number.isFinite(max) && max > 0 && now >= max) return true
                if (/100\s*%/.test(marker.textContent || '')) return true
                const styled = [marker, ...marker.querySelectorAll<HTMLElement>('[style]')]
                if (styled.some((child) => child.style.width === '100%' || /scaleX\(1(?:\.0+)?\)/.test(child.style.transform))) return true
              }
            }
            return false
          }, name).catch(() => false)
          if (done) {
            completed.add(name)
            break
          }
        }
      }
    }
    return completed
  }

  private async visibleFailureText(page: Page): Promise<string> {
    const locator = page.locator('[role="alert"], [class*="error"], [class*="fail"]')
    const count = Math.min(await locator.count(), 30)
    const messages: string[] = []
    for (let index = 0; index < count; index += 1) {
      const item = locator.nth(index)
      if (!(await item.isVisible().catch(() => false))) continue
      const text = (await item.innerText().catch(() => '')).trim()
      if (text && /失败|错误|不支持|过大|超限|重试/.test(text)) messages.push(text)
    }
    return [...new Set(messages)].join('；')
  }

  private async visibleSuccessText(page: Page): Promise<string> {
    const locator = page.locator('[role="status"], [class*="success"], [class*="complete"]')
    const count = Math.min(await locator.count(), 30)
    const messages: string[] = []
    for (let index = 0; index < count; index += 1) {
      const item = locator.nth(index)
      if (!(await item.isVisible().catch(() => false))) continue
      const text = (await item.innerText().catch(() => '')).trim()
      if (text && /上传成功|上传完成|处理完成/.test(text)) messages.push(text)
    }
    return [...new Set(messages)].join('；')
  }

  private setState(state: QianchuanRunState, message: string): void {
    this.snapshot.state = state
    this.snapshot.message = message
    this.commit()
  }

  private recalculateTotalBatches(): void {
    this.snapshot.totalBatches = this.snapshot.entrances.reduce((total, entrance) => {
      const remaining = this.snapshot.files.filter((file) => (
        file.entranceId === entrance.id && this.fileMatchesMode(file) && (file.state === 'pending' || file.state === 'failed')
      )).length
      return total + Math.ceil(remaining / this.snapshot.config.batchSize)
    }, 0)
  }

  private commit(): void {
    this.snapshot.taskRunning = this.running
    this.snapshot.browserOpen = this.context !== null
    this.snapshot.updatedAt = new Date().toISOString()
    const file = this.stateFile()
    mkdirSync(join(app.getPath('userData'), 'qianchuan'), { recursive: true })
    writeFileSync(file, JSON.stringify({ ...this.snapshot, browserOpen: false }, null, 2), 'utf8')
    this.onStatus(this.getStatus())
  }

  private stateFile(): string {
    return join(app.getPath('userData'), 'qianchuan', 'upload-state.json')
  }

  private load(): QianchuanUploadSnapshot {
    try {
      const saved = JSON.parse(readFileSync(this.stateFile(), 'utf8')) as QianchuanUploadSnapshot
      const fallback = initialSnapshot()
      const files = Array.isArray(saved.files) ? saved.files.filter((file) => Boolean(file.entranceId)).map((file) => ({
        ...file,
        queueId: file.queueId || file.entranceId,
        state: file.state === 'uploading' ? 'failed' as const : file.state,
        error: file.state === 'uploading' ? '上次任务在上传中断，平台结果待人工确认' : file.error,
      })) : []
      return {
        ...fallback,
        ...saved,
        browserOpen: false,
        taskRunning: false,
        state: files.length ? 'idle' : 'idle',
        message: files.length ? '已恢复上次上传队列，请打开 Chrome 后继续' : fallback.message,
        config: { ...fallback.config, ...saved.config },
        files,
        sharedSourceDirectories: Array.isArray(saved.sharedSourceDirectories) ? saved.sharedSourceDirectories : [],
        entrances: Array.isArray(saved.entrances) ? saved.entrances.map((entrance, index) => ({
          ...entrance,
          state: 'unavailable' as const,
          currentBatch: 0,
          fileCount: 0,
          sourceDirectory: entrance.sourceDirectory || entrance.sourceDirectories?.[0] || '',
          sourceDirectories: Array.isArray(entrance.sourceDirectories) && entrance.sourceDirectories.length
            ? entrance.sourceDirectories
            : entrance.sourceDirectory ? [entrance.sourceDirectory] : [],
          totalFiles: files.filter((file) => file.entranceId === entrance.id).length,
          taskRunning: false,
          color: entrance.color || ENTRANCE_COLORS[index % ENTRANCE_COLORS.length],
          message: (entrance.sourceDirectories?.length || entrance.sourceDirectory) ? '等待重新识别 Chrome 上传入口' : '尚未选择素材文件夹',
        })) : [],
      }
    } catch {
      return initialSnapshot()
    }
  }
}
