<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api'
import type { AssetCategory, JobSummary } from '@/types'

const router = useRouter()
const categories = ref<AssetCategory[]>([])
const jobs = ref<JobSummary[]>([])

const metrics = computed(() => ({
  clips: categories.value.reduce((sum, item) => sum + item.clip_count, 0),
  categories: categories.value.length,
  active: jobs.value.filter((item) => ['queued', 'preparing_audio', 'rendering'].includes(item.state)).length,
  outputs: jobs.value.filter((item) => item.state === 'succeeded').length,
}))

async function refresh() {
  ;[categories.value, jobs.value] = await Promise.all([
    api<AssetCategory[]>('/assets/categories'),
    api<JobSummary[]>('/jobs'),
  ])
}

onMounted(refresh)
</script>

<template>
  <div>
    <div class="page-grid grid-4">
      <div class="panel metric-card"><span class="metric-label">可用素材</span><div class="metric-value">{{ metrics.clips }}</div><span class="metric-foot">跨 {{ metrics.categories }} 个分类</span></div>
      <div class="panel metric-card"><span class="metric-label">正在处理</span><div class="metric-value">{{ metrics.active }}</div><span class="metric-foot">队列与合成任务</span></div>
      <div class="panel metric-card"><span class="metric-label">已生成成片</span><div class="metric-value">{{ metrics.outputs }}</div><span class="metric-foot">历史成功任务</span></div>
      <div class="panel metric-card"><span class="metric-label">工作流状态</span><div class="metric-value success" style="font-size: 24px">准备就绪</div><span class="metric-foot">本地优先 · 数据不出设备</span></div>
    </div>

    <div class="page-grid grid-2 section-gap">
      <div class="panel">
        <div class="panel-header"><div><h2>开始创作</h2><p>从素材到成片的最短路径</p></div></div>
        <div class="page-grid grid-2">
          <el-button size="large" @click="router.push('/assets')">整理素材</el-button>
          <el-button size="large" type="primary" @click="router.push('/editor')">新建混剪任务</el-button>
        </div>
      </div>
      <div class="panel">
        <div class="panel-header"><div><h2>最近任务</h2><p>只显示最新5条</p></div><el-button text @click="router.push('/jobs')">查看全部</el-button></div>
        <div v-if="!jobs.length" class="empty-state">还没有任务<br><small>完成素材扫描后创建第一个混剪任务</small></div>
        <el-table v-else :data="jobs.slice(0, 5)" size="small">
          <el-table-column prop="name" label="任务" min-width="150" />
          <el-table-column label="状态" width="120"><template #default="scope"><span class="job-state" :class="scope.row.state">{{ scope.row.message || scope.row.state }}</span></template></el-table-column>
          <el-table-column label="进度" width="120"><template #default="scope"><el-progress :percentage="Math.round(scope.row.progress * 100)" :stroke-width="6" :show-text="false" /></template></el-table-column>
        </el-table>
      </div>
    </div>
  </div>
</template>
