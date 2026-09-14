<script setup lang="ts">
import { onActivated, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

defineOptions({ name: 'ProductSetView' })

const router = useRouter()
const frameUrl = ref('')
const loadError = ref('')
const frame = ref<HTMLIFrameElement | null>(null)

async function receiveMessage(event: MessageEvent) {
  if (event.source !== frame.value?.contentWindow) return
  if (event.data?.type === 'outocut-open-settings') void router.push('/settings')
  if (event.data?.type === 'outocut-competitor-browser') {
    const requestId = String(event.data.requestId || '')
    try {
      if (!window.outocut) throw new Error('当前环境不支持商品页采集')
      const action = String(event.data.action || '')
      const result = action === 'open'
        ? await window.outocut.competitorOpen(String(event.data.url || ''))
        : action === 'collect'
          ? await window.outocut.competitorCollect()
          : await window.outocut.competitorStatus()
      frame.value?.contentWindow?.postMessage({
        type: 'outocut-competitor-browser-result', requestId, result,
      }, '*')
    } catch (error) {
      frame.value?.contentWindow?.postMessage({
        type: 'outocut-competitor-browser-result', requestId,
        error: error instanceof Error ? error.message : String(error),
      }, '*')
    }
  }
  if (event.data?.type === 'outocut-save-generated-image') {
    const requestId = String(event.data.requestId || '')
    try {
      if (!window.outocut) throw new Error('当前环境不支持系统文件保存')
      const result = await window.outocut.saveGeneratedImage({
        source: String(event.data.source || ''),
        suggestedName: String(event.data.suggestedName || '生成图片.png'),
        outputDirectory: String(event.data.outputDirectory || '') || undefined,
        relativeDirectory: String(event.data.relativeDirectory || '') || undefined,
      })
      frame.value?.contentWindow?.postMessage({
        type: 'outocut-save-generated-image-result',
        requestId,
        ...result,
      }, '*')
    } catch (error) {
      frame.value?.contentWindow?.postMessage({
        type: 'outocut-save-generated-image-result',
        requestId,
        saved: false,
        error: error instanceof Error ? error.message : String(error),
      }, '*')
    }
  }
  if (event.data?.type === 'outocut-select-replacement-output-directory') {
    const requestId = String(event.data.requestId || '')
    try {
      if (!window.outocut) throw new Error('当前环境不支持系统目录选择')
      const path = await window.outocut.selectDirectory()
      frame.value?.contentWindow?.postMessage({
        type: 'outocut-select-replacement-output-directory-result', requestId, path,
      }, '*')
    } catch (error) {
      frame.value?.contentWindow?.postMessage({
        type: 'outocut-select-replacement-output-directory-result', requestId,
        error: error instanceof Error ? error.message : String(error),
      }, '*')
    }
  }
}

onMounted(async () => {
  window.addEventListener('message', receiveMessage)
  try {
    const connection = window.outocut
      ? await window.outocut.engineConnection()
      : { baseUrl: 'http://127.0.0.1:35006', token: 'development-token' }
    const url = new URL('./ecom-design-desk/index.html', window.location.href)
    url.searchParams.set('engineBase', connection.baseUrl)
    url.searchParams.set('token', connection.token)
    frameUrl.value = url.href
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : String(error)
  }
})

onBeforeUnmount(() => window.removeEventListener('message', receiveMessage))

onActivated(() => {
  frame.value?.contentWindow?.postMessage({ type: 'outocut-refresh-settings' }, '*')
})
</script>

<template>
  <div class="ecom-design-host">
    <div v-if="loadError" class="panel ecom-design-error">
      <h3>电商设计台加载失败</h3>
      <p>{{ loadError }}</p>
    </div>
    <iframe
      v-else-if="frameUrl"
      ref="frame"
      :src="frameUrl"
      class="ecom-design-frame"
      title="电商设计台"
      allow="clipboard-read; clipboard-write"
    />
  </div>
</template>

<style scoped>
.ecom-design-host {
  width: 100%;
  height: calc(100vh - 32px);
  min-height: 680px;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: #0b1020;
}

.ecom-design-frame {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
  background: #0b1020;
}

.ecom-design-error {
  margin: 24px;
  color: var(--danger);
}
</style>
