<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, post, remove } from '@/api'
import type { AppSettings } from '@/types'

defineOptions({ name: 'OverlayView' })

type OverlayPosition = 'top_left' | 'top_right' | 'center' | 'bottom_left' | 'bottom_right'
type FileStatus = 'pending' | 'processing' | 'succeeded' | 'failed'

interface MediaBatchTask {
  id: string
  state: FileStatus
  payload: Record<string, any>
  output_path?: string | null
  error?: string | null
}

interface MediaBatch {
  id: string
  kind: 'overlay'
  state: 'running' | 'paused' | 'completed'
  tasks: MediaBatchTask[]
}

interface BatchVideo {
  path: string
  name: string
  status: FileStatus
  outputPath: string
  error: string
  stickerPosition?: OverlayPosition
  stickerPath?: string
  stickerSourceFolder?: string
  taskId?: string
}

const videos = ref<BatchVideo[]>([])
const outputDirectory = ref('')
const processing = ref(false)
const stopping = ref(false)
const batchPaused = ref(false)
const configPanel = ref<HTMLElement | null>(null)
const filePanelHeader = ref<HTMLElement | null>(null)
const configPanelHeight = ref(0)
const filePanelHeaderHeight = ref(0)
let configPanelObserver: ResizeObserver | null = null
let filePanelHeaderObserver: ResizeObserver | null = null
let stopRequested = false
let activeBatchId = ''
let pollTimer: number | null = null
const enabled = reactive({ watermark: true, sticker: false, pixel: false })
const stickerRandom = ref(false)
const stickerRandomSource = ref(false)
const stickerFolder = ref('')
const watermark = reactive({ path: 'D:/watermark/watermark/布素朵水印.gif', opacity: 0.5 })
const sticker = reactive({ path: 'D:/watermark/stickers/贴纸.png', opacity: 0.8, scale: 0.5 })
const pixel = reactive({ path: 'D:/watermark/stickers/防搬运动态图.gif', opacity: 0.5 })
const stickerPositions = reactive<Record<OverlayPosition, boolean>>({
  top_left: false,
  top_right: false,
  center: false,
  bottom_left: false,
  bottom_right: true,
})
const completedCount = computed(() => videos.value.filter((item) => item.status === 'succeeded').length)
const pendingCount = computed(() => videos.value.filter((item) => item.status === 'pending').length)
const fileTableMaxHeight = computed(() => Math.max(180, configPanelHeight.value - filePanelHeaderHeight.value))
const statusLabels: Record<string, string> = { pending: '待处理', processing: '处理中', succeeded: '已完成', failed: '失败' }

function restoreOverlaySettings() {
  try {
    const saved = JSON.parse(localStorage.getItem('outocut-overlay-settings') || 'null')
    if (!saved) return
    Object.assign(enabled, saved.enabled || {})
    Object.assign(watermark, saved.watermark || {})
    Object.assign(sticker, saved.sticker || {})
    Object.assign(pixel, saved.pixel || {})
    Object.assign(stickerPositions, saved.stickerPositions || {})
    stickerRandom.value = Boolean(saved.stickerRandom)
    stickerRandomSource.value = Boolean(saved.stickerRandomSource)
    stickerFolder.value = typeof saved.stickerFolder === 'string' ? saved.stickerFolder : ''
    outputDirectory.value = saved.outputDirectory || ''
  } catch {
    // Invalid legacy settings fall back to the built-in defaults.
  }
}

function persistBatchState() {
  localStorage.setItem('outocut-overlay-batch', JSON.stringify({
    paused: batchPaused.value,
    videos: videos.value,
  }))
}

function restoreBatchState() {
  try {
    const saved = JSON.parse(localStorage.getItem('outocut-overlay-batch') || 'null')
    if (!saved || !Array.isArray(saved.videos)) return
    let interrupted = false
    videos.value = saved.videos
      .filter((item: Partial<BatchVideo>) => typeof item.path === 'string' && item.path)
      .map((item: Partial<BatchVideo>) => {
        const status = item.status === 'processing' ? 'pending' : item.status
        if (item.status === 'processing') interrupted = true
        return {
          path: item.path as string,
          name: typeof item.name === 'string' && item.name ? item.name : fileName(item.path as string),
          status: ['pending', 'succeeded', 'failed'].includes(status || '') ? status as FileStatus : 'pending',
          outputPath: typeof item.outputPath === 'string' ? item.outputPath : '',
          error: typeof item.error === 'string' ? item.error : '',
          stickerPosition: ['top_left', 'top_right', 'center', 'bottom_left', 'bottom_right'].includes(item.stickerPosition || '')
            ? item.stickerPosition as OverlayPosition
            : undefined,
          stickerPath: typeof item.stickerPath === 'string' ? item.stickerPath : undefined,
          stickerSourceFolder: typeof item.stickerSourceFolder === 'string' ? item.stickerSourceFolder : undefined,
        }
      })
    batchPaused.value = Boolean(saved.paused) || interrupted
  } catch {
    // Invalid legacy queue data is ignored.
  }
}

