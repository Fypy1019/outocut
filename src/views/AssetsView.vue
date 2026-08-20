<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, post } from '@/api'
import type { AssetClip, AssetFolderView, AssetRootOverview } from '@/types'

const overview = ref<AssetRootOverview>({
  root_directory: '', folders: [], folder_count: 0, usable_clip_count: 0,
  unsupported_file_count: 0, insufficient_folder_count: 0, scanned_at: null,
})
const rootDirectory = ref('')
const keyword = ref('')
const loading = ref(false)
const scanning = ref(false)
const rescanningId = ref('')
const detailOpen = ref(false)
const detailFolder = ref<AssetFolderView | null>(null)
const detailClips = ref<AssetClip[]>([])
const previewUrls = reactive<Record<string, string>>({})
const previewStatus = reactive<Record<string, 'loading' | 'ready' | 'error'>>({})

const visibleFolders = computed(() => {
  const query = keyword.value.trim().toLocaleLowerCase()
  if (!query) return overview.value.folders
  return overview.value.folders.filter((folder) =>
    `${folder.name} ${folder.directory}`.toLocaleLowerCase().includes(query),
  )
})

async function hydratePreviewUrls() {
  const paths = [...new Set(overview.value.folders.flatMap((folder) => folder.preview_paths))]
  await Promise.all(paths.map(async (path) => {
    if (previewUrls[path]) return
    const url = await window.outocut?.pathToFileUrl(path)
    if (url) {
      previewUrls[path] = url
      previewStatus[path] = 'loading'
    }
  }))
}

async function loadOverview() {
  loading.value = true
  try {
    overview.value = await api<AssetRootOverview>('/assets/root')
    rootDirectory.value = overview.value.root_directory
    await hydratePreviewUrls()
  } finally {
    loading.value = false
  }
}

async function chooseRoot() {
  const path = await window.outocut?.selectDirectory()
  if (!path) return
  rootDirectory.value = path
  await scanRoot()
}

async function scanRoot() {
  if (!rootDirectory.value) return ElMessage.warning('请先选择混剪素材根目录')
  scanning.value = true
  try {
    overview.value = await post<AssetRootOverview>('/assets/root/scan', {
      root_directory: rootDirectory.value,
    })
    await hydratePreviewUrls()
    ElMessage.success(`扫描完成，接入 ${overview.value.folder_count} 个素材文件夹`)
  } finally {
    scanning.value = false
  }
}

async function rescanFolder(folder: AssetFolderView) {
  rescanningId.value = folder.id
  try {
    await post<AssetClip[]>(`/assets/scan?category_id=${encodeURIComponent(folder.id)}`)
    await loadOverview()
    ElMessage.success(`“${folder.name}”已重新扫描`)
  } finally {
    rescanningId.value = ''
  }
}

async function openDetails(folder: AssetFolderView) {
  detailFolder.value = folder
  detailClips.value = await api<AssetClip[]>(`/assets?category_id=${encodeURIComponent(folder.id)}`)
  detailOpen.value = true
}

function preparePreview(path: string, event: Event) {
  const video = event.currentTarget as HTMLVideoElement
  video.pause()
  if (Number.isFinite(video.duration) && video.duration > 0.05) {
    video.currentTime = Math.min(0.12, video.duration / 2)
  } else {
    previewStatus[path] = 'ready'
  }
}

function formatScanTime(value: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '尚未扫描'
}

onMounted(loadOverview)
</script>

