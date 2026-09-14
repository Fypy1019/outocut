<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { EditPen, Film, FolderOpened, HomeFilled, MagicStick, Monitor, Picture, Setting, UploadFilled, VideoPlay } from '@element-plus/icons-vue'
import { useAppStore } from '@/stores/app'

const route = useRoute()
const store = useAppStore()
const title = computed(() => String(route.meta.title || 'OutoCut'))

onMounted(() => store.refreshHealth())
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark">O</span>
        <div><strong>OutoCut</strong><small>智能混剪工作台</small></div>
      </div>
      <nav class="nav-list">
        <RouterLink to="/"><el-icon><HomeFilled /></el-icon><span>工作台</span></RouterLink>
        <RouterLink to="/assets"><el-icon><FolderOpened /></el-icon><span>素材中心</span></RouterLink>
        <RouterLink to="/editor"><el-icon><Film /></el-icon><span>镜头剪辑</span></RouterLink>
        <RouterLink to="/jobs"><el-icon><VideoPlay /></el-icon><span>任务中心</span></RouterLink>
        <RouterLink to="/resolutions"><el-icon><Monitor /></el-icon><span>分辨率转换</span></RouterLink>
        <RouterLink to="/overlays"><el-icon><Picture /></el-icon><span>水印/贴纸</span></RouterLink>
        <RouterLink to="/rename"><el-icon><EditPen /></el-icon><span>批量重命名</span></RouterLink>
        <RouterLink to="/qianchuan-upload"><el-icon><UploadFilled /></el-icon><span>千川上传</span></RouterLink>
        <RouterLink to="/product-set"><el-icon><MagicStick /></el-icon><span>生成套图</span></RouterLink>
        <RouterLink to="/settings"><el-icon><Setting /></el-icon><span>系统设置</span></RouterLink>
      </nav>
      <div class="engine-card" :class="{ offline: !store.online }">
        <span class="status-dot" />
        <div><strong>{{ store.online ? '本地引擎已就绪' : '本地引擎未连接' }}</strong><small>{{ store.health?.ffmpeg_version || store.error || '正在检测…' }}</small></div>
      </div>
    </aside>
    <main class="main-area">
      <header class="topbar">
        <div><p class="eyebrow">OUTOCUT STUDIO</p><h1>{{ title }}</h1></div>
        <div class="topbar-meta" v-if="store.health">
          <span>{{ store.health.nvenc ? 'GPU 加速可用' : 'CPU 编码' }}</span>
          <span>剩余 {{ (store.health.disk_free_bytes / 1024 ** 3).toFixed(1) }} GB</span>
        </div>
      </header>
      <section class="page-content">
        <RouterView v-slot="{ Component }">
          <KeepAlive include="OverlayView,ResolutionView,ProductSetView">
            <component :is="Component" />
          </KeepAlive>
        </RouterView>
      </section>
    </main>
  </div>
</template>