restoreOverlaySettings()
restoreBatchState()
watch(
  [enabled, watermark, sticker, pixel, stickerPositions, stickerRandom, stickerRandomSource, stickerFolder, outputDirectory],
  () => localStorage.setItem('outocut-overlay-settings', JSON.stringify({
    enabled,
    watermark,
    sticker,
    pixel,
    stickerPositions,
    stickerRandom: stickerRandom.value,
    stickerRandomSource: stickerRandomSource.value,
    stickerFolder: stickerFolder.value,
    outputDirectory: outputDirectory.value,
  })),
  { deep: true },
)
watch([videos, batchPaused], persistBatchState, { deep: true })

function fileName(path: string) {
  return path.split(/[\\/]/).pop() || path
}

async function chooseVideos() {
  const paths = await window.outocut?.selectFiles([
    { name: '视频文件', extensions: ['mp4', 'mov', 'mkv', 'avi', 'webm', 'm4v', 'flv', 'ts', 'mts', 'm2ts', 'wmv', 'mpg', 'mpeg', '3gp'] },
  ]) || []
  const existing = new Set(videos.value.map((item) => item.path.toLowerCase()))
  for (const path of paths) {
    if (existing.has(path.toLowerCase())) continue
    videos.value.push({ path, name: fileName(path), status: 'pending', outputPath: '', error: '' })
    existing.add(path.toLowerCase())
  }
}

async function chooseOverlay(target: 'watermark' | 'sticker' | 'pixel') {
  const filters = target === 'sticker'
    ? [{ name: '静态图片', extensions: ['png', 'jpg', 'jpeg', 'webp', 'bmp'] }]
    : [{ name: 'GIF 动图', extensions: ['gif'] }]
  const path = await window.outocut?.selectFile(filters)
  if (!path) return
  if (target === 'watermark') watermark.path = path
  else if (target === 'sticker') sticker.path = path
  else pixel.path = path
}

async function chooseOutputDirectory() {
  const path = await window.outocut?.selectDirectory()
  if (path) outputDirectory.value = path
}

async function chooseStickerFolder() {
  const path = await window.outocut?.selectDirectory()
  if (path) stickerFolder.value = path
}

async function removeVideo(index: number) {
  if (processing.value) return
  const taskId = videos.value[index]?.taskId
  if (activeBatchId && taskId) {
    await remove(`/media-batches/${activeBatchId}/tasks/${taskId}`)
  }
  videos.value.splice(index, 1)
}

async function clearList() {
  if (!processing.value) {
    if (activeBatchId) {
      await remove(`/media-batches/${activeBatchId}`)
      activeBatchId = ''
    }
    videos.value = []
    batchPaused.value = false
  }
}

async function stopBatch(showMessage = true) {
  if (!processing.value || stopRequested || !activeBatchId) return
  stopRequested = true
  stopping.value = true
  await post<MediaBatch>(`/media-batches/${activeBatchId}/pause`)
  await refreshBatch(activeBatchId)
  if (showMessage) ElMessage.info('正在中止当前转换，请稍候')
}

function applyBatch(batch: MediaBatch) {
  activeBatchId = batch.id
  processing.value = batch.state === 'running'
  batchPaused.value = batch.state === 'paused'
  stopping.value = false
  stopRequested = false
  videos.value = batch.tasks.map((task) => ({
    path: String(task.payload.video_path || ''),
    name: fileName(String(task.payload.video_path || '')),
    status: task.state,
    outputPath: task.output_path || '',
    error: task.error || '',
    stickerPosition: task.payload.sticker?.positions?.length === 1
      ? task.payload.sticker.positions[0] as OverlayPosition
      : undefined,
    stickerPath: task.payload.sticker?.path,
    taskId: task.id,
  }))
}

