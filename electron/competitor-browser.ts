import { app } from 'electron'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { chromium, type BrowserContext, type Page } from 'playwright-core'

export type CompetitorImageKind = 'main' | 'sku' | 'detail'

export interface CompetitorImage {
  id: string
  url: string
  dataUrl: string
  width: number
  height: number
  kind: CompetitorImageKind
  alt: string
}

export interface CompetitorBrowserStatus {
  browserOpen: boolean
  pageUrl: string
  title: string
}

interface DiscoveredImage {
  url: string
  width: number
  height: number
  alt: string
  context: string
  top: number
  index: number
}

function registryDefaultBrowser(): 'edge' | 'chrome' | null {
  try {
    const output = execFileSync('reg.exe', [
      'query',
      'HKCU\\Software\\Microsoft\\Windows\\Shell\\Associations\\UrlAssociations\\https\\UserChoice',
      '/v',
      'ProgId',
    ], { encoding: 'utf8', windowsHide: true })
    if (/MSEdgeHTM/i.test(output)) return 'edge'
    if (/ChromeHTML/i.test(output)) return 'chrome'
  } catch {
    // Fall back to an installed Chromium browser below.
  }
  return null
}

function browserExecutable(): string {
  const roots = {
    programFiles: process.env.PROGRAMFILES || 'C:\\Program Files',
    programFilesX86: process.env['PROGRAMFILES(X86)'] || 'C:\\Program Files (x86)',
    localAppData: process.env.LOCALAPPDATA || '',
  }
  const candidates = {
    edge: [
      join(roots.programFilesX86, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
      join(roots.programFiles, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    ],
    chrome: [
      join(roots.programFiles, 'Google', 'Chrome', 'Application', 'chrome.exe'),
      join(roots.programFilesX86, 'Google', 'Chrome', 'Application', 'chrome.exe'),
      join(roots.localAppData, 'Google', 'Chrome', 'Application', 'chrome.exe'),
    ],
  }
  const preferred = registryDefaultBrowser()
  const order = preferred ? [preferred, preferred === 'edge' ? 'chrome' : 'edge'] as const : ['chrome', 'edge'] as const
  for (const browser of order) {
    const executable = candidates[browser].find((candidate) => candidate && existsSync(candidate))
    if (executable) return executable
  }
  throw new Error('未找到可控制的 Chrome 或 Edge。请安装其中一个浏览器后重试。')
}

function normalizePageUrl(raw: string): string {
  const input = raw.trim()
  if (!input) throw new Error('请输入竞品商品链接')
  let parsed: URL
  try {
    parsed = new URL(input)
  } catch {
    throw new Error('竞品链接格式无效，请输入完整的 http/https 地址')
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('仅支持 http/https 商品链接')
  return parsed.href
}

function isAllowedRemoteImage(rawUrl: string): boolean {
  try {
    const parsed = new URL(rawUrl)
    const host = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, '')
    if (!['http:', 'https:'].includes(parsed.protocol)) return false
    if (host === 'localhost' || host === '::1' || host.endsWith('.localhost')) return false
    if (/^127\./.test(host) || /^10\./.test(host) || /^169\.254\./.test(host) || /^192\.168\./.test(host)) return false
    const private172 = /^172\.(\d{1,2})\./.exec(host)
    if (private172 && Number(private172[1]) >= 16 && Number(private172[1]) <= 31) return false
    return true
  } catch {
    return false
  }
}

function classifyImage(image: DiscoveredImage): CompetitorImageKind {
  const context = image.context.toLowerCase()
  const skuScore = /sku|颜色|colour|color|规格|属性|variant|variation|spec/.test(context) ? 8 : 0
  const detailScore = /detail|description|desc|详情|商品介绍|module|richtext/.test(context) ? 8 : 0
  const mainScore = /gallery|main|hero|preview|carousel|swiper|主图|展示图|thumbnail/.test(context) ? 6 : 0
  if (skuScore >= Math.max(detailScore, mainScore)) return 'sku'
  if (detailScore > mainScore || image.top > 1100 || image.height > image.width * 1.65) return 'detail'
  return 'main'
}

function mimeType(contentType: string, url: string): string | null {
  const value = contentType.split(';')[0].trim().toLowerCase()
  if (['image/png', 'image/jpeg', 'image/webp'].includes(value)) return value
  if (value) return null
  if (/\.png(?:$|\?)/i.test(url)) return 'image/png'
  if (/\.webp(?:$|\?)/i.test(url)) return 'image/webp'
  if (/\.jpe?g(?:$|\?)/i.test(url)) return 'image/jpeg'
  return null
}

export class CompetitorBrowser {
  private context: BrowserContext | null = null
  private page: Page | null = null

  async open(rawUrl: string): Promise<CompetitorBrowserStatus> {
    const url = normalizePageUrl(rawUrl)
    if (!this.context) {
      const profile = join(app.getPath('userData'), 'competitor-browser-profile')
      mkdirSync(profile, { recursive: true })
      this.context = await chromium.launchPersistentContext(profile, {
        executablePath: browserExecutable(),
        headless: false,
        viewport: null,
        args: ['--start-maximized'],
      })
      this.context.on('close', () => {
        this.context = null
        this.page = null
      })
    }
    this.page = this.page && !this.page.isClosed() ? this.page : this.context.pages()[0] || await this.context.newPage()
    await this.page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90_000 })
    await this.page.bringToFront()
    return this.status()
  }

  async status(): Promise<CompetitorBrowserStatus> {
    const page = this.page && !this.page.isClosed() ? this.page : null
    return {
      browserOpen: Boolean(this.context && page),
      pageUrl: page?.url() || '',
      title: page ? await page.title().catch(() => '') : '',
    }
  }

  async collect(rawUrl?: string): Promise<{ pageUrl: string; title: string; images: CompetitorImage[] }> {
    if (rawUrl?.trim()) await this.open(rawUrl)
    const page = this.page && !this.page.isClosed() ? this.page : null
    if (!page) throw new Error('请先打开竞品页面并完成登录')
    await page.waitForLoadState('domcontentloaded', { timeout: 30_000 }).catch(() => undefined)
    await page.evaluate(async () => {
      const distance = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)
      for (let y = 0; y < distance; y += Math.max(500, window.innerHeight * 0.75)) {
        window.scrollTo(0, y)
        await new Promise((resolve) => setTimeout(resolve, 120))
      }
      window.scrollTo(0, 0)
    })
    const discovered = await page.evaluate(() => {
      const absolute = (value: string) => {
        try { return new URL(value, document.baseURI).href } catch { return '' }
      }
      const candidates: DiscoveredImage[] = []
      const seen = new Set<string>()
      const add = (element: Element, rawUrl: string, width: number, height: number, alt: string, index: number) => {
        const url = absolute(rawUrl)
        if (!url || !/^https?:/i.test(url) || seen.has(url) || url.startsWith('data:')) return
        if (width && height && (width < 120 || height < 120)) return
        const ancestors: string[] = []
        let current: Element | null = element
        for (let depth = 0; current && depth < 5; depth += 1, current = current.parentElement) {
          ancestors.push(`${current.tagName}#${current.id}.${String(current.className || '')}`)
        }
        seen.add(url)
        candidates.push({
          url,
          width,
          height,
          alt: String(alt || '').slice(0, 240),
          context: ancestors.join(' '),
          top: element.getBoundingClientRect().top + window.scrollY,
          index,
        })
      }
      Array.from(document.images).forEach((image, index) => {
        const sources = [
          image.currentSrc,
          image.src,
          image.getAttribute('data-src'),
          image.getAttribute('data-original'),
          image.getAttribute('data-ks-lazyload'),
          image.getAttribute('data-lazy-src'),
        ].filter(Boolean) as string[]
        sources.forEach((source) => add(image, source, image.naturalWidth || image.width, image.naturalHeight || image.height, image.alt, index))
      })
      Array.from(document.querySelectorAll<HTMLElement>('[style*="background"]')).forEach((element, index) => {
        const background = getComputedStyle(element).backgroundImage
        const match = /url\(["']?([^"')]+)["']?\)/.exec(background)
        if (match) add(element, match[1], element.offsetWidth, element.offsetHeight, element.getAttribute('aria-label') || '', 10000 + index)
      })
      return candidates
    })
    const sorted = discovered
      .filter((item) => item.url.length < 8_000 && isAllowedRemoteImage(item.url))
      .sort((left, right) => left.index - right.index)
      .slice(0, 120)
    const images: CompetitorImage[] = []
    let totalBytes = 0
    for (const item of sorted) {
      if (totalBytes >= 90 * 1024 * 1024) break
      try {
        const response = await this.context!.request.get(item.url, { timeout: 30_000 })
        if (!response.ok()) continue
        const body = await response.body()
        if (!body.length || body.length > 12 * 1024 * 1024) continue
        const type = mimeType(response.headers()['content-type'] || '', item.url)
        if (!type) continue
        totalBytes += body.length
        images.push({
          id: `competitor-${images.length + 1}`,
          url: item.url,
          dataUrl: `data:${type};base64,${body.toString('base64')}`,
          width: item.width,
          height: item.height,
          kind: classifyImage(item),
          alt: item.alt,
        })
      } catch {
        // Individual CDN images may reject direct requests; continue with the rest.
      }
    }
    if (!images.length) throw new Error('当前页面未发现可用商品图。请确认已登录并停留在商品详情页后重试。')
    return { pageUrl: page.url(), title: await page.title().catch(() => ''), images }
  }

  async close(): Promise<void> {
    const context = this.context
    this.context = null
    this.page = null
    await context?.close().catch(() => undefined)
  }
}
