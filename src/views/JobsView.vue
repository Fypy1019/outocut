<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, post, put, remove } from '@/api'
import type { AppSettings, JobQueueStatus, JobSummary } from '@/types'

const jobs = ref<JobSummary[]>([])
const loading = ref(false)
const startingAll = ref(false)
const changingQueue = ref(false)
const queueStatus = ref<JobQueueStatus>({
  paused: false, automatic: false, reason: '', consecutive_failures: 0,
})
const selectedJobId = ref('')
const previewUrl = ref('')
const previewPath = ref('')
const settings = reactive<AppSettings>({
  output_directory: '', cache_directory: '', asset_root_directory: '', max_concurrent_jobs: 1,
  default_width: 1080, default_height: 1920, default_fps: 30, default_codec: 'libx264', default_quality: 23,
  bailian_base_url: '', ecom_text_api_mode: 'chat-completions', ecom_text_base_url: '', ecom_text_model: '', ecom_text_timeout_seconds: 600,
  ecom_image_api_mode: 'images', ecom_image_base_url: '', ecom_image_model: '', ecom_search_enabled: false,
  minimax_base_url: '', deepseek_base_url: 'https://api.deepseek.com', rewrite_provider: 'bailian', default_model: 'qwen-plus', default_voice_model: 'speech-2.8-hd', asr_provider: 'bailian', asr_model: 'paraformer-realtime-v2',
  rewrite_instructions: '', configured: {},
})
let timer: number | undefined

const labels: Record<string, string> = {
  queued: '排队中', preparing_audio: '生成配音', rendering: '合成中', succeeded: '已完成',
  failed: '合成失败', cancelled: '已取消', draft: '待合成',
}
const pendingCount = computed(() => jobs.value.filter((job) => job.state === 'draft').length)
const selected = computed(() => jobs.value.find((job) => job.id === selectedJobId.value) || null)

function clearSelectedJob() {
  selectedJobId.value = ''
  previewUrl.value = ''
  previewPath.value = ''
}

async function loadPreview(job: JobSummary) {
  if (job.state !== 'succeeded' || !job.output_path) {
    previewUrl.value = ''
    previewPath.value = ''
    return
  }
  if (previewPath.value === job.output_path && previewUrl.value) return
  const requestedJobId = job.id
  const requestedPath = job.output_path
  const url = await window.outocut?.pathToFileUrl(requestedPath) || ''
  if (selectedJobId.value !== requestedJobId) return
  previewPath.value = requestedPath
  previewUrl.value = url
}

async function refresh(silent = false) {
  if (!silent) loading.value = true
  try {
    const [jobRows, status] = await Promise.all([
      api<JobSummary[]>('/jobs'),
      api<JobQueueStatus>('/jobs-queue'),
    ])
    jobs.value = jobRows
    queueStatus.value = status
    if (selectedJobId.value) {
      const current = jobs.value.find((item) => item.id === selectedJobId.value)
      if (!current) clearSelectedJob()
      else await loadPreview(current)
    }
  } finally { loading.value = false }
}

async function selectJob(job: JobSummary | null) {
  // Element Plus 在轮询替换表格数据时会短暂发出 null，不应因此清空播放器。
  if (!job) return
  if (
    job.id === selectedJobId.value
    && job.output_path === previewPath.value
    && previewUrl.value
  ) return
  selectedJobId.value = job.id
  previewUrl.value = ''
  previewPath.value = ''
  await loadPreview(job)
}

async function saveStorage() {
  Object.assign(settings, await put<AppSettings>('/settings', settings))
}

async function chooseStorage(field: 'cache_directory' | 'output_directory') {
  const path = await window.outocut?.selectDirectory()
  if (!path) return
  settings[field] = path
  await saveStorage()
  ElMessage.success('存储目录已保存')
}

async function clearStorage(field: 'cache_directory' | 'output_directory') {
  settings[field] = ''
  await saveStorage()
  ElMessage.success('已恢复应用默认目录')
}

async function cancel(job: JobSummary) {
  await ElMessageBox.confirm(`取消任务“${job.name}”？`, '取消任务', { type: 'warning' })
  await post(`/jobs/${job.id}/cancel`)
  await refresh(true)
}

async function retry(job: JobSummary) {
  await post(`/jobs/${job.id}/retry`)
  ElMessage.success('任务已重新加入队列')
  await refresh(true)
}

async function startJob(job: JobSummary) {
  await post(`/jobs/${job.id}/start`)
  ElMessage.success(`任务“${job.name}”已加入合成队列`)
  await refresh(true)
}

async function startAll() {
  if (!pendingCount.value) return ElMessage.info('当前没有待合成任务')
  startingAll.value = true
  try {
    const result = await post<{ started: number }>('/jobs/start-all')
    ElMessage.success(`已开始 ${result.started} 个待合成任务`)
    await refresh(true)
  } finally { startingAll.value = false }
}

async function toggleQueue() {
  changingQueue.value = true
  try {
    const action = queueStatus.value.paused ? 'resume' : 'pause'
    queueStatus.value = await post<JobQueueStatus>(`/jobs-queue/${action}`)
    ElMessage.success(queueStatus.value.paused ? '队列已暂停，当前任务会继续完成' : '队列已继续')
    await refresh(true)
  } finally { changingQueue.value = false }
}

