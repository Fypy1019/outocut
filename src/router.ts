import { createRouter, createWebHashHistory } from 'vue-router'
import DashboardView from '@/views/DashboardView.vue'
import AssetsView from '@/views/AssetsView.vue'
import EditorView from '@/views/EditorView.vue'
import JobsView from '@/views/JobsView.vue'
import ResolutionView from '@/views/ResolutionView.vue'
import OverlayView from '@/views/OverlayView.vue'
import RenameView from '@/views/RenameView.vue'
import SettingsView from '@/views/SettingsView.vue'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: DashboardView, meta: { title: '工作台' } },
    { path: '/assets', component: AssetsView, meta: { title: '素材中心' } },
    { path: '/editor', component: EditorView, meta: { title: '镜头剪辑' } },
    { path: '/jobs', component: JobsView, meta: { title: '任务中心' } },
    { path: '/resolutions', component: ResolutionView, meta: { title: '分辨率转换' } },
    { path: '/overlays', component: OverlayView, meta: { title: '水印/贴纸' } },
    { path: '/settings', component: SettingsView, meta: { title: '系统设置' } },
    { path: '/rename', component: RenameView, meta: { title: '批量重命名' } },
  ],
})
