<script setup lang="ts">

import { computed, onMounted, reactive, ref, type CSSProperties } from 'vue'

import { ElMessage, ElMessageBox } from 'element-plus'

import { api, post, remove, ApiClientError } from '@/api'

import VoiceManagerDialog from '@/components/VoiceManagerDialog.vue'

import type { AppSettings, AssetCategory, AssetClip, MixTemplate, SubtitleSettings, TemplateShot, TitleSettings, VoiceProfile } from '@/types'



const categories = ref<AssetCategory[]>([])

const templates = ref<MixTemplate[]>([])

const settings = ref<AppSettings | null>(null)

const activeTemplateId = ref('')

const outputDirectory = ref('')

const creating = ref(false)

const taskCreateCount = ref(1)

const rewriting = ref(false)

const createDialogOpen = ref(false)

const extractingCopy = ref(false)

const creatingTemplate = ref(false)

const newStyle = ref('')

const musicEnabled = ref(true)

const musicDirectory = ref('')

const musicChoice = ref<string>('random')

const musicTracks = ref<string[]>([])

const voiceEnabled = ref(true)

const selectedVoice = ref('未选择音色')

const voiceManagerOpen = ref(false)

const voiceDialogOpen = ref(false)

const captionDialogOpen = ref(false)

const captionTab = ref<'title' | 'caption'>('title')

const fonts = ref<FontEntry[]>([])

const titleFolderFonts = ref<FontEntry[]>([])

const captionFolderFonts = ref<FontEntry[]>([])

const fontFaceCache = new Map<string, FontFace | null>()

const voiceDraft = reactive({ speed: 1, voiceVolume: 1, musicVolume: 0.18 })



function defaultCaptions(): SubtitleSettings {

  return { enabled: true, font_name: 'Microsoft YaHei', font_path: null, font_size: 52, primary_color: '#FFFFFF', outline_color: '#111111', outline_width: 3, margin_v: 180, animation: 'fade' }

}

function defaultTitleSettings(): TitleSettings {

  return { enabled: true, start_time: 0, end_time: 0, font_name: 'Microsoft YaHei', font_path: null, font_size: 83, primary_color: '#FFFFFF', centered: true, outline_width: 1, outline_color: '#111111', shadow_color: '#7A7070', shadow_x: 2, shadow_y: 2 }

}

const captionDraft = reactive({

  title: '',

  subtitle: '',

  titleSettings: defaultTitleSettings(),

  captions: defaultCaptions(),

})



const phonePreviewEl = ref<HTMLElement | null>(null)

const previewSize = ref({ width: 260, height: 462 })

let previewResizeObserver: ResizeObserver | null = null



function measurePreviewSize() {

  const el = phonePreviewEl.value

  if (!el) return

  const rect = el.getBoundingClientRect()

  if (rect.width > 0 && rect.height > 0) previewSize.value = { width: rect.width, height: rect.height }

}



function onPhonePreviewMounted(el: unknown) {

  phonePreviewEl.value = (el as HTMLElement | null)

  previewResizeObserver?.disconnect()

  previewResizeObserver = null

  if (!el) return

  measurePreviewSize()

  if (typeof ResizeObserver !== 'undefined') {

    previewResizeObserver = new ResizeObserver(() => measurePreviewSize())

    previewResizeObserver.observe(el as HTMLElement)

  }

}



function previewScale(): number {

  const outputWidth = template.output?.width || 1080

  const outputHeight = template.output?.height || 1920

  const size = previewSize.value

  if (size.height > 0 && outputHeight > 0) return size.height / outputHeight

  if (size.width > 0 && outputWidth > 0) return size.width / outputWidth

  return 260 / 1080

}



