<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

const snapshot = ref<QianchuanUploadSnapshot | null>(null)
const busy = ref(false)
let unsubscribe: (() => void) | undefined

const sharedMode = computed(() => Boolean(snapshot.value?.config.sharedEntrances))
const files = computed(() => {
  const allFiles = snapshot.value?.files || []
  return allFiles.filter((file) => sharedMode.value
    ? file.queueId?.startsWith('shared-entrances:')
    : !file.queueId?.startsWith('shared-entrances:'))
})
const failures = computed(() => files.value.filter((file) => file.state === 'failed'))
const succeeded = computed(() => files.value.filter((file) => file.state === 'succeeded').length)
const pending = computed(() => files.value.filter((file) => file.state === 'pending').length)
const uploading = computed(() => files.value.filter((file) => file.state === 'uploading').length)
const entrances = computed(() => snapshot.value?.entrances || [])
const activeEntrances = computed(() => entrances.value.filter((entrance) => entrance.taskRunning).length)
const isRunning = computed(() => Boolean(snapshot.value?.taskRunning))
const isPaused = computed(() => snapshot.value?.state === 'paused')

const stateLabel: Record<QianchuanRunState, string> = {
  idle: '等待配置', waiting_for_user: '等待人工确认', ready: '准备上传', uploading: '上传中',
  paused: '已暂停', login_required: '需要登录', page_changed: '入口未识别', completed: '批次完成',
  stopped: '已停止', error: '运行异常',
}

async function invoke(action: () => Promise<QianchuanUploadSnapshot>, success = '') {
  if (busy.value) return
  busy.value = true
  try {
    snapshot.value = await action()
    if (success) ElMessage.success(success)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error))
  } finally { busy.value = false }
}

async function saveConfig() {
  if (!snapshot.value || !window.outocut) return
  await invoke(() => window.outocut!.qianchuanConfigure({
    batchSize: snapshot.value!.config.batchSize,
    batchTimeoutSeconds: snapshot.value!.config.batchTimeoutSeconds,
    maxConcurrentEntrances: snapshot.value!.config.maxConcurrentEntrances,
    sharedEntrances: snapshot.value!.config.sharedEntrances,
  }))
}

async function chooseEntranceDirectory(entrance: QianchuanUploadEntrance) {
  if (!window.outocut) return ElMessage.warning('千川上传仅支持桌面客户端')
  const directories = await window.outocut.selectDirectories()
  if (!directories.length) return
  if (entrance.sourceDirectories.length) {
    try {
      await ElMessageBox.confirm(
        `重新选择会用这 ${directories.length} 个文件夹替换 ${entrance.name} 当前关联的全部文件夹，并清除该入口现有的成功/失败记录。是否继续？`,
        '确认更换入口文件夹',
        { type: 'warning', confirmButtonText: '更换并重新扫描', cancelButtonText: '取消' },
      )
    } catch { return }
  }
  await invoke(
    () => window.outocut!.qianchuanAssignEntranceDirectories(entrance.id, directories),
    `${entrance.name} 已关联 ${directories.length} 个素材文件夹`,
  )
}

async function chooseSharedDirectories() {
  if (!window.outocut || !snapshot.value) return ElMessage.warning('千川上传仅支持桌面客户端')
  const directories = await window.outocut.selectDirectories()
  if (!directories.length) return
  if (snapshot.value.sharedSourceDirectories.length) {
    try {
      await ElMessageBox.confirm(
        `重新选择会用这 ${directories.length} 个文件夹替换当前共用文件夹，并重建所有入口的上传记录。是否继续？`,
        '确认更换共用文件夹',
        { type: 'warning', confirmButtonText: '更换并重新扫描', cancelButtonText: '取消' },
      )
    } catch { return }
  }
  await invoke(
    () => window.outocut!.qianchuanAssignSharedDirectories(directories),
    `已为全部入口选择 ${directories.length} 个共用素材文件夹`,
  )
}

const focusEntrance = (entrance: QianchuanUploadEntrance) => (
  window.outocut && invoke(() => window.outocut!.qianchuanFocusEntrance(entrance.id))
)

async function openChrome() {
  if (!window.outocut) return ElMessage.warning('千川上传仅支持桌面客户端')
  await saveConfig()
  await invoke(() => window.outocut!.qianchuanOpenChrome(), '已打开千川专用 Chrome')
}

async function discoverEntrances() {
  if (!window.outocut) return
  await saveConfig()
  await invoke(() => window.outocut!.qianchuanDiscoverEntrances(), '上传入口识别完成')
}