async function refreshBatch(batchId = activeBatchId) {
  if (!batchId) return
  try {
    const batch = await api<MediaBatch>(`/media-batches/${batchId}`)
    applyBatch(batch)
    if (batch.state === 'completed' && pollTimer !== null) {
      window.clearInterval(pollTimer)
      pollTimer = null
    }
  } catch {
    // Keep the last rendered state during a transient engine reconnect.
  }
}

function ensurePolling() {
  if (pollTimer === null) pollTimer = window.setInterval(() => void refreshBatch(), 600)
}

async function startBatch() {
  if (batchPaused.value && activeBatchId) {
    applyBatch(await post<MediaBatch>(`/media-batches/${activeBatchId}/resume`))
    ensurePolling()
    return
  }
  if (!videos.value.length) return ElMessage.warning('请先批量选择视频文件')
  if (!pendingCount.value) return ElMessage.warning('没有待转换的视频')
  if (!enabled.watermark && !enabled.sticker && !enabled.pixel) return ElMessage.warning('请至少开启一种处理方式')
  if (enabled.watermark && !watermark.path) return ElMessage.warning('请选择水印 GIF 图')
  let stickerFiles: string[] = []
  if (enabled.sticker && stickerRandomSource.value) {
    if (!stickerFolder.value) return ElMessage.warning('请选择随机贴纸文件夹')
    stickerFiles = await window.outocut?.listImageFiles(stickerFolder.value) || []
    if (!stickerFiles.length) return ElMessage.warning('所选文件夹中没有 PNG、JPG 或 JPEG 贴纸')
  } else if (enabled.sticker && !sticker.path) {
    return ElMessage.warning('请选择贴纸图片')
  }
  if (enabled.pixel && !pixel.path) return ElMessage.warning('请选择像素点 GIF 图')
  const selectedPositions = (Object.entries(stickerPositions) as Array<[OverlayPosition, boolean]>)
    .filter(([, selected]) => selected)
    .map(([position]) => position)
  if (enabled.sticker && !stickerRandom.value && !selectedPositions.length) return ElMessage.warning('请至少开启一个贴纸显示位置')
  if (!outputDirectory.value) return ElMessage.warning('请选择输出目录')
  const items: Record<string, any>[] = []
  for (const item of videos.value) {
    if (item.status !== 'pending') continue
    const randomPositions: OverlayPosition[] = ['top_left', 'bottom_left', 'top_right', 'bottom_right']
    const savedRandomPosition = item.stickerPosition && randomPositions.includes(item.stickerPosition)
      ? item.stickerPosition
      : undefined
    const positions = stickerRandom.value
      ? [savedRandomPosition || randomPositions[Math.floor(Math.random() * randomPositions.length)]]
      : selectedPositions
    item.stickerPosition = stickerRandom.value ? positions[0] : undefined
    let stickerPath = sticker.path
    if (stickerRandomSource.value) {
      const savedStickerStillAvailable = item.stickerSourceFolder === stickerFolder.value
        && Boolean(item.stickerPath)
        && stickerFiles.includes(item.stickerPath as string)
      stickerPath = savedStickerStillAvailable
        ? item.stickerPath as string
        : stickerFiles[Math.floor(Math.random() * stickerFiles.length)]
      item.stickerPath = stickerPath
      item.stickerSourceFolder = stickerFolder.value
    } else {
      item.stickerPath = undefined
      item.stickerSourceFolder = undefined
    }
    items.push({
        video_path: item.path,
        output_dir: outputDirectory.value,
        watermark: enabled.watermark ? { path: watermark.path, opacity: watermark.opacity } : null,
        sticker: enabled.sticker ? { path: stickerPath, opacity: sticker.opacity, scale: sticker.scale, positions } : null,
        pixel: enabled.pixel ? { path: pixel.path, opacity: pixel.opacity } : null,
      })
  }
  applyBatch(await post<MediaBatch>('/media-batches/overlays', { items }))
  ensurePolling()
  ElMessage.success(`已提交 ${items.length} 个视频到后台处理队列`)
}

async function openResult(item: BatchVideo) {
  if (item.outputPath) await window.outocut?.openPath(item.outputPath)
}