async function deleteJob(job: JobSummary) {
  if (job.state === 'succeeded') {
    await ElMessageBox.confirm('删除任务记录？成片文件不会被删除。', '删除任务', { type: 'warning' })
  }
  await remove(`/jobs/${job.id}`)
  if (selectedJobId.value === job.id) clearSelectedJob()
  await refresh(true)
}

async function openOutput(job: JobSummary) {
  if (job.output_path) await window.outocut?.openPath(job.output_path)
}

async function clearCompleted() {
  const result = await remove<{ deleted: number }>('/jobs/completed')
  ElMessage.success(`已清理 ${result.deleted} 条任务记录`)
  clearSelectedJob()
  await refresh(true)
}

onMounted(async () => {
  Object.assign(settings, await api<AppSettings>('/settings'))
  await refresh()
  timer = window.setInterval(() => refresh(true), 1500)
})
onBeforeUnmount(() => { if (timer) window.clearInterval(timer) })
</script>

<template>
  <div class="jobs-page">
    <div class="asset-tip">以下为当前混剪任务列表，与“镜头剪辑”页创建的任务数据共用。点击已完成任务，可在右侧直接预览成片。</div>

    <section class="panel storage-settings">
      <div class="storage-title"><h2>存储设置</h2></div>
      <div class="storage-body">
        <div class="storage-row">
          <label>工作缓存目录</label>
          <el-input v-model="settings.cache_directory" readonly />
          <el-button type="primary" plain @click="chooseStorage('cache_directory')">选择目录</el-button>
          <el-button type="danger" plain @click="clearStorage('cache_directory')">清空</el-button>
          <p>配音音频、字幕及任务临时文件写入此目录；不设置则使用应用缓存目录。</p>
        </div>
        <div class="storage-row">
          <label>成片目录</label>
          <el-input v-model="settings.output_directory" readonly />
          <el-button type="primary" plain @click="chooseStorage('output_directory')">选择目录</el-button>
          <el-button type="danger" plain @click="clearStorage('output_directory')">清空</el-button>
          <p>用于保存最终导出的视频文件；开始合成前需要有效的成片目录。</p>
        </div>
      </div>
    </section>

    <div class="jobs-toolbar">
      <el-button type="danger" plain @click="clearCompleted">清除已完成</el-button>
      <el-button type="primary" plain :loading="startingAll" :disabled="!pendingCount" @click="startAll">全部开始<span v-if="pendingCount">（{{ pendingCount }}）</span></el-button>
      <el-button type="primary" plain :loading="changingQueue" @click="toggleQueue">{{ queueStatus.paused ? '继续队列' : '暂停队列' }}</el-button>
      <span v-if="queueStatus.paused" class="queue-pause-reason">{{ queueStatus.reason || '队列已暂停' }}</span>
    </div>

    <div class="jobs-content-grid">
      <section class="panel jobs-table-panel">
        <div v-if="!jobs.length && !loading" class="empty-state">暂无混剪任务</div>
        <el-table v-else v-loading="loading" :data="jobs" row-key="id" :current-row-key="selectedJobId || undefined" highlight-current-row height="100%" @current-change="selectJob">
          <el-table-column type="index" label="序号" width="65" align="center" />
          <el-table-column prop="template_name" label="模板类型" min-width="190" show-overflow-tooltip />
          <el-table-column prop="shot_count" label="镜头数" width="90" align="center" />
          <el-table-column label="状态" width="120" align="center"><template #default="scope">
            <el-tag class="job-state-tag" :class="`is-${scope.row.state}`" :type="scope.row.state === 'succeeded' ? 'success' : scope.row.state === 'failed' ? 'danger' : scope.row.state === 'cancelled' ? 'info' : 'warning'">{{ labels[scope.row.state] }}</el-tag>
          </template></el-table-column>
          <el-table-column label="操作" width="210" align="center"><template #default="scope">
            <div class="job-operation-buttons" v-if="scope.row.state === 'draft'">
              <el-button size="small" type="primary" plain @click.stop="startJob(scope.row)">合成</el-button>
              <el-button size="small" type="danger" plain @click.stop="deleteJob(scope.row)">删除</el-button>
            </div>
            <el-button v-if="['queued','preparing_audio','rendering'].includes(scope.row.state)" size="small" type="danger" plain @click.stop="cancel(scope.row)">取消</el-button>
            <div class="job-operation-buttons" v-if="['failed','cancelled'].includes(scope.row.state)">
              <el-button size="small" type="primary" plain @click.stop="retry(scope.row)">重试</el-button>
              <el-button size="small" type="danger" plain @click.stop="deleteJob(scope.row)">删除</el-button>
            </div>
            <el-button v-if="scope.row.state === 'succeeded'" size="small" type="danger" plain @click.stop="deleteJob(scope.row)">删除</el-button>
          </template></el-table-column>
        </el-table>
      </section>

      <aside class="panel job-preview-panel">
        <div class="preview-heading"><span class="preview-play-icon">▷</span><h2>成片预览</h2></div>
        <div class="video-preview-shell">
          <video v-if="previewUrl" :key="previewUrl" :src="previewUrl" controls preload="metadata" />
          <div v-else class="video-preview-empty"><b>▣</b><span>点击左侧已完成任务预览成片</span></div>
        </div>
        <div v-if="selected?.output_path" class="preview-actions"><el-button type="primary" plain @click="openOutput(selected)">打开成片文件</el-button></div>
      </aside>
    </div>
  </div>
</template>