async function startEntrance(entrance: QianchuanUploadEntrance) {
  if (!window.outocut || !snapshot.value || !canStartEntrance(entrance)) return
  const remaining = files.value.filter((file) => file.entranceId === entrance.id && ['pending', 'failed'].includes(file.state)).length
  try {
    await ElMessageBox.confirm(
      `${entrance.name} 将只上传其素材文件夹中的 ${remaining} 个待处理视频，每批 ${snapshot.value.config.batchSize} 个。是否开始？`,
      `启动 ${entrance.name}`,
      { type: 'warning', confirmButtonText: '开始上传', cancelButtonText: '取消' },
    )
  } catch { return }
  await invoke(() => window.outocut!.qianchuanStartEntrance(entrance.id))
}

const pause = () => window.outocut && invoke(() => window.outocut!.qianchuanPause())
const resume = () => window.outocut && invoke(() => window.outocut!.qianchuanResume())
const stop = () => window.outocut && invoke(() => window.outocut!.qianchuanStop())
const retryFailed = () => window.outocut && invoke(() => window.outocut!.qianchuanRetryFailed())

async function confirmSucceeded(fileIds: string[]) {
  if (!window.outocut || !fileIds.length) return
  try {
    await ElMessageBox.confirm(
      `确认这 ${fileIds.length} 个视频已在千川页面显示上传成功？`,
      '人工确认上传结果',
      { type: 'warning', confirmButtonText: '平台已显示成功', cancelButtonText: '取消' },
    )
  } catch { return }
  await invoke(() => window.outocut!.qianchuanConfirmSucceeded(fileIds))
}

const confirmOneSucceeded = (file: QianchuanUploadFile) => confirmSucceeded([file.id])

const entranceStateLabel: Record<QianchuanEntranceState, string> = {
  ready: '空闲', uploading: '上传中', paused: '未启用', completed: '已完成', stopped: '已停止',
  unavailable: '不可用', error: '异常',
}
const formatEntranceState = (state: QianchuanEntranceState) => entranceStateLabel[state] || state
const entranceProgress = (entranceId: string) => {
  const assigned = files.value.filter((file) => file.entranceId === entranceId)
  const done = assigned.filter((file) => file.state === 'succeeded').length
  return `${done}/${assigned.length}`
}
const fileEntrance = (file: QianchuanUploadFile) => entrances.value.find((entrance) => entrance.id === file.entranceId)
const entranceBadgeStyle = (entrance?: QianchuanUploadEntrance, file?: QianchuanUploadFile) => ({
  '--entrance-color': entrance?.color || file?.entranceColor || '#8b6cff',
})
const entranceDirectories = (entrance: QianchuanUploadEntrance) => entrance.sourceDirectories?.length
  ? entrance.sourceDirectories
  : entrance.sourceDirectory ? [entrance.sourceDirectory] : []
const entranceDirectoryText = (entrance: QianchuanUploadEntrance) => {
  const directories = entranceDirectories(entrance)
  if (!directories.length) return '尚未选择'
  return directories.length === 1 ? directories[0] : `${directories.length} 个文件夹：${directories.join('；')}`
}
const entranceHasRemaining = (entranceId: string) => files.value.some((file) => (
  file.entranceId === entranceId && ['pending', 'failed'].includes(file.state)
))
const canStartEntrance = (entrance: QianchuanUploadEntrance) => Boolean(
  !sharedMode.value
  &&
  snapshot.value?.browserOpen
  && entranceDirectories(entrance).length > 0
  && entrance.totalFiles > 0
  && !entrance.taskRunning
  && entrance.state !== 'paused'
  && entranceHasRemaining(entrance.id)
  && activeEntrances.value < snapshot.value.config.maxConcurrentEntrances,
)
const canStartAll = computed(() => Boolean(
  snapshot.value?.browserOpen
  && !isRunning.value
  && (sharedMode.value
    ? snapshot.value!.sharedSourceDirectories.length > 0
      && entrances.value.length > 0
      && files.value.some((file) => ['pending', 'failed'].includes(file.state))
    : entrances.value.some((entrance) => (
      entranceDirectories(entrance).length > 0 && entrance.totalFiles > 0 && entranceHasRemaining(entrance.id)
    ))),
))