<template>
  <div class="asset-root-page">
    <section class="panel asset-root-panel">
      <div class="asset-tip">
        素材中心按混剪根目录管理：根目录下的每个一级文件夹都是一个可复用的镜头素材视图。混剪时选择文件夹，系统会按随机种子从各文件夹轮换抽取镜头。
      </div>

      <div class="asset-root-toolbar">
        <strong>混剪根目录</strong>
        <el-input v-model="rootDirectory" readonly placeholder="请选择包含多个素材子文件夹的根目录" />
        <el-button type="primary" @click="chooseRoot">选择文件夹</el-button>
        <el-button type="primary" plain :loading="scanning" :disabled="!rootDirectory && !loading" @click="scanRoot">重新扫描</el-button>
        <el-input v-model="keyword" clearable placeholder="按类别名或路径搜索" />
      </div>

      <div class="asset-summary-grid">
        <div class="asset-summary-card"><b>{{ overview.folder_count }}</b><span>已接入文件夹</span></div>
        <div class="asset-summary-card"><b>{{ overview.usable_clip_count }}</b><span>可用片段总数</span></div>
        <div class="asset-summary-card warning"><b>{{ overview.unsupported_file_count }}</b><span>非视频文件</span></div>
        <div class="asset-summary-card danger"><b>{{ overview.insufficient_folder_count }}</b><span>素材不足文件夹</span></div>
      </div>
    </section>

    <div class="asset-section-heading">
      <div><h2>素材文件夹视图</h2><p>仅显示一级文件夹；视频列表可在详情中查看</p></div>
      <span>最近扫描：{{ formatScanTime(overview.scanned_at) }}</span>
    </div>

    <div v-if="!overview.root_directory" class="panel empty-state">
      选择一个混剪根目录开始扫描，例如 D:\分类素材
    </div>
    <div v-else-if="!visibleFolders.length" class="panel empty-state">
      根目录下没有可显示的一级素材文件夹
    </div>

    <div v-else class="asset-folder-grid">
      <article v-for="folder in visibleFolders" :key="folder.id" class="asset-folder-card">
        <header>
          <div><h3>{{ folder.name }}</h3><p :title="folder.directory">{{ folder.directory }}</p></div>
          <div class="folder-badges">
            <el-tag v-if="folder.usable_count < 3" type="warning" size="small">素材较少</el-tag>
            <el-tag v-if="folder.hdr_count" type="danger" size="small">含 {{ folder.hdr_count }} 个HDR</el-tag>
          </div>
        </header>

        <div class="folder-stats">
          <span><b>{{ folder.usable_count }}</b> 个可用片段</span>
          <span v-if="folder.unsupported_file_count" class="danger-text">{{ folder.unsupported_file_count }} 个非视频文件</span>
        </div>

        <div class="folder-preview-grid">
          <div v-for="index in 4" :key="index" class="folder-preview">
            <video
              v-if="folder.preview_paths[index - 1] && previewUrls[folder.preview_paths[index - 1]]"
              :src="previewUrls[folder.preview_paths[index - 1]]"
              :class="{ ready: previewStatus[folder.preview_paths[index - 1]] === 'ready' }"
              muted
              playsinline
              preload="auto"
              @loadeddata="preparePreview(folder.preview_paths[index - 1], $event)"
              @seeked="previewStatus[folder.preview_paths[index - 1]] = 'ready'"
              @error="previewStatus[folder.preview_paths[index - 1]] = 'error'"
            />
            <span v-if="folder.preview_paths[index - 1] && previewStatus[folder.preview_paths[index - 1]] !== 'ready'" class="preview-state">
              {{ previewStatus[folder.preview_paths[index - 1]] === 'error' ? '预览失败' : '读取首帧' }}
            </span>
            <span v-else-if="!folder.preview_paths[index - 1]">{{ index <= folder.usable_count ? '读取预览' : '暂无片段' }}</span>
          </div>
        </div>

        <footer>
          <el-button text type="primary" @click="openDetails(folder)">查看详情</el-button>
          <el-button text type="primary" :loading="rescanningId === folder.id" @click="rescanFolder(folder)">重新扫描</el-button>
        </footer>
      </article>
    </div>
  </div>

  <el-dialog v-model="detailOpen" :title="`${detailFolder?.name || ''} · 素材详情`" width="900px">
    <el-table :data="detailClips" height="520">
      <el-table-column prop="name" label="文件" min-width="230" show-overflow-tooltip />
      <el-table-column label="画面" width="120"><template #default="scope">{{ scope.row.width }}×{{ scope.row.height }}</template></el-table-column>
      <el-table-column label="时长" width="90"><template #default="scope">{{ scope.row.duration.toFixed(1) }}s</template></el-table-column>
      <el-table-column prop="codec" label="编码" width="100" />
      <el-table-column label="状态" width="150"><template #default="scope">
        <el-tag :type="scope.row.issue === 'none' ? 'success' : scope.row.issue === 'hdr' ? 'warning' : 'danger'">
          {{ scope.row.issue === 'none' ? '可用' : scope.row.issue === 'hdr' ? 'HDR（可用）' : scope.row.issue === 'too_short' ? '不可用（不足3秒）' : scope.row.issue_message }}
        </el-tag>
      </template></el-table-column>
    </el-table>
  </el-dialog>
</template>
