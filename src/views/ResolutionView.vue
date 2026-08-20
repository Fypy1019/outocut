<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, post } from '@/api'

defineOptions({ name: 'ResolutionView' })

type Orientation = 'portrait' | 'landscape'
type ConvertStatus = 'pending' | 'converting' | 'succeeded' | 'failed'

interface ResolutionVideo {
  path: string
  relative_path: string
  name: string
  width: number
  height: number
  orientation: Orientation
  qualified: boolean
  status?: ConvertStatus
  outputPath?: string
  error?: string
}

interface ScanResponse {
  root_directory: string
  videos: ResolutionVideo[]
  errors: Array<{ path: string; error: string }>
}

interface MediaBatchTask {
  id: string
  state: ConvertStatus
  payload: Record<string, any>
  output_path?: string | null
  error?: string | null
}

interface MediaBatch {
  id: string
  kind: 'resolution'
  state: 'running' | 'paused' | 'completed'
  tasks: MediaBatchTask[]
}

const rootDirectory = ref('')
const outputDirectory = ref('')
const exportToFolder = ref(false)
const videos = ref<ResolutionVideo[]>([])
const scanErrors = ref<Array<{ path: string; error: string }>>([])
const scanning = ref(false)
const converting = ref<Orientation | null>(null)
const pausedOrientation = ref<Orientation | null>(null)
const pauseRequested = ref(false)
const portraitTarget = ref('1080x1920')
const landscapeTarget = ref('1920x1080')
let activeBatchId = ''
let pollTimer: number | null = null

const portraitQualified = computed(() => videos.value.filter((item) => item.orientation === 'portrait' && item.qualified))
const portraitNeeds = computed(() => videos.value.filter((item) => item.orientation === 'portrait' && !item.qualified))
const landscapeQualified = computed(() => videos.value.filter((item) => item.orientation === 'landscape' && item.qualified))
const landscapeNeeds = computed(() => videos.value.filter((item) => item.orientation === 'landscape' && !item.qualified))
const queueBusy = computed(() => converting.value !== null || pausedOrientation.value !== null)

const portraitTargets = [
  { label: '720 × 1280', value: '720x1280' },
  { label: '1080 × 1920', value: '1080x1920' },
  { label: '1440 × 2560', value: '1440x2560' },
]
const landscapeTargets = [
  { label: '1280 × 720', value: '1280x720' },
  { label: '1920 × 1080', value: '1920x1080' },
  { label: '2560 × 1440', value: '2560x1440' },
]
const statusLabels: Record<ConvertStatus, string> = {
  pending: '待转换', converting: '转换中', succeeded: '已完成', failed: '失败',
}

function restoreResolutionState() {
  try {
    const saved = JSON.parse(localStorage.getItem('outocut-resolution-state') || 'null')
    if (!saved) return
    rootDirectory.value = typeof saved.rootDirectory === 'string' ? saved.rootDirectory : ''
    outputDirectory.value = typeof saved.outputDirectory === 'string' ? saved.outputDirectory : ''
    exportToFolder.value = typeof saved.exportToFolder === 'boolean' ? saved.exportToFolder : false
    if (portraitTargets.some((item) => item.value === saved.portraitTarget)) portraitTarget.value = saved.portraitTarget
    if (landscapeTargets.some((item) => item.value === saved.landscapeTarget)) landscapeTarget.value = saved.landscapeTarget
    if (Array.isArray(saved.videos)) {
      videos.value = saved.videos
        .filter((item: Partial<ResolutionVideo>) => typeof item.path === 'string' && item.path)
        .map((item: ResolutionVideo) => ({
          ...item,
          status: item.status === 'converting' ? 'pending' : item.status,
        }))
    }
    scanErrors.value = Array.isArray(saved.scanErrors) ? saved.scanErrors : []
    const restoredOrientation = ['portrait', 'landscape'].includes(saved.pausedOrientation)
      ? saved.pausedOrientation as Orientation
      : ['portrait', 'landscape'].includes(saved.activeOrientation)
        ? saved.activeOrientation as Orientation
        : null
    if (restoredOrientation && videos.value.some((item) => item.orientation === restoredOrientation && !item.qualified && item.status !== 'succeeded')) {
      pausedOrientation.value = restoredOrientation
    }
  } catch {
    // Invalid or legacy persisted state is ignored.
  }
}

function persistResolutionState() {
  try {
    localStorage.setItem('outocut-resolution-state', JSON.stringify({
      rootDirectory: rootDirectory.value,
      outputDirectory: outputDirectory.value,
      exportToFolder: exportToFolder.value,
      portraitTarget: portraitTarget.value,
      landscapeTarget: landscapeTarget.value,
      activeOrientation: converting.value,
      pausedOrientation: pausedOrientation.value,
      videos: videos.value,
      scanErrors: scanErrors.value,
    }))
  } catch {
    // Storage quota errors must not interrupt scanning or conversion.
  }
}