onMounted(async () => {
  configPanelObserver = new ResizeObserver(([entry]) => {
    configPanelHeight.value = Math.ceil(entry.borderBoxSize?.[0]?.blockSize || entry.target.getBoundingClientRect().height)
  })
  if (configPanel.value) configPanelObserver.observe(configPanel.value)
  filePanelHeaderObserver = new ResizeObserver(([entry]) => {
    filePanelHeaderHeight.value = Math.ceil(entry.borderBoxSize?.[0]?.blockSize || entry.target.getBoundingClientRect().height)
  })
  if (filePanelHeader.value) filePanelHeaderObserver.observe(filePanelHeader.value)
  const settings = await api<AppSettings>('/settings')
  if (!outputDirectory.value) outputDirectory.value = settings.output_directory || ''
  const latest = await api<MediaBatch | null>('/media-batches/latest/overlay')
  if (latest) {
    applyBatch(latest)
    if (latest.state === 'running') ensurePolling()
  }
})

onBeforeUnmount(() => {
  configPanelObserver?.disconnect()
  filePanelHeaderObserver?.disconnect()
  if (pollTimer !== null) window.clearInterval(pollTimer)
})
</script>

<template>
  <div class="overlay-page">
    <div class="asset-tip">批量选择本地视频，通过 FFmpeg 叠加全屏 GIF 水印、静态贴纸或像素点 GIF。所有处理均在本机完成，原视频不会被修改。</div>

    <section class="panel overlay-upload-panel">
      <div class="panel-header">
        <div><h2>上传视频文件</h2><p>支持一次选择多个视频，重复文件会自动忽略</p></div>
        <div class="toolbar"><el-button type="primary" plain @click="chooseVideos">选择多个视频</el-button><el-button type="danger" plain :disabled="processing || !videos.length" @click="clearList">清空列表</el-button></div>
      </div>
      <div class="overlay-summary"><b>{{ videos.length }}</b><span>个待处理视频</span><b>{{ completedCount }}</b><span>个处理成功</span></div>
    </section>

    <div class="overlay-workspace">
      <section ref="configPanel" class="panel overlay-config-panel">
        <div class="panel-header"><div><h2>处理方式</h2><p>三种处理可单独开启，也可组合后统一输出</p></div></div>

        <section class="overlay-layer-card" :class="{ enabled: enabled.watermark }">
          <header><div><strong>批量打水印</strong><span>全屏 GIF 循环覆盖至视频结束</span></div><el-switch v-model="enabled.watermark" /></header>
          <div class="overlay-layer-settings" :class="{ disabled: !enabled.watermark }">
            <label>GIF 图像</label><div class="path-field"><el-input v-model="watermark.path" readonly placeholder="选择水印 GIF" /><el-button type="primary" plain :disabled="!enabled.watermark" @click="chooseOverlay('watermark')">选择图像</el-button></div>
            <label>透明度</label><div class="overlay-slider-row"><el-slider v-model="watermark.opacity" :disabled="!enabled.watermark" :min="0.05" :max="1" :step="0.05" :show-tooltip="false" /><span>{{ Math.round(watermark.opacity * 100) }}%</span></div>
          </div>
        </section>

        <section class="overlay-layer-card" :class="{ enabled: enabled.sticker }">
          <header><div><strong>批量贴纸</strong><span>静态图片可同时显示在多个位置</span></div><el-switch v-model="enabled.sticker" /></header>
          <div class="overlay-layer-settings" :class="{ disabled: !enabled.sticker }">
            <label>贴纸来源</label><div class="sticker-source-mode"><span :class="{ active: !stickerRandomSource }">固定贴纸</span><el-switch v-model="stickerRandomSource" :disabled="!enabled.sticker" /><span :class="{ active: stickerRandomSource }">随机贴纸</span></div>
            <label>贴纸图像</label><div class="path-field"><el-input v-model="sticker.path" readonly placeholder="选择 PNG、JPG 等静态图片" /><el-button type="primary" plain :disabled="!enabled.sticker || stickerRandomSource" @click="chooseOverlay('sticker')">选择图像</el-button></div>
            <label>贴纸文件夹</label><div class="path-field"><el-input v-model="stickerFolder" readonly placeholder="选择包含 PNG、JPG 贴纸的文件夹" /><el-button type="primary" plain :disabled="!enabled.sticker || !stickerRandomSource" @click="chooseStickerFolder">选择文件夹</el-button></div>
            <label>透明度</label><div class="overlay-slider-row"><el-slider v-model="sticker.opacity" :disabled="!enabled.sticker" :min="0.05" :max="1" :step="0.05" :show-tooltip="false" /><span>{{ Math.round(sticker.opacity * 100) }}%</span></div>
            <label>贴纸缩放</label><div class="overlay-slider-row"><el-slider v-model="sticker.scale" :disabled="!enabled.sticker" :min="0.01" :max="2" :step="0.01" :show-tooltip="false" /><span>{{ Math.round(sticker.scale * 100) }}%</span></div>
            <label class="sticker-position-label">显示位置</label>
            <div class="sticker-position-options" :class="{ disabled: !enabled.sticker }">
              <span class="sticker-random-switch"><el-switch v-model="stickerRandom" :disabled="!enabled.sticker" />随机</span>
              <div class="sticker-position-switches" :class="{ subdued: stickerRandom }">
                <span><el-switch v-model="stickerPositions.top_left" :disabled="!enabled.sticker || stickerRandom" />左上</span>
                <span><el-switch v-model="stickerPositions.bottom_left" :disabled="!enabled.sticker || stickerRandom" />左下</span>
                <span><el-switch v-model="stickerPositions.top_right" :disabled="!enabled.sticker || stickerRandom" />右上</span>
                <span><el-switch v-model="stickerPositions.bottom_right" :disabled="!enabled.sticker || stickerRandom" />右下</span>
                <span><el-switch v-model="stickerPositions.center" :disabled="!enabled.sticker || stickerRandom" />中央</span>
              </div>
            </div>
          </div>
        </section>

        <section class="overlay-layer-card" :class="{ enabled: enabled.pixel }">
          <header><div><strong>批量像素点图</strong><span>全屏像素点 GIF 循环覆盖至视频结束</span></div><el-switch v-model="enabled.pixel" /></header>
          <div class="overlay-layer-settings" :class="{ disabled: !enabled.pixel }">
            <label>GIF 图像</label><div class="path-field"><el-input v-model="pixel.path" readonly placeholder="选择像素点 GIF" /><el-button type="primary" plain :disabled="!enabled.pixel" @click="chooseOverlay('pixel')">选择图像</el-button></div>
            <label>透明度</label><div class="overlay-slider-row"><el-slider v-model="pixel.opacity" :disabled="!enabled.pixel" :min="0.05" :max="1" :step="0.05" :show-tooltip="false" /><span>{{ Math.round(pixel.opacity * 100) }}%</span></div>
          </div>
        </section>

        <div class="overlay-output-row"><label>输出目录</label><div class="path-field"><el-input v-model="outputDirectory" readonly placeholder="选择处理后视频的保存目录" /><el-button type="primary" plain @click="chooseOutputDirectory">选择目录</el-button></div></div>
        <el-button class="overlay-start-button" type="primary" :loading="processing" :disabled="!pendingCount" @click="startBatch">{{ batchPaused ? '继续转换' : '开始批量处理' }}</el-button>
      </section>

      <section class="panel overlay-file-panel" :class="{ empty: !videos.length }" :style="configPanelHeight ? { maxHeight: `${configPanelHeight}px` } : undefined">
        <div ref="filePanelHeader" class="panel-header"><div><h2>视频列表</h2><p>已完成与未处理项目会自动保存，之后可以继续转换</p></div><div class="toolbar"><el-button v-if="processing" type="danger" plain :loading="stopping" @click="stopBatch()">中止转换</el-button><el-button v-else-if="batchPaused && pendingCount" type="primary" plain @click="startBatch">继续转换</el-button></div></div>
        <div v-if="!videos.length" class="empty-state">尚未选择视频文件</div>
        <el-table v-else :data="videos" :max-height="fileTableMaxHeight">
          <el-table-column type="index" label="序号" width="60" align="center" />
          <el-table-column label="视频文件" min-width="240"><template #default="scope"><div class="overlay-file-name"><span>{{ scope.row.name }}</span><small v-if="scope.row.error" :title="scope.row.error">{{ scope.row.error }}</small></div></template></el-table-column>
          <el-table-column label="状态" width="100" align="center"><template #default="scope"><span class="overlay-file-state" :class="scope.row.status">{{ statusLabels[scope.row.status] }}</span></template></el-table-column>
          <el-table-column label="操作" width="130" align="center"><template #default="scope"><el-button v-if="scope.row.status === 'succeeded'" size="small" type="primary" plain @click="openResult(scope.row)">打开</el-button><el-button v-else size="small" type="danger" plain :disabled="processing" @click="removeVideo(scope.$index)">删除</el-button></template></el-table-column>
        </el-table>
      </section>
    </div>
  </div>
</template>