const sharedDirectoryText = computed(() => {
  const directories = snapshot.value?.sharedSourceDirectories || []
  if (!directories.length) return '尚未选择共用素材文件夹'
  return directories.length === 1 ? directories[0] : `${directories.length} 个文件夹：${directories.join('；')}`
})

async function startAllEntrances() {
  if (!window.outocut || !canStartAll.value) return
  await saveConfig()
  await invoke(() => window.outocut!.qianchuanStart(), '已启动所有配置完成的上传入口')
}

function formatSize(size: number) {
  if (size >= 1024 ** 3) return `${(size / 1024 ** 3).toFixed(2)} GB`
  return `${(size / 1024 ** 2).toFixed(1)} MB`
}

onMounted(async () => {
  if (!window.outocut) return
  snapshot.value = await window.outocut.qianchuanStatus()
  unsubscribe = window.outocut.onQianchuanStatus((next) => { snapshot.value = next })
})
onBeforeUnmount(() => unsubscribe?.())
</script>

<template>
  <div v-if="snapshot" class="qianchuan-page">
    <div class="asset-tip">{{ sharedMode ? '多入口共用模式：每个入口都会完整上传一遍共用文件夹中的全部视频。' : '独立模式：每个 Chrome 上传入口可关联一个或多个本地素材文件夹，入口之间不会共享素材。' }}</div>
    <section class="panel qianchuan-status-panel">
      <div><span class="qianchuan-state" :class="snapshot.state">{{ stateLabel[snapshot.state] }}</span><strong>{{ snapshot.message }}</strong></div>
      <span>Chrome {{ snapshot.browserOpen ? '已连接' : '未连接' }}</span>
    </section>

    <section class="panel settings-section-card">
      <div class="settings-card-title"><h2>上传配置</h2></div>
      <div class="qianchuan-config">
        <label>每批文件数</label>
        <el-input-number v-model="snapshot.config.batchSize" :min="1" :max="100" :disabled="isRunning" @change="saveConfig" />
        <span class="field-help">默认 9，请按当前千川入口的实际限制配置。</span>
        <label>单批超时</label>
        <el-input-number v-model="snapshot.config.batchTimeoutSeconds" :min="30" :max="3600" :step="30" :disabled="isRunning" @change="saveConfig" />
        <span class="field-help">单位：秒。超时文件会进入失败记录。</span>
        <label>并发入口数</label>
        <el-input-number v-model="snapshot.config.maxConcurrentEntrances" :min="1" :max="50" :disabled="isRunning" @change="saveConfig" />
        <span class="field-help">从已识别入口中最多同时启用的数量。</span>
      </div>
      <div class="qianchuan-actions">
        <div class="qianchuan-shared-switch"><span>多入口共用</span><el-switch v-model="snapshot.config.sharedEntrances" :disabled="isRunning" @change="saveConfig" /></div>
        <el-button type="primary" plain :loading="busy" @click="openChrome">{{ snapshot.browserOpen ? '新增千川页面' : '打开千川 Chrome' }}</el-button>
        <el-button type="primary" plain :loading="busy" :disabled="!snapshot.browserOpen || isRunning" @click="discoverEntrances">识别全部入口</el-button>
        <el-button v-if="!sharedMode" class="qianchuan-start-all-button" type="primary" :loading="busy" :disabled="!canStartAll" @click="startAllEntrances">一键开启上传</el-button>
        <el-button v-if="isRunning" type="warning" plain @click="pause">暂停</el-button>
        <el-button v-if="isPaused" type="primary" plain @click="resume">继续</el-button>
        <el-button v-if="isRunning || isPaused" type="danger" plain @click="stop">停止</el-button>
      </div>
      <div v-if="sharedMode" class="qianchuan-shared-box">
        <div><strong>共用素材文件夹</strong><span :class="snapshot.sharedSourceDirectories.length ? 'qianchuan-folder-path' : 'qianchuan-folder-empty'">{{ sharedDirectoryText }}</span></div>
        <span class="field-help">每个已识别入口都会完整上传一遍，共 {{ files.length }} 个上传文件记录。</span>
        <div><el-button type="primary" plain :disabled="isRunning" @click="chooseSharedDirectories">选择文件夹</el-button><el-button class="qianchuan-start-all-button" type="primary" :loading="busy" :disabled="!canStartAll" @click="startAllEntrances">开始全部入口</el-button></div>
      </div>
    </section>

    <section class="panel settings-section-card">
      <div class="settings-card-title qianchuan-result-title">
        <div><h2>上传入口与素材文件夹</h2><small>{{ sharedMode ? '所有入口使用上方同一组文件夹，并分别完整上传全部视频。' : '先识别 Chrome 中全部“上传视频”页面，再为每个入口分别选择素材文件夹。' }}</small></div>
        <span class="field-help">已识别 {{ entrances.length }} 个，正在上传 {{ activeEntrances }} 个</span>
      </div>
      <el-empty v-if="!entrances.length" description="尚未识别上传入口" :image-size="60" />
      <el-table v-else :data="entrances" max-height="300">
        <el-table-column label="入口" width="90" align="center"><template #default="scope"><span class="qianchuan-color-marker" :style="entranceBadgeStyle(scope.row)" /></template></el-table-column>
        <el-table-column label="素材文件夹" min-width="280" show-overflow-tooltip><template #default="scope"><span v-if="sharedMode" class="qianchuan-folder-path">使用共用素材文件夹</span><span v-else :class="entranceDirectories(scope.row).length ? 'qianchuan-folder-path' : 'qianchuan-folder-empty'">{{ entranceDirectoryText(scope.row) }}</span></template></el-table-column>
        <el-table-column label="操作" width="290"><template #default="scope"><div class="qianchuan-entry-actions"><el-button plain size="small" @click="focusEntrance(scope.row)">定位页面</el-button><template v-if="!sharedMode"><el-button type="primary" plain size="small" :disabled="scope.row.taskRunning || (isRunning && scope.row.state === 'paused')" @click="chooseEntranceDirectory(scope.row)">选择文件夹</el-button><el-button class="qianchuan-start-button" type="primary" size="small" :disabled="!canStartEntrance(scope.row)" @click="startEntrance(scope.row)">开始上传</el-button></template></div></template></el-table-column>
        <el-table-column prop="message" label="说明" min-width="240" show-overflow-tooltip />
        <el-table-column prop="totalFiles" label="素材数" width="80" align="center" />
        <el-table-column label="成功进度" width="100" align="center"><template #default="scope">{{ entranceProgress(scope.row.id) }}</template></el-table-column>
        <el-table-column label="状态" width="100"><template #default="scope"><span class="qianchuan-entry-state" :class="scope.row.state">{{ formatEntranceState(scope.row.state) }}</span></template></el-table-column>
        <el-table-column label="批次" width="90"><template #default="scope">{{ scope.row.currentBatch || '-' }}</template></el-table-column>
        <el-table-column label="文件数" width="90"><template #default="scope">{{ scope.row.fileCount || '-' }}</template></el-table-column>
      </el-table>
    </section>

    <section class="qianchuan-summary-grid">
      <div class="panel"><small>素材总数</small><strong>{{ files.length }}</strong></div>
      <div class="panel"><small>上传成功</small><strong>{{ succeeded }}</strong></div>
      <div class="panel"><small>等待处理</small><strong>{{ pending + uploading }}</strong></div>
      <div class="panel"><small>上传失败</small><strong class="failure-count">{{ failures.length }}</strong></div>
    </section>

    <section class="panel settings-section-card">
      <div class="settings-card-title qianchuan-result-title">
        <div><h2>失败记录</h2><small>只展示上传失败或无法确认结果的视频</small></div>
        <el-button v-if="failures.length" type="primary" plain size="small" :disabled="isRunning" @click="retryFailed">重新加入队列</el-button>
      </div>
      <el-empty v-if="!failures.length" description="暂无失败视频" :image-size="72" />
      <el-table v-else :data="failures" max-height="360">
        <el-table-column label="入口" width="90" align="center"><template #default="scope"><span class="qianchuan-color-marker compact" :style="entranceBadgeStyle(fileEntrance(scope.row), scope.row)" /></template></el-table-column>
        <el-table-column prop="name" label="文件名" min-width="280" show-overflow-tooltip />
        <el-table-column label="大小" width="110"><template #default="scope">{{ formatSize(scope.row.size) }}</template></el-table-column>
        <el-table-column prop="error" label="失败原因" min-width="320" show-overflow-tooltip />
        <el-table-column label="操作" width="130"><template #default="scope"><el-button type="primary" plain size="small" @click="confirmOneSucceeded(scope.row)">平台已成功</el-button></template></el-table-column>
      </el-table>
    </section>

    <div v-if="snapshot.state === 'completed'" class="qianchuan-final-notice">自动批次已经结束。请回到 Chrome 核对素材数量、名称及平台提示，然后由人工执行最终提交。</div>
  </div>
</template>