restoreResolutionState()
watch(
  [rootDirectory, outputDirectory, exportToFolder, portraitTarget, landscapeTarget, converting, pausedOrientation, videos, scanErrors],
  persistResolutionState,
  { deep: true },
)

async function chooseRootDirectory() {
  const path = await window.outocut?.selectDirectory()
  if (!path) return
  rootDirectory.value = path
  await scanDirectory()
}

async function chooseOutputDirectory() {
  const path = await window.outocut?.selectDirectory()
  if (path) outputDirectory.value = path
}

async function scanDirectory() {
  if (!rootDirectory.value) return ElMessage.warning('请先选择需要扫描的文件夹')
  scanning.value = true
  try {
    const result = await post<ScanResponse>('/resolutions/scan', { root_directory: rootDirectory.value })
    rootDirectory.value = result.root_directory
    videos.value = result.videos.map((item) => ({ ...item, status: item.qualified ? undefined : 'pending' }))
    scanErrors.value = result.errors
    pausedOrientation.value = null
    ElMessage.success(`扫描完成，共识别 ${videos.value.length} 个视频`)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error))
  } finally {
    scanning.value = false
  }
}

async function convertGroup(orientation: Orientation) {
  if (exportToFolder.value && !outputDirectory.value) return ElMessage.warning('导出到新文件夹模式下，请先选择导出文件夹')
  const items = (orientation === 'portrait' ? portraitNeeds.value : landscapeNeeds.value)
    .filter((item) => item.status !== 'succeeded')
  if (!items.length) return ElMessage.warning('该分类没有待转换视频')
  const resuming = pausedOrientation.value === orientation && Boolean(activeBatchId)
  if (resuming) {
    applyBatch(await post<MediaBatch>(`/media-batches/${activeBatchId}/resume`))
    ensurePolling()
    return
  }
  if (!exportToFolder.value && !resuming) {
    try {
      await ElMessageBox.confirm(
        `将直接替换这 ${items.length} 个原视频，替换后无法恢复。是否继续？`,
        '确认替换原视频',
        { confirmButtonText: '继续替换', cancelButtonText: '取消', type: 'warning' },
      )
    } catch {
      return
    }
  }
  const target = orientation === 'portrait' ? portraitTarget.value : landscapeTarget.value
  const [targetWidth, targetHeight] = target.split('x').map(Number)
  const batch = await post<MediaBatch>('/media-batches/resolutions', {
    items: items.map((item) => ({
        video_path: item.path,
        source_root: rootDirectory.value,
        output_dir: exportToFolder.value ? outputDirectory.value : '',
        replace_original: !exportToFolder.value,
        target_width: targetWidth,
        target_height: targetHeight,
        relative_path: item.relative_path,
        source_width: item.width,
        source_height: item.height,
        orientation: item.orientation,
      })),
  })
  applyBatch(batch)
  ensurePolling()
  ElMessage.success(`已提交 ${items.length} 个视频到后台转换队列`)
}

async function pauseConversion() {
  if (!converting.value || pauseRequested.value) return
  pauseRequested.value = true
  applyBatch(await post<MediaBatch>(`/media-batches/${activeBatchId}/pause`))
  pauseRequested.value = false
  ElMessage.info('将在当前视频转换完成后暂停队列')
}