function strokeShadows(width: number, color: string): string {

  if (width <= 0) return ''

  const directions: ReadonlyArray<readonly [number, number]> = [

    [1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [-1, 1], [1, -1], [-1, -1],

  ]

  return directions.map(([dx, dy]) => `${dx * width}px ${dy * width}px 0 ${color}`).join(', ')

}



function titlePreviewStyle(): CSSProperties {

  const s = captionDraft.titleSettings

  const scale = previewScale()

  const shadow = [

    strokeShadows(s.outline_width * scale, s.outline_color),

    `${s.shadow_x * scale}px ${s.shadow_y * scale}px 0 ${s.shadow_color}`,

  ].filter(Boolean).join(', ')

  return {

    color: s.primary_color,

    fontFamily: `"${s.font_name}"`,

    fontSize: `${s.font_size * scale}px`,

    textShadow: shadow,

  }

}



function subtitlePreviewStyle(): CSSProperties {

  const c = captionDraft.captions

  const scale = previewScale()

  const outputHeight = template.output?.height || 1920

  return {

    color: c.primary_color,

    fontFamily: `"${c.font_name}"`,

    fontSize: `${c.font_size * scale}px`,

    textShadow: strokeShadows(c.outline_width * scale, c.outline_color),

    bottom: `${Math.min(60, Math.max(0, (c.margin_v / outputHeight) * 100))}%`,

  }

}



function titleSubtitlePreviewSize(): number {

  return Math.max(18, captionDraft.titleSettings.font_size - 22) * previewScale()

}



const defaultStyles = ['口语自然', '探店推荐', '强转化', '温柔种草', '专业介绍']

const customStyles = ref<string[]>(loadCustomStyles())

const styleOptions = computed(() => [...new Set([...defaultStyles, ...customStyles.value])])



function uid() { return crypto.randomUUID().replaceAll('-', '') }

function cloneSerializable<T>(value: T): T { return JSON.parse(JSON.stringify(value)) as T }

function newShot(): TemplateShot {

  return { id: uid(), original_text: '', rewritten_text: '', duration: 0, category_id: '', muted: false }

}

function newTemplate(): MixTemplate {

  return {

    id: uid(), name: '未命名模板', shot_count: 1, category_ids: [], default_shot_duration: 3,

    title: '', subtitle: '', background_music: null, background_music_enabled: true,

    background_music_directory: '', background_music_mode: 'random', background_music_volume: 0.18,

    voice_enabled: true, voice_id: null, voice_name: '', voice_speed: 1, voice_volume: 1,

    reference_text: '', copy_style: '专业介绍', shots: [newShot()],

    output: {

      width: settings.value?.default_width || 1080,

      height: settings.value?.default_height || 1920,

      fps: settings.value?.default_fps || 30,

      codec: settings.value?.default_codec || 'libx264',

      quality: settings.value?.default_quality || 23,

    },

    captions: defaultCaptions(),

    title_settings: defaultTitleSettings(),

  }

}



const template = reactive<MixTemplate>(newTemplate())

const createForm = reactive({

  name: '', shareText: '', extractedText: '', copyStyle: '专业介绍', shotCount: 6,

})



function loadCustomStyles(): string[] {

  try { return JSON.parse(localStorage.getItem('outocut-copy-styles') || '[]') as string[] }

  catch { return [] }

}



function normalizeTemplate(source: MixTemplate): MixTemplate {

  const normalized = cloneSerializable(source)

  normalized.reference_text ||= ''

  normalized.copy_style ||= '专业介绍'

  normalized.voice_speed ??= 1

  normalized.voice_volume ??= 1

  normalized.voice_enabled ??= true

  normalized.voice_name = normalized.voice_id ? (normalized.voice_name || normalized.voice_id) : ''

  normalized.background_music_enabled ??= true

  normalized.background_music_directory ||= ''

  normalized.background_music_mode ||= 'random'

  normalized.default_shot_duration = 3

  normalized.captions = { ...defaultCaptions(), ...(normalized.captions || {}) }

  normalized.title_settings = { ...defaultTitleSettings(), ...(normalized.title_settings || {}) }

  normalized.shots = normalized.shots?.length

    ? normalized.shots.map((shot) => ({ ...newShot(), ...shot }))

    : Array.from({ length: normalized.shot_count || 1 }, () => newShot())

  normalized.shot_count = normalized.shots.length

  normalized.category_ids = [...new Set(normalized.shots.map((shot) => shot.category_id).filter(Boolean))]

  return normalized

}



async function load() {

  ;[categories.value, templates.value, settings.value] = await Promise.all([

    api<AssetCategory[]>('/assets/categories'),

    api<MixTemplate[]>('/templates'),

    api<AppSettings>('/settings'),

  ])

  outputDirectory.value = settings.value?.output_directory || ''

  if (templates.value.length) selectTemplate(templates.value[0].id)

  else {

    Object.assign(template, newTemplate())

    applyTemplateToPanels()

  }

}



function selectTemplate(id: string) {

  const saved = templates.value.find((item) => item.id === id)

  if (!saved) return

  activeTemplateId.value = id

  Object.assign(template, normalizeTemplate(saved))

  applyTemplateToPanels()

}



async function applyTemplateToPanels() {

  musicEnabled.value = template.background_music_enabled

  musicDirectory.value = template.background_music_directory

  musicChoice.value =

    template.background_music_mode === 'fixed' && template.background_music

      ? template.background_music

      : 'random'

  musicTracks.value = await scanMusicTracks(template.background_music_directory)

  if (musicChoice.value !== 'random' && !musicTracks.value.includes(musicChoice.value)) {

    musicChoice.value = 'random'

  }

  voiceEnabled.value = template.voice_enabled

  selectedVoice.value = template.voice_name || '未选择音色'

}



function openCreateDialog() {

  Object.assign(createForm, { name: '', shareText: '', extractedText: '', copyStyle: '专业介绍', shotCount: 6 })

  createDialogOpen.value = true

}



async function extractCopy() {

  const value = createForm.shareText.trim()

  if (!value) return ElMessage.warning('请先粘贴抖音分享链接/口令，或直接输入文案')

  if (/https?:\/\//i.test(value) && /douyin\.com|iesdouyin\.com/i.test(value)) {

    extractingCopy.value = true

    try {

      const result = await post<{ content: string; source_url?: string }>('/links/douyin', { text: value })

      createForm.extractedText = result.content

      ElMessage.success('已从抖音链接提取口播文案')

    } catch (error) {

      if (error instanceof ApiClientError && error.code === 'needs_cookie') {

        await ElMessageBox.alert(

          error.message + '。\n请打开“系统设置 → 文案配置设置”，粘贴抖音网页版 Cookie 后重试',

          '需要抖音 Cookie',

          { type: 'warning', confirmButtonText: '知道了' },

        )

      } else if (error instanceof ApiClientError && error.code === 'needs_asr_key') {

        await ElMessageBox.alert(

          error.message + '。\n请打开“系统设置 → 文案配置设置”，填写阿里云百炼 API Key 后重试',

          '需要配置百炼 API Key',

          { type: 'warning', confirmButtonText: '知道了' },

        )

      } else if (error instanceof ApiClientError && error.code === 'asr_empty') {
        await ElMessageBox.alert(
          error.message,
          '????????',
          { type: 'warning', confirmButtonText: '???' },
        )
      } else if (error instanceof ApiClientError && error.code === 'local_model_missing') {

        await ElMessageBox.alert(

          error.message + '。\n请打开“系统设置 → 文案配置设置”，在“口播转写”中选择本地识别并下载模型',

          '需要下载本地识别模型',

          { type: 'warning', confirmButtonText: '知道了' },

        )

      } else if (error instanceof ApiClientError) {

        ElMessage.error(error.message)

      } else {

        ElMessage.error('链接解析失败，请稍后重试')

      }

    } finally {

      extractingCopy.value = false

    }

    return

  }

  createForm.extractedText = value

  ElMessage.success('已将文案放入提取文案区域')

}



function splitCopy(text: string, count: number): string[] {
  const units = splitTextUnits(text)
  if (units.length >= count) return distributeUnits(units, count)
  // 句子数不足时，优先在次级断点处拆分最长的单元，避免把一句话切断
  let parts = units
  while (parts.length < count) {
    const longestIndex = parts.reduce(
      (best, part, index) => (part.length > parts[best].length ? index : best),
      0,
    )
    const longest = parts[longestIndex]
    if (longest.length <= 10) break
    const cut = nearestBreakPoint(longest, Math.floor(longest.length / 2))
    const left = longest.slice(0, cut).trim()
    const right = longest.slice(cut).trim()
    if (!left || !right) break
    parts.splice(longestIndex, 1, left, right)
  }
  return distributeUnits(parts, count)
}

function splitTextUnits(text: string): string[] {
  // 按句末标点（、！？；等）与换行分句，保留原文所有标点，不做任何压缩或修改
  const units: string[] = []
  for (const line of text.split('\n')) {
    for (let sentence of line.match(/[^。！？!?；;]+[。！？!?；;]*/g) ?? []) {
      sentence = sentence.trim()
      if (!sentence) continue
      if (sentence.length > 50) {
        // 长句按逗号、顿号拆成更小的意群，保证镜头数量充足
        for (let piece of sentence.match(/[^，,、]+[，,、]?/g) ?? [sentence]) {
          piece = piece.trim()
          if (piece) units.push(piece)
        }
      } else {
        units.push(sentence)
      }
    }
  }
  return units
}

function nearestBreakPoint(text: string, around: number): number {
  const windowStart = Math.max(0, around - 10)
  const windowEnd = Math.min(text.length, around + 10)
  let best = around
  let bestDistance = Infinity
  for (let index = windowStart; index < windowEnd; index += 1) {
    if (/[\s，,、]/.test(text[index])) {
      const distance = Math.abs(index + 1 - around)
      if (distance < bestDistance) {
        bestDistance = distance
        best = index + 1
      }
    }
  }
  return Math.min(text.length, Math.max(1, best))
}

function distributeUnits(units: string[], count: number): string[] {
  const buckets: string[] = Array.from({ length: count }, () => '')
  const target = units.reduce((sum, unit) => sum + unit.length, 0) / count
  let bucket = 0
  let current = 0
  for (const unit of units) {
    // 当前桶超过均值后换桶，保证每个镜头文案长度尽量均衡且不切断句子
    if (bucket < count - 1 && current > 0 && current + unit.length > target) {
      bucket += 1
      current = 0
    }
    buckets[bucket] += unit
    current += unit.length
  }
  return buckets
}

async function generateTemplate() {

  if (!createForm.name.trim()) return ElMessage.warning('请填写模板名称')

  if (!createForm.extractedText.trim()) return ElMessage.warning('请把测试文案复制到提取文案区域')

  creatingTemplate.value = true

  let parts: string[]

  try {

    // 每次创作模板都优先用语言模型按镜头数量转写（忠实保留原文），失败时回退规则切分

    const result = await post<{ shots: string[] }>('/ai/rewrite', {

      reference_text: createForm.extractedText.trim(),

      shot_count: createForm.shotCount,

      extra_instructions: `严格保留原文措辞与顺序，不要改写、润色或增删内容，只需按语义自然划分为 ${createForm.shotCount} 段，每段对应一个镜头，保持口播连贯`,

      model: settings.value?.default_model || 'qwen-plus',

      faithful: true,

    })

    parts = result.shots

  } catch (error) {

    parts = splitCopy(createForm.extractedText, createForm.shotCount)

    const reason = error instanceof ApiClientError ? error.message : '未配置可用的 AI 转写服务'

    ElMessage.warning(`AI 转写不可用，已使用规则分片：${reason}`)

  } finally {

    creatingTemplate.value = false

  }

  if (parts.some((part) => !part.trim()))

    ElMessage.warning('文案较短，部分镜头未分配到文案，可在模板中编辑或删除空镜头')

  const next = newTemplate()

  next.name = createForm.name.trim()

  next.reference_text = createForm.extractedText.trim()

  next.copy_style = createForm.copyStyle

  next.shots = parts.map((text) => ({ ...newShot(), original_text: text, rewritten_text: text }))

  next.shot_count = next.shots.length

  await post<MixTemplate>('/templates', next)

  templates.value = await api<MixTemplate[]>('/templates')

  selectTemplate(next.id)

  createDialogOpen.value = false

  ElMessage.success('模板已生成并加入“我的模板”')

}



function addStyle() {

  const value = newStyle.value.trim()

  if (!value) return

  if (!styleOptions.value.includes(value)) customStyles.value.push(value)

  localStorage.setItem('outocut-copy-styles', JSON.stringify(customStyles.value))

  createForm.copyStyle = value

  newStyle.value = ''

}



async function saveTemplate() {

  applyPanelSettingsToTemplate()

  template.shot_count = template.shots.length

  template.category_ids = [...new Set(template.shots.map((shot) => shot.category_id).filter(Boolean))]

  await post<MixTemplate>('/templates', cloneSerializable(template))

  templates.value = await api<MixTemplate[]>('/templates')

  activeTemplateId.value = template.id

  ElMessage.success('模板已保存')

}



function applyPanelSettingsToTemplate() {

  template.background_music_enabled = musicEnabled.value

  template.background_music_directory = musicDirectory.value

  template.background_music_mode = musicChoice.value === 'random' ? 'random' : 'fixed'

  template.background_music = musicChoice.value === 'random' ? null : musicChoice.value

  template.voice_enabled = voiceEnabled.value

  template.voice_name = template.voice_id ? selectedVoice.value : ''

  template.default_shot_duration = 3

}



async function deleteTemplate(id: string) {

  await ElMessageBox.confirm('仅删除模板配置，不会删除任何素材文件。', '删除模板', { type: 'warning' })

  await remove(`/templates/${id}`)

  templates.value = await api<MixTemplate[]>('/templates')

  if (activeTemplateId.value === id) {

    activeTemplateId.value = ''

    Object.assign(template, newTemplate())

    if (templates.value.length) selectTemplate(templates.value[0].id)

    else applyTemplateToPanels()

  }

}



function addShot() {

  template.shots.push(newShot())

  template.shot_count = template.shots.length

}



function deleteShot(index: number) {

  if (template.shots.length === 1) return ElMessage.warning('模板至少需要一个镜头')

  template.shots.splice(index, 1)

  template.shot_count = template.shots.length

}



async function rewriteAll() {

  const source = template.reference_text || template.shots.map((shot) => shot.original_text).join('\n')

  if (!source.trim()) return ElMessage.warning('模板中没有可改写的原始文案')

  rewriting.value = true

  try {

    const result = await post<{ shots: string[] }>('/ai/rewrite', {

      reference_text: source,

      shot_count: template.shots.length,

      extra_instructions: [`文案风格：${template.copy_style}`, settings.value?.rewrite_instructions].filter(Boolean).join('\n'),

      model: settings.value?.default_model || 'qwen-plus',

    })

    template.shots.forEach((shot, index) => { shot.rewritten_text = result.shots[index] || shot.rewritten_text })

    ElMessage.success('镜头文案已改写')

  } finally { rewriting.value = false }

}



async function scanMusicTracks(directory: string): Promise<string[]> {

  if (!directory) return []

  const files = await window.outocut?.listAudioFiles(directory)

  return files || []

}



function musicTrackName(path: string): string {

  const normalized = path.replaceAll('\\', '/')

  return normalized.slice(normalized.lastIndexOf('/') + 1) || path

}



async function chooseMusicDirectory() {

  const path = await window.outocut?.selectDirectory()

  if (!path) return

  musicDirectory.value = path

  musicTracks.value = await scanMusicTracks(path)

  musicChoice.value = 'random'

}



function openVoiceSettings() {

  Object.assign(voiceDraft, {

    speed: template.voice_speed ?? 1,

    voiceVolume: template.voice_volume ?? 1,

    musicVolume: template.background_music_volume ?? 0.18,

  })

  voiceDialogOpen.value = true

}



function saveVoiceSettings() {

  template.voice_speed = voiceDraft.speed

  template.voice_volume = voiceDraft.voiceVolume

  template.background_music_volume = voiceDraft.musicVolume

  voiceDialogOpen.value = false

  ElMessage.success('声音配置已应用到当前模板')

}



function selectManagedVoice(voice: VoiceProfile) {

  template.voice_id = voice.voice_id

  template.voice_name = voice.voice_name

  selectedVoice.value = voice.voice_name

  voiceManagerOpen.value = false

}



async function ensureFonts() {

  if (fonts.value.length) return

  try {

    fonts.value = await api<FontEntry[]>('/fonts/list')

  } catch {

    fonts.value = []

  }

}



function fontOptions(target: 'title' | 'caption'): FontEntry[] {

  const folderFonts = target === 'title' ? titleFolderFonts.value : captionFolderFonts.value

  return folderFonts.length ? folderFonts : fonts.value

}



function fontLabel(font: FontEntry): string {

  const normalized = font.path.replaceAll('\\', '/')

  const fileName = normalized.slice(normalized.lastIndexOf('/') + 1)

  return font.name === fileName ? font.name : `${font.name}?${fileName}?`

}



async function registerFontFace(entry: FontEntry): Promise<void> {

  if (fontFaceCache.has(entry.path)) return

  try {

    const bytes = await window.outocut?.readFontBytes(entry.path)

    if (!bytes) return

    const face = new FontFace(entry.name, bytes)

    await face.load()

    document.fonts.add(face)

    fontFaceCache.set(entry.path, face)

  } catch {

    fontFaceCache.set(entry.path, null)

  }

}



async function refreshFolderFonts(target: 'title' | 'caption', directory: string): Promise<FontEntry[]> {

  let entries: FontEntry[] = []

  try {

    entries = await api<FontEntry[]>(`/fonts/list?directory=${encodeURIComponent(directory)}`)

  } catch {

    entries = []

  }

  if (target === 'title') titleFolderFonts.value = entries

  else captionFolderFonts.value = entries

  await Promise.all(entries.map((entry) => registerFontFace(entry)))

  return entries

}



async function onFontSelected(target: 'title' | 'caption', family: string): Promise<void> {

  const entry = fontOptions(target).find((font) => font.name === family)

  if (entry) await registerFontFace(entry)

}



function useDefaultFont(target: 'title' | 'caption') {

  if (target === 'title') {

    captionDraft.titleSettings.font_path = null

    titleFolderFonts.value = []

  } else {

    captionDraft.captions.font_path = null

    captionFolderFonts.value = []

  }

}



async function openCaptionSettings() {

  Object.assign(captionDraft, {

    title: template.title,

    subtitle: template.subtitle,

    titleSettings: cloneSerializable({ ...defaultTitleSettings(), ...template.title_settings }),

    captions: cloneSerializable({ ...defaultCaptions(), ...template.captions }),

  })

  captionTab.value = 'title'

  captionDialogOpen.value = true

  await ensureFonts()

  const titlePath = captionDraft.titleSettings.font_path

  const captionPath = captionDraft.captions.font_path

  await Promise.all([

    titlePath ? refreshFolderFonts('title', titlePath) : Promise.resolve([]),

    captionPath ? refreshFolderFonts('caption', captionPath) : Promise.resolve([]),

  ])

}



async function chooseFontDirectory(target: 'title' | 'caption') {

  const path = await window.outocut?.selectDirectory()

  if (!path) return

  if (target === 'title') captionDraft.titleSettings.font_path = path

  else captionDraft.captions.font_path = path

  const entries = await refreshFolderFonts(target, path)

  if (!entries.length) ElMessage.warning('????????????????.ttf/.otf/.ttc?')

}



function saveCaptionSettings() {

  template.title = captionDraft.title

  template.subtitle = captionDraft.subtitle

  template.title_settings = cloneSerializable(captionDraft.titleSettings)

  template.captions = cloneSerializable(captionDraft.captions)

  captionDialogOpen.value = false

  ElMessage.success('标题与字幕配置已应用到当前模板')

}



async function createJob() {

  if (!template.shots.length) return ElMessage.warning('请先添加镜头')

  const missingCategory = template.shots.findIndex((shot) => !shot.category_id)

  if (missingCategory >= 0) return ElMessage.warning(`请为镜头 ${missingCategory + 1} 选择素材文件夹`)

  if (!outputDirectory.value) {

    const path = await window.outocut?.selectDirectory()

    if (!path) return ElMessage.warning('请选择成片目录')

    outputDirectory.value = path

  }

  if (template.shots.some((shot) => !(shot.rewritten_text || shot.original_text).trim())) {

    return ElMessage.warning('每个镜头都需要填写文案')

  }

  if (voiceEnabled.value && !settings.value?.configured.minimax) {
    return ElMessage.warning('智能配音已开启，请先在系统设置中配置 MiniMax Key')
  }

  if (voiceEnabled.value && !template.voice_id) {
    return ElMessage.warning('智能配音已开启，请先在音色管理中选择真实 MiniMax 音色')
  }

  creating.value = true

  try {

    applyPanelSettingsToTemplate()

    const assets = await api<AssetClip[]>('/assets')

    const categoryIds = [...new Set(template.shots.map((shot) => shot.category_id))]

    const usableAssets = assets.filter((clip) => categoryIds.includes(clip.category_id) && ['none', 'hdr'].includes(clip.issue))

    const assetGroups = categoryIds.map((categoryId) => ({

      category_id: categoryId,

      name: categories.value.find((item) => item.id === categoryId)?.name || categoryId,

      asset_paths: usableAssets.filter((clip) => clip.category_id === categoryId).map((clip) => clip.path),

      asset_durations: Object.fromEntries(

        usableAssets.filter((clip) => clip.category_id === categoryId).map((clip) => [clip.path, clip.duration]),

      ),

    }))

    const emptyGroup = assetGroups.find((group) => !group.asset_paths.length)

    if (emptyGroup) {

      const shotNumbers = template.shots

        .map((shot, index) => shot.category_id === emptyGroup.category_id ? index + 1 : 0)

        .filter(Boolean)

        .join('、')

      await ElMessageBox.alert(

        `镜头 ${shotNumbers} 选择的素材文件夹“${emptyGroup.name}”中没有时长达到3秒的可用视频，任务未创建。请补充合格素材后重新扫描。`,

        '素材时长不足',

        { type: 'warning', confirmButtonText: '知道了' },

      )

      return

    }

    template.shot_count = template.shots.length

    template.category_ids = categoryIds

    const quantity = taskCreateCount.value

    const baseSeed = Date.now() % 2147483647

    const payload = {

      name: `${template.name}-${new Date().toLocaleString('zh-CN', { hour12: false }).replaceAll('/', '-')}`,

      template: cloneSerializable(template),

      asset_paths: usableAssets.map((clip) => clip.path),

      asset_groups: assetGroups,

      shots: template.shots.map((shot) => ({

        text: shot.rewritten_text || shot.original_text,

        clip_path: null,

        voice_path: null,

        category_id: shot.category_id,

        muted: shot.muted,

      })),

      output_dir: outputDirectory.value,

      cache_dir: settings.value?.cache_directory || '',

      seed: baseSeed,

      count: 1,

    }

    for (let index = 0; index < quantity; index += 1) {

      await post('/jobs', { ...payload, seed: (baseSeed + index) % 2147483647 })

    }

    ElMessage.success(`已创建 ${quantity} 个任务，请在任务中心点击“合成”或“全部开始”`)

  } finally { creating.value = false }

}



onMounted(load)

</script>



<template>

  <div class="editor-page">

    <div class="asset-tip">从“我的模板”选择或创建模板，在中间逐个编辑镜头文案并为每个镜头指定素材文件夹。生成任务时，每个镜头只会从其指定的文件夹中随机选取视频。</div>



    <div class="editor-workspace">

      <aside class="panel template-library">

        <div class="panel-header">

          <div><h2>我的模板</h2><p>共 {{ templates.length }} 个自定义模板</p></div>

          <el-button type="primary" @click="openCreateDialog">＋ 创作模板</el-button>

        </div>

        <div v-if="!templates.length" class="empty-state compact">还没有模板<br>点击“创作模板”开始</div>

        <div v-else class="template-card-list">

          <button

            v-for="item in templates"

            :key="item.id"

            class="template-card"

            :class="{ active: activeTemplateId === item.id }"

            @click="selectTemplate(item.id)"

          >

            <span><strong>{{ item.name }}</strong><small>{{ item.shot_count }} 个镜头 · {{ item.copy_style || '自定义' }}</small></span>

            <el-button class="editor-delete-button" size="small" type="danger" plain @click.stop="deleteTemplate(item.id)">删除</el-button>

          </button>

        </div>

      </aside>



      <main class="panel template-editor">

        <div class="panel-header">

          <div><h2>模板创作区</h2><p>逐句编辑文案，并按镜头选择素材文件夹</p></div>

          <div class="toolbar">

            <el-button :loading="rewriting" @click="rewriteAll">一键改写文案</el-button>

            <el-button type="primary" @click="saveTemplate">保存模板</el-button>

          </div>

        </div>

        <div class="template-meta-row">

          <el-input v-model="template.name" placeholder="模板名称" />

          <el-select v-model="template.copy_style" placeholder="文案风格"><el-option v-for="style in styleOptions" :key="style" :label="style" :value="style" /></el-select>

        </div>



        <div class="editor-shot-list">

          <article v-for="(shot, index) in template.shots" :key="shot.id" class="editor-shot-card">

            <header><b>镜头 {{ index + 1 }}</b><el-button class="editor-delete-button" size="small" type="danger" plain @click="deleteShot(index)">删除</el-button></header>

            <div class="shot-copy-grid">

              <el-form-item label="模板原句"><el-input v-model="shot.original_text" type="textarea" :rows="3" placeholder="填写本句模板原文" /></el-form-item>

              <el-form-item label="改写文案"><el-input v-model="shot.rewritten_text" type="textarea" :rows="3" placeholder="编辑当前镜头口播文案" /></el-form-item>

            </div>

            <div class="shot-settings-row">

              <el-form-item label="素材文件夹" class="shot-category-select">

                <el-select v-model="shot.category_id" filterable placeholder="选择子文件夹名称">

                  <el-option v-for="category in categories" :key="category.id" :value="category.id" :label="`${category.name} · ${category.clip_count} 个片段`" />

                </el-select>

              </el-form-item>

              <el-form-item label="静音"><el-switch v-model="shot.muted" /></el-form-item>

            </div>

          </article>

        </div>

        <div class="editor-actions"><el-button round @click="addShot">＋ 添加镜头</el-button></div>

      </main>



      <aside class="editor-side-stack">

        <section class="panel editor-config-panel">

          <div class="config-section">

            <h2>背景音乐</h2>

            <div class="config-switch-row">

              <el-switch v-model="musicEnabled" />

              <span>开启后使用下方文件夹中的音频；可选择固定曲目或随机一条</span>

            </div>

            <el-input v-model="musicDirectory" readonly placeholder="背景音乐文件夹" :disabled="!musicEnabled" />

            <div class="music-source-row">

              <el-button class="music-directory-button" type="primary" plain :disabled="!musicEnabled" @click="chooseMusicDirectory">选择本地文件夹</el-button>

              <span>曲目</span>

            </div>

                        <el-select v-model="musicChoice" :disabled="!musicEnabled" placeholder="选择曲目">

              <el-option label="随机" value="random" />

              <el-option v-for="track in musicTracks" :key="track" :label="musicTrackName(track)" :value="track" />

            </el-select>

          </div>



          <div class="config-section">

            <h2>智能配音</h2>

            <div class="config-switch-row">

              <el-switch v-model="voiceEnabled" />

              <span>自定义模板可关闭配音</span>

              <span>音色</span>

            </div>

            <div class="editor-voice-choice" :class="{ disabled: !voiceEnabled }">

              <div><strong>{{ selectedVoice }}</strong><code>{{ template.voice_id || '尚未选择真实 voice_id' }}</code></div>

              <el-button type="primary" plain :disabled="!voiceEnabled" @click="voiceManagerOpen = true">选择音色</el-button>

            </div>

          </div>



          <div class="config-section">

            <h2>声音配置</h2>

            <el-button type="primary" plain @click="openVoiceSettings">声音配置</el-button>

          </div>



          <div class="config-section">

            <h2>标题/字幕</h2>

            <el-button type="primary" plain @click="openCaptionSettings">顶部标题及字幕设置</el-button>

          </div>

        </section>

        <div class="create-task-controls">

          <label class="task-count-row"><span>创建数量</span><el-input-number v-model="taskCreateCount" :min="1" :max="100" :disabled="creating" /></label>

          <el-button class="create-task-button" type="primary" size="large" :loading="creating" @click="createJob">创建任务</el-button>

        </div>

      </aside>

    </div>

  </div>



  <el-dialog v-model="createDialogOpen" title="生成分镜头混剪文案" width="760px" destroy-on-close>

    <div class="dialog-tip">粘贴测试文案，生成一套可以继续编辑、并能保存到“我的模板”的分镜结构。</div>

    <el-form label-position="top" class="create-template-form">

      <el-form-item label="模板名称" required><el-input v-model="createForm.name" maxlength="100" show-word-limit placeholder="例如：产品包装展示模板" /></el-form-item>

      <el-form-item label="抖音链接或分享口令">

        <div class="extract-row"><el-input v-model="createForm.shareText" maxlength="500" show-word-limit placeholder="粘贴抖音分享链接/口令（可选），也可直接输入口播文案" /><el-button type="primary" plain :loading="extractingCopy" @click="extractCopy">提取文案</el-button></div>

      </el-form-item>

      <el-form-item label="提取文案" required><el-input v-model="createForm.extractedText" type="textarea" :rows="6" maxlength="5000" show-word-limit placeholder="提取到的口播文案将显示在这里；也可直接输入（必填）" /></el-form-item>

      <div class="dialog-form-grid">

        <el-form-item label="文案风格">

          <el-select v-model="createForm.copyStyle">

            <el-option v-for="style in styleOptions" :key="style" :label="style" :value="style" />

            <template #footer><div class="style-add-row"><el-input v-model="newStyle" size="small" placeholder="添加自定义风格" @keyup.enter="addStyle" /><el-button size="small" @click="addStyle">添加</el-button></div></template>

          </el-select>

        </el-form-item>

        <el-form-item label="镜头数量" required><el-input-number v-model="createForm.shotCount" :min="1" :max="100" /></el-form-item>

      </div>

    </el-form>

    <template #footer><el-button @click="createDialogOpen = false">取消</el-button><el-button type="primary" :loading="creatingTemplate" @click="generateTemplate">生成模板</el-button></template>

  </el-dialog>



  <el-dialog v-model="voiceDialogOpen" title="声音配置" width="520px" class="voice-settings-dialog" destroy-on-close>

    <div class="dialog-tip">调整当前模板的配音节奏和混音音量，创建任务时会随模板快照保存。</div>

    <div class="voice-setting-list">

      <div class="slider-setting">

        <div><b>语速</b><span>{{ voiceDraft.speed.toFixed(1) }}</span></div>

        <el-slider v-model="voiceDraft.speed" :min="0.5" :max="2" :step="0.1" :show-tooltip="false" />

      </div>

      <div class="slider-setting">

        <div><b>人声音量</b><span>{{ voiceDraft.voiceVolume.toFixed(1) }}</span></div>

        <el-slider v-model="voiceDraft.voiceVolume" :min="0" :max="2" :step="0.1" :show-tooltip="false" />

      </div>

      <div class="slider-setting">

        <div><b>背景音乐音量</b><span>{{ voiceDraft.musicVolume.toFixed(1) }}</span></div>

        <el-slider v-model="voiceDraft.musicVolume" :min="0" :max="1" :step="0.1" :show-tooltip="false" />

      </div>

    </div>

    <template #footer><el-button @click="voiceDialogOpen = false">取消</el-button><el-button type="primary" @click="saveVoiceSettings">确定</el-button></template>

  </el-dialog>



  <VoiceManagerDialog v-model="voiceManagerOpen" :selected-voice-id="template.voice_id" @select="selectManagedVoice" />



  <el-dialog v-model="captionDialogOpen" title="顶部标题及字幕设置" width="1080px" class="caption-settings-dialog" destroy-on-close>

    <div class="caption-dialog-layout">

      <el-tabs v-model="captionTab" class="caption-tabs">

        <el-tab-pane label="顶部标题" name="title">

          <div class="subtitle-switch-card">

            <div><b>顶部标题显示</b><p>控制当前模板是否显示主标题和副标题</p></div>

            <el-switch v-model="captionDraft.titleSettings.enabled" />

          </div>

          <section class="settings-block">

            <h3>基础信息</h3>

            <div class="caption-form-grid title-info-grid">

              <label>主标题</label><el-input v-model="captionDraft.title" placeholder="请输入主标题" />

              <label>副标题</label><el-input v-model="captionDraft.subtitle" placeholder="可选；样式跟随主标题" />

              <label>显示时间</label>

              <div class="time-range-row">

                <el-input-number v-model="captionDraft.titleSettings.start_time" :min="0" :max="3600" :step="0.5" :precision="1" />

                <span>秒 至</span>

                <el-input-number v-model="captionDraft.titleSettings.end_time" :min="0" :max="3600" :step="0.5" :precision="1" />

                <span>秒，结束为 0 时显示到视频结束</span>

              </div>

            </div>

          </section>

          <section class="settings-block">

            <h3>字体与样式</h3>

            <div class="caption-form-grid style-form-grid">

              <label>字体目录</label>

              <div class="font-directory-row">

                <el-input :model-value="captionDraft.titleSettings.font_path || '默认字体文件夹'" readonly />

                <el-button @click="useDefaultFont('title')">使用默认字体</el-button>

                <el-button @click="chooseFontDirectory('title')">选择本地字体文件夹</el-button>

              </div>

              <label>字体</label>

              <el-select v-model="captionDraft.titleSettings.font_name" filterable allow-create default-first-option @change="onFontSelected('title', $event)">

                <el-option v-for="font in fontOptions('title')" :key="`${font.path}|${font.name}`" :label="fontLabel(font)" :value="font.name" />

              </el-select>

              <label>字号</label><el-input-number v-model="captionDraft.titleSettings.font_size" :min="18" :max="160" />

              <label>字体颜色</label><el-color-picker v-model="captionDraft.titleSettings.primary_color" />

              <label>居中</label><el-switch v-model="captionDraft.titleSettings.centered" />

              <label>描边宽度</label><el-input-number v-model="captionDraft.titleSettings.outline_width" :min="0" :max="20" />

              <label>描边颜色</label><el-color-picker v-model="captionDraft.titleSettings.outline_color" />

              <label>阴影颜色</label><el-color-picker v-model="captionDraft.titleSettings.shadow_color" />

              <label>阴影 X 偏移</label><el-input-number v-model="captionDraft.titleSettings.shadow_x" :min="-30" :max="30" />

              <label>阴影 Y 偏移</label><el-input-number v-model="captionDraft.titleSettings.shadow_y" :min="-30" :max="30" />

            </div>

          </section>

        </el-tab-pane>



        <el-tab-pane label="字幕显示" name="caption">

          <div class="subtitle-switch-card">

            <div><b>字幕显示</b><p>智能配音时在视频中显示逐句字幕</p></div>

            <el-switch v-model="captionDraft.captions.enabled" />

          </div>

          <section class="settings-block">

            <h3>基础设置</h3>

            <div class="caption-form-grid style-form-grid">

              <label>字体</label>

              <el-select v-model="captionDraft.captions.font_name" filterable allow-create default-first-option @change="onFontSelected('caption', $event)">

                <el-option v-for="font in fontOptions('caption')" :key="`${font.path}|${font.name}`" :label="fontLabel(font)" :value="font.name" />

              </el-select>

              <label>字体目录</label>

              <div class="font-directory-row">

                <el-input :model-value="captionDraft.captions.font_path || '默认字体文件夹'" readonly />

                <el-button @click="useDefaultFont('caption')">使用默认字体</el-button>

                <el-button @click="chooseFontDirectory('caption')">选择本地字体文件夹</el-button>

              </div>

              <label>字号</label><el-input-number v-model="captionDraft.captions.font_size" :min="18" :max="120" />

              <label>字幕颜色</label><el-color-picker v-model="captionDraft.captions.primary_color" />

            </div>

          </section>

          <section class="settings-block">

            <h3>样式与动效</h3>

            <div class="caption-form-grid style-form-grid">

              <label>描边宽度</label><el-input-number v-model="captionDraft.captions.outline_width" :min="0" :max="20" />

              <label>描边颜色</label><el-color-picker v-model="captionDraft.captions.outline_color" />

              <label>下边距</label><el-input-number v-model="captionDraft.captions.margin_v" :min="0" :max="1000" />

              <label>字幕动效</label>

              <el-select v-model="captionDraft.captions.animation"><el-option label="渐入弹出" value="fade" /><el-option label="无动效" value="none" /></el-select>

            </div>

          </section>

        </el-tab-pane>

      </el-tabs>



      <aside class="caption-preview-column">

        <b>预览示意</b>

        <div ref="onPhonePreviewMounted" class="caption-phone-preview">

          <div v-if="captionTab === 'title' && captionDraft.titleSettings.enabled" class="title-preview" :class="{ centered: captionDraft.titleSettings.centered }" :style="titlePreviewStyle()">

            <strong>{{ captionDraft.title || '顶部标题预览' }}</strong><small v-if="captionDraft.subtitle" :style="{ fontSize: `${titleSubtitlePreviewSize()}px` }">{{ captionDraft.subtitle }}</small>

          </div>

          <div v-else-if="captionDraft.captions.enabled" class="subtitle-preview" :style="subtitlePreviewStyle()">字幕样式预览</div>

          <p>标题与字幕位置为近似预览<br>最终效果以成片为准</p>

        </div>

      </aside>

    </div>

    <template #footer><el-button @click="captionDialogOpen = false">取消</el-button><el-button type="primary" @click="saveCaptionSettings">确定</el-button></template>

  </el-dialog>

</template>