function applyBatch(batch: MediaBatch) {
  activeBatchId = batch.id
  const first = batch.tasks[0]
  const orientation = first?.payload.target_height >= first?.payload.target_width
    ? 'portrait' as Orientation
    : 'landscape' as Orientation
  converting.value = batch.state === 'running' ? orientation : null
  pausedOrientation.value = batch.state === 'paused' ? orientation : null
  const byPath = new Map(batch.tasks.map((task) => [String(task.payload.video_path), task]))
  for (const task of batch.tasks) {
    const path = String(task.payload.video_path || '')
    if (!path || videos.value.some((item) => item.path === path)) continue
    videos.value.push({
      path,
      relative_path: String(task.payload.relative_path || path),
      name: path.split(/[\\/]/).pop() || path,
      width: Number(task.payload.source_width || 0),
      height: Number(task.payload.source_height || 0),
      orientation: task.payload.orientation as Orientation,
      qualified: false,
      status: task.state,
      outputPath: task.output_path || '',
      error: task.error || '',
    })
  }
  for (const item of videos.value) {
    const task = byPath.get(item.path)
    if (!task) continue
    item.status = task.state
    item.outputPath = task.output_path || ''
    item.error = task.error || ''
  }
  if (batch.state === 'completed' && pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

async function refreshBatch() {
  if (!activeBatchId) return
  try { applyBatch(await api<MediaBatch>(`/media-batches/${activeBatchId}`)) } catch { /* retry */ }
}

function ensurePolling() {
  if (pollTimer === null) pollTimer = window.setInterval(() => void refreshBatch(), 600)
}

async function openOutput(item: ResolutionVideo) {
  if (item.outputPath) await window.outocut?.openPath(item.outputPath)
}

onMounted(async () => {
  const latest = await api<MediaBatch | null>('/media-batches/latest/resolution')
  if (latest) {
    applyBatch(latest)
    if (latest.state === 'running') ensurePolling()
  }
})

onBeforeUnmount(() => {
  if (pollTimer !== null) window.clearInterval(pollTimer)
})
</script>

<template>
  <div class="resolution-page">
    <div class="asset-tip">递归扫描所选文件夹及全部子文件夹，按横竖版和分辨率合格状态分类。转换后的视频保存到导出文件夹，原视频不会被修改。</div>

    <section class="panel resolution-folder-panel">
      <div class="panel-header"><div><h2>文件夹上传</h2><p>选择根文件夹后自动递归扫描常见视频格式</p></div></div>
      <div class="resolution-folder-form">
        <label>扫描文件夹</label><div class="path-field"><el-input v-model="rootDirectory" readonly placeholder="请选择包含视频的根文件夹" /><el-button type="primary" plain :loading="scanning" @click="chooseRootDirectory">选择文件夹</el-button><el-button type="primary" plain :disabled="!rootDirectory" :loading="scanning" @click="scanDirectory">重新扫描</el-button></div>
        <label>保存方式</label><div class="resolution-output-mode"><span :class="{ active: !exportToFolder }">替换原视频</span><el-switch v-model="exportToFolder" :disabled="queueBusy" /><span :class="{ active: exportToFolder }">导出到新文件夹</span><small>{{ exportToFolder ? '转换视频将保存到所选目录，原视频保持不变' : '仅成功转换的视频会原子替换原文件' }}</small></div>
        <label>导出文件夹</label><div class="path-field" :class="{ disabled: !exportToFolder }"><el-input v-model="outputDirectory" readonly :disabled="!exportToFolder" placeholder="请选择转换后视频的导出文件夹" /><el-button type="primary" plain :disabled="!exportToFolder" @click="chooseOutputDirectory">选择导出文件夹</el-button></div>
      </div>
      <div class="resolution-summary">
        <span><b>{{ videos.length }}</b> 视频总数</span>
        <span><b>{{ portraitQualified.length + landscapeQualified.length }}</b> 合格分辨率</span>
        <span><b>{{ portraitNeeds.length + landscapeNeeds.length }}</b> 需转换分辨率</span>
        <span v-if="scanErrors.length" class="error"><b>{{ scanErrors.length }}</b> 无法识别</span>
      </div>
    </section>

    <section class="panel resolution-list-panel">
      <div class="panel-header"><div><h2>视频列表</h2><p>竖版与横版分别提供合格、需转换两个分类</p></div></div>
      <div v-if="!videos.length && !scanning" class="empty-state">尚未扫描视频文件夹</div>
      <div v-else class="resolution-orientation-grid">
        <article class="resolution-orientation-card">
          <header><div><strong>竖版视频</strong><span>合格范围：720×1280 至 1440×2560</span></div><em>{{ portraitQualified.length + portraitNeeds.length }} 个</em></header>
          <section class="resolution-group qualified">
            <div class="resolution-group-heading"><div><strong>合格分辨率</strong><span>{{ portraitQualified.length }} 个视频，无需转换</span></div></div>
            <el-table :data="portraitQualified" max-height="260" empty-text="暂无合格竖版视频">
              <el-table-column label="视频" min-width="220"><template #default="scope"><div class="resolution-video-name"><span>{{ scope.row.name }}</span><small :title="scope.row.relative_path">{{ scope.row.relative_path }}</small></div></template></el-table-column>
              <el-table-column label="分辨率" width="110" align="center"><template #default="scope">{{ scope.row.width }} × {{ scope.row.height }}</template></el-table-column>
              <el-table-column label="状态" width="90" align="center"><template #default><span class="resolution-state qualified">合格</span></template></el-table-column>
            </el-table>
          </section>
          <section class="resolution-group needs-convert">
            <div class="resolution-group-heading"><div><strong>需转换分辨率</strong><span>{{ portraitNeeds.length }} 个视频</span></div><div class="resolution-actions"><el-select v-model="portraitTarget" :disabled="queueBusy"><el-option v-for="option in portraitTargets" :key="option.value" :label="option.label" :value="option.value" /></el-select><el-button v-if="converting === 'portrait'" type="primary" plain :disabled="pauseRequested" @click="pauseConversion">{{ pauseRequested ? '等待暂停' : '暂停队列' }}</el-button><el-button v-else-if="pausedOrientation === 'portrait'" type="primary" plain @click="convertGroup('portrait')">继续转换</el-button><el-button v-else type="primary" plain :disabled="!portraitNeeds.length || queueBusy" @click="convertGroup('portrait')">批量转换</el-button></div></div>
            <el-table :data="portraitNeeds" max-height="300" empty-text="暂无需转换竖版视频">
              <el-table-column label="视频" min-width="200"><template #default="scope"><div class="resolution-video-name"><span>{{ scope.row.name }}</span><small v-if="scope.row.error" class="error" :title="scope.row.error">{{ scope.row.error }}</small><small v-else :title="scope.row.relative_path">{{ scope.row.relative_path }}</small></div></template></el-table-column>
              <el-table-column label="分辨率" width="105" align="center"><template #default="scope">{{ scope.row.width }} × {{ scope.row.height }}</template></el-table-column>
              <el-table-column label="状态" width="86" align="center"><template #default="scope"><span class="resolution-state" :class="scope.row.status">{{ statusLabels[scope.row.status as ConvertStatus] }}</span></template></el-table-column>
              <el-table-column label="操作" width="74" align="center"><template #default="scope"><el-button v-if="scope.row.status === 'succeeded'" size="small" type="primary" plain @click="openOutput(scope.row)">打开</el-button></template></el-table-column>
            </el-table>
          </section>
        </article>

        <article class="resolution-orientation-card">
          <header><div><strong>横版视频</strong><span>合格范围：1280×720 至 2560×1440</span></div><em>{{ landscapeQualified.length + landscapeNeeds.length }} 个</em></header>
          <section class="resolution-group qualified">
            <div class="resolution-group-heading"><div><strong>合格分辨率</strong><span>{{ landscapeQualified.length }} 个视频，无需转换</span></div></div>
            <el-table :data="landscapeQualified" max-height="260" empty-text="暂无合格横版视频">
              <el-table-column label="视频" min-width="220"><template #default="scope"><div class="resolution-video-name"><span>{{ scope.row.name }}</span><small :title="scope.row.relative_path">{{ scope.row.relative_path }}</small></div></template></el-table-column>
              <el-table-column label="分辨率" width="110" align="center"><template #default="scope">{{ scope.row.width }} × {{ scope.row.height }}</template></el-table-column>
              <el-table-column label="状态" width="90" align="center"><template #default><span class="resolution-state qualified">合格</span></template></el-table-column>
            </el-table>
          </section>
          <section class="resolution-group needs-convert">
            <div class="resolution-group-heading"><div><strong>需转换分辨率</strong><span>{{ landscapeNeeds.length }} 个视频</span></div><div class="resolution-actions"><el-select v-model="landscapeTarget" :disabled="queueBusy"><el-option v-for="option in landscapeTargets" :key="option.value" :label="option.label" :value="option.value" /></el-select><el-button v-if="converting === 'landscape'" type="primary" plain :disabled="pauseRequested" @click="pauseConversion">{{ pauseRequested ? '等待暂停' : '暂停队列' }}</el-button><el-button v-else-if="pausedOrientation === 'landscape'" type="primary" plain @click="convertGroup('landscape')">继续转换</el-button><el-button v-else type="primary" plain :disabled="!landscapeNeeds.length || queueBusy" @click="convertGroup('landscape')">批量转换</el-button></div></div>
            <el-table :data="landscapeNeeds" max-height="300" empty-text="暂无需转换横版视频">
              <el-table-column label="视频" min-width="200"><template #default="scope"><div class="resolution-video-name"><span>{{ scope.row.name }}</span><small v-if="scope.row.error" class="error" :title="scope.row.error">{{ scope.row.error }}</small><small v-else :title="scope.row.relative_path">{{ scope.row.relative_path }}</small></div></template></el-table-column>
              <el-table-column label="分辨率" width="105" align="center"><template #default="scope">{{ scope.row.width }} × {{ scope.row.height }}</template></el-table-column>
              <el-table-column label="状态" width="86" align="center"><template #default="scope"><span class="resolution-state" :class="scope.row.status">{{ statusLabels[scope.row.status as ConvertStatus] }}</span></template></el-table-column>
              <el-table-column label="操作" width="74" align="center"><template #default="scope"><el-button v-if="scope.row.status === 'succeeded'" size="small" type="primary" plain @click="openOutput(scope.row)">打开</el-button></template></el-table-column>
            </el-table>
          </section>
        </article>
      </div>
    </section>
  </div>
</template>
