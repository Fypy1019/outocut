<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, post, put } from '@/api'
import VoiceManagerDialog from '@/components/VoiceManagerDialog.vue'
import type { AppSettings, VoiceProfile } from '@/types'

const loading = ref(false)
const saving = ref(false)
const testingEcomText = ref(false)
const testingEcomImage = ref(false)
const activePanels = ref<string[]>([])
const logs = ref<string[]>([])
const voiceManagerOpen = ref(false)
const managedVoiceId = ref(localStorage.getItem('outocut-managed-voice-id') || '')
const settings = reactive<AppSettings>({
  output_directory: '', cache_directory: '', asset_root_directory: '', max_concurrent_jobs: 1,
  default_width: 1080, default_height: 1920, default_fps: 30, default_codec: 'libx264', default_quality: 18,
  bailian_base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  ecom_text_api_mode: 'chat-completions', ecom_text_base_url: 'https://api.openai.com/v1', ecom_text_model: 'gpt-4o',
  ecom_text_timeout_seconds: 600,
  ecom_image_api_mode: 'images', ecom_image_base_url: 'https://api.openai.com/v1', ecom_image_model: 'gpt-image-2',
  ecom_search_enabled: false,
  minimax_base_url: 'https://api.minimaxi.com/v1',
  deepseek_base_url: 'https://api.deepseek.com', default_model: 'qwen-plus', rewrite_provider: 'bailian',
  default_voice_model: 'speech-2.8-hd', asr_provider: 'bailian', asr_model: 'paraformer-realtime-v2', rewrite_instructions: '', configured: {},
})
const secrets = reactive({ bailian_api_key: '', ecom_text_api_key: '', ecom_image_api_key: '', ecom_tavily_api_key: '', minimax_api_key: '', deepseek_api_key: '', douyin_cookie: '' })

interface AsrModelStatus {
  provider: string
  asr_model: string
  asr_models: string[]
  model_dir: string
  ready: boolean
  asr_ready: boolean
  punctuation_ready: boolean
  missing_files: string[]
  size_bytes: number
  sherpa_available: boolean
}
const asrModel = ref<AsrModelStatus | null>(null)
const DEFAULT_ASR_MODELS = ['paraformer-realtime-v2', 'paraformer-realtime-v1']
const asrModelOptions = computed(() =>
  asrModel.value?.asr_models?.length ? asrModel.value.asr_models : DEFAULT_ASR_MODELS,
)
const asrDownloading = ref(false)
const REWRITE_MODELS: Record<string, string[]> = {
  bailian: ['qwen-plus', 'qwen-turbo', 'qwen-max', 'qwen-long'],
  deepseek: ['deepseek-v4-flash', 'deepseek-v4-pro', 'deepseek-chat', 'deepseek-reasoner'],
}
const rewriteModelOptions = computed(() => REWRITE_MODELS[settings.rewrite_provider] || REWRITE_MODELS.bailian)
function onRewriteProviderChange() {
  if (!rewriteModelOptions.value.includes(settings.default_model)) settings.default_model = rewriteModelOptions.value[0]
}

async function loadAsrModel() {
  try { asrModel.value = await api<AsrModelStatus>('/asr/model') } catch { asrModel.value = null }
}

function formatSize(bytes: number): string {
  if (bytes <= 0) return '0 B'
  const mb = bytes / (1024 * 1024)
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(0)} MB`
}

async function downloadAsrModel() {
  if (asrDownloading.value) return
  asrDownloading.value = true
  try {
    asrModel.value = await post<AsrModelStatus>('/asr/model/download')
    ElMessage.success('本地识别模型已下载并就绪')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '模型下载失败')
  } finally { asrDownloading.value = false }
}

const resolution = computed({
  get: () => `${settings.default_width}x${settings.default_height}`,
  set: (value: string) => {
    const [width, height] = value.split('x').map(Number)
    settings.default_width = width
    settings.default_height = height
  },
})

async function load() {
  loading.value = true
  try { Object.assign(settings, await api<AppSettings>('/settings')) }
  finally { loading.value = false }
}

async function persistSettings(notify: boolean) {
  saving.value = true
  try {
    const secretPayload: Record<string, string> = {}
    if (secrets.bailian_api_key) secretPayload.bailian_api_key = secrets.bailian_api_key
    if (secrets.ecom_text_api_key) secretPayload.ecom_text_api_key = secrets.ecom_text_api_key
    if (secrets.ecom_image_api_key) secretPayload.ecom_image_api_key = secrets.ecom_image_api_key
    if (secrets.ecom_tavily_api_key) secretPayload.ecom_tavily_api_key = secrets.ecom_tavily_api_key
    if (secrets.minimax_api_key) secretPayload.minimax_api_key = secrets.minimax_api_key
    if (secrets.deepseek_api_key) secretPayload.deepseek_api_key = secrets.deepseek_api_key
    if (secrets.douyin_cookie) secretPayload.douyin_cookie = secrets.douyin_cookie
    if (Object.keys(secretPayload).length) settings.configured = await post('/settings/secrets', secretPayload)
    Object.assign(settings, await put('/settings', settings))
    secrets.bailian_api_key = ''
    secrets.ecom_text_api_key = ''
    secrets.ecom_image_api_key = ''
    secrets.ecom_tavily_api_key = ''
    secrets.minimax_api_key = ''
    secrets.deepseek_api_key = ''
    secrets.douyin_cookie = ''
    if (notify) ElMessage.success('系统设置已保存')
  } finally { saving.value = false }
}

async function save() {
  await persistSettings(true)
}

async function testEcomTextConnection() {
  if (testingEcomText.value) return
  testingEcomText.value = true
  try {
    await persistSettings(false)
    const result = await post<{ text: string }>('/ecom-design/text', {
      prompt: '你好，请只回复“连接成功”。',
      system_prompt: '',
      max_tokens: 50,
      temperature: 0,
    })
    ElMessage.success(`文本模型连接成功：${result.text.slice(0, 40)}`)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '文本模型连接失败')
  } finally { testingEcomText.value = false }
}

async function testEcomImageConnection() {
  if (testingEcomImage.value) return
  testingEcomImage.value = true
  try {
    await persistSettings(false)
    await post('/ecom-design/image', {
      prompt: 'A simple blue circle centered on a plain white background.',
      image_base64: null,
      size: '1024x1024',
      quality: 'high',
      output_format: 'png',
    })
    ElMessage.success('图片模型连接成功')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '图片模型连接失败')
  } finally { testingEcomImage.value = false }
}

async function refreshLogs() {
  logs.value = await window.outocut?.engineLogs() || []
}

async function copyLogs() {
  if (!logs.value.length) return ElMessage.warning('暂无日志可复制')
  await navigator.clipboard.writeText(logs.value.join('\n'))
  ElMessage.success('日志已复制')
}

async function clearLogs() {
  await window.outocut?.clearEngineLogs()
  logs.value = []
  ElMessage.success('当前日志视图已清空')
}

function resetRewriteInstructions() {
  settings.rewrite_instructions = '更像真实本地商家口播，语言自然接地气，不要太广告腔，避免夸张承诺。'
}

async function openVoiceManager() {
  if (secrets.minimax_api_key.trim()) await save()
  else Object.assign(settings, await put('/settings', settings))
  if (!settings.configured.minimax) return ElMessage.warning('请先填写并保存 MiniMax Key')
  voiceManagerOpen.value = true
}

function selectManagedVoice(voice: VoiceProfile) {
  managedVoiceId.value = voice.voice_id
  localStorage.setItem('outocut-managed-voice-id', voice.voice_id)
}

onMounted(() => { load(); loadAsrModel() })
</script>

<template>
  <div class="settings-page">
    <div class="settings-save-row"><el-button type="primary" size="large" :loading="saving" @click="save">保存系统设置</el-button></div>

    <section class="panel settings-section-card">
      <div class="settings-card-title"><h2>剪辑设置</h2></div>
      <el-form class="compact-settings-form" label-width="100px">
        <el-form-item label="预设分辨率">
          <el-select v-model="resolution">
            <el-option label="1080 × 1920（竖屏）" value="1080x1920" />
            <el-option label="1920 × 1080（横屏）" value="1920x1080" />
            <el-option label="1080 × 1080（方形）" value="1080x1080" />
          </el-select>
        </el-form-item>
        <el-form-item label="帧率"><el-input-number v-model="settings.default_fps" :min="15" :max="60" /></el-form-item>
        <el-form-item label="编码器">
          <el-select v-model="settings.default_codec">
            <el-option label="普通剪辑（CPU）" value="libx264" />
            <el-option label="快速剪辑（GPU）h264_nvenc" value="h264_nvenc" />
          </el-select>
        </el-form-item>
        <el-form-item label="视频质量">
          <el-select v-model="settings.default_quality">
            <el-option label="低档（1M）" :value="28" />
            <el-option label="中档（3M）" :value="23" />
            <el-option label="高档（5M）" :value="18" />
          </el-select>
        </el-form-item>
        <el-form-item label="媒体处理并发">
          <el-input-number v-model="settings.max_concurrent_jobs" :min="1" :max="4" />
          <span class="field-help">混剪、分辨率转换和水印/贴纸共用此上限；CPU 编码线程会按并发数自动分配，并始终为系统保留至少 2 个逻辑核心。</span>
        </el-form-item>
      </el-form>
    </section>

    <section class="panel settings-section-card">
      <div class="settings-card-title"><h2>文案配置设置</h2></div>
      <el-form class="copy-config-form" label-width="100px">
        <el-form-item label="MiniMax Key">
          <el-input v-model="secrets.minimax_api_key" type="password" show-password :placeholder="settings.configured.minimax ? '已安全配置，留空表示不修改' : '输入 MiniMax API Key'" />
          <a class="config-link" href="https://platform.minimaxi.com/console/access?tab=api-keys" target="_blank" rel="noopener">去配置</a>
        </el-form-item>
        <el-form-item label="MiniMax 区域">
          <el-select v-model="settings.minimax_base_url">
            <el-option label="中国站（minimaxi.com）" value="https://api.minimaxi.com/v1" />
            <el-option label="国际站（minimax.io）" value="https://api.minimax.io/v1" />
          </el-select>
          <span class="field-help">请选择 API Key 所属站点，保存后再打开音色管理。</span>
        </el-form-item>
        <el-form-item label="音色管理">
          <el-button type="primary" plain @click="openVoiceManager">音色管理</el-button>
          <span class="field-help">查看已保存音色，或添加新的复刻音色。</span>
        </el-form-item>
        <el-form-item label="文案改写模型">
          <el-select v-model="settings.rewrite_provider" style="width: 200px" @change="onRewriteProviderChange">
            <el-option label="阿里云百炼" value="bailian" />
            <el-option label="DeepSeek 官方" value="deepseek" />
          </el-select>
          <el-select v-model="settings.default_model" style="width: 280px" filterable allow-create placeholder="选择或输入模型名称">
            <el-option v-for="model in rewriteModelOptions" :key="model" :label="model" :value="model" />
          </el-select>
          <span class="field-help">选择文案改写使用的供应商与具体模型；保存后模板创作与改写采用所选供应商与模型。</span>
        </el-form-item>
        <el-form-item label="百炼 Key">
          <el-input v-model="secrets.bailian_api_key" type="password" show-password :placeholder="settings.configured.bailian ? '已安全配置，留空表示不修改' : '输入阿里云百炼 API Key'" />
          <a class="config-link" href="https://bailian.console.aliyun.com/cn-beijing?spm=5176.38070734.nav-v2-dropdown-menu-0.d_main_2_0.32a134c9OFm7LG&amp;tab=model&amp;scm=20140722.M_10944401._.V_1#/api-key" target="_blank" rel="noopener">去配置</a>
          <span class="field-help">用于文案改写和抖音口播转写；电商设计台在下方使用独立的文本、图片和 Tavily 配置。</span>
        </el-form-item>
        <el-form-item label="DeepSeek Key">
          <el-input v-model="secrets.deepseek_api_key" type="password" show-password :disabled="settings.rewrite_provider === 'bailian'" :placeholder="settings.configured.deepseek ? '已安全配置，留空表示不修改' : '输入 DeepSeek API Key'" />
          <a class="config-link" href="https://platform.deepseek.com/api_keys" target="_blank" rel="noopener">去配置</a>
          <span class="field-help">用于文案改写（与百炼二选一），不影响抖音链接口播语音转写。</span>
        </el-form-item>
        <el-form-item label="抖音 Cookie">
          <el-input v-model="secrets.douyin_cookie" type="password" show-password :placeholder="settings.configured.douyin ? '已安全配置，留空表示不修改' : '粘贴抖音网页版 Cookie（可选）'" />
          <span class="field-help">提取抖音链接文案被风控拦截时需要；打开抖音网页版登录后，从浏览器复制 Cookie 粘贴到这里。</span>
        </el-form-item>
        <el-form-item label="口播转写">
          <el-select v-model="settings.asr_provider" style="width: 220px">
            <el-option label="百炼 API（需联网）" value="bailian" />
            <el-option label="本地识别（离线）" value="local" />
          </el-select>
          <span class="field-help">抖音链接提取口播文案时的语音转写方式。百炼模式使用 paraformer-realtime-v2（WebSocket 实时识别，自带标点断句），长音频自动分片；直连失败时自动尝试系统代理。</span>
        </el-form-item>
        <el-form-item v-if="settings.asr_provider === 'bailian'" label="识别模型">
          <el-select v-model="settings.asr_model" style="width: 300px">
            <el-option v-for="model in asrModelOptions" :key="model" :label="model" :value="model" />
          </el-select>
          <span class="field-help">以下为已实测、配置百炼 Key 即可直接使用的转写模型；paraformer-v2/v1 等录音文件识别模型需百炼 Key 具备 OSS 文件访问权限，暂未列出（后端已保留支持）。</span>
        </el-form-item>
        <el-form-item v-if="settings.asr_provider === 'local'" label="本地模型">
          <div style="display: flex; flex-direction: column; gap: 6px; align-items: flex-start;">
            <div v-if="asrModel" style="display: flex; gap: 8px; align-items: center;">
              <el-button v-if="asrModel.ready" class="model-ready-badge" type="primary" plain>模型已就绪</el-button>
              <el-tag v-else class="state-tag is-warning">模型未下载</el-tag>
              <span class="field-help model-dir-help" :title="asrModel.model_dir">模型目录：{{ asrModel.model_dir }}（约 {{ formatSize(asrModel.size_bytes) }}）</span>
            </div>
            <span v-if="asrModel && !asrModel.ready" class="field-help">需下载 paraformer-zh 中文识别模型（约 234MB）与标点断句模型（约 72MB），下载后即可离线转写并自动添加标点。</span>
            <el-button v-if="!asrModel?.ready" type="primary" plain :loading="asrDownloading" @click="downloadAsrModel">下载本地识别模型</el-button>
            <el-button v-else type="primary" plain disabled>已下载</el-button>
          </div>
        </el-form-item>
      </el-form>
    </section>

    <section class="panel settings-section-card">
      <div class="settings-card-title"><h2>生成套图配置</h2></div>
      <el-form class="copy-config-form" label-width="120px">
        <div class="ecom-config-group"><b>文本 LLM</b><span>用于竞品分析、变量推荐、差异化策略、提示词生成和自由对话</span></div>
        <el-form-item label="接口协议">
          <el-select v-model="settings.ecom_text_api_mode" style="width: 280px">
            <el-option label="Chat Completions API" value="chat-completions" />
            <el-option label="Responses API" value="responses" />
            <el-option label="Claude Messages API" value="claude" />
          </el-select>
        </el-form-item>
        <el-form-item label="文本 Base URL">
          <el-input v-model="settings.ecom_text_base_url" placeholder="例如 https://api.openai.com/v1" />
          <span class="field-help">填写到版本根路径，系统会按协议追加 /chat/completions、/responses 或 /messages。</span>
        </el-form-item>
        <el-form-item label="文本 API Key">
          <el-input v-model="secrets.ecom_text_api_key" type="password" show-password :placeholder="settings.configured.ecom_text ? '文本 Key 已配置，留空表示不修改' : '输入文本 LLM API Key'" />
        </el-form-item>
        <el-form-item label="文本模型">
          <el-input v-model="settings.ecom_text_model" placeholder="例如 gpt-4o、qwen-plus、deepseek-chat" />
        </el-form-item>
        <el-form-item label="请求超时">
          <el-input-number v-model="settings.ecom_text_timeout_seconds" :min="60" :max="1800" :step="60" />
          <span class="field-help">单位：秒。默认 600 秒；竞品分析为避免长时间卡死，单独限制为最多 300 秒。</span>
        </el-form-item>
        <el-form-item label="连接测试">
          <el-button :loading="testingEcomText" @click="testEcomTextConnection">保存配置并测试文本模型</el-button>
        </el-form-item>

        <div class="ecom-config-group"><b>图片 LLM</b><span>根据可编辑提示词和商品原图执行图片生成或图生图</span></div>
        <el-form-item label="图片接口协议">
          <el-select v-model="settings.ecom_image_api_mode" style="width: 280px">
            <el-option label="Images API" value="images" />
            <el-option label="Responses API（图片工具）" value="responses" />
          </el-select>
        </el-form-item>
        <el-form-item label="图片 Base URL">
          <el-input v-model="settings.ecom_image_base_url" placeholder="例如 https://api.openai.com/v1" />
          <span class="field-help">Images模式会追加 /images/generations 或 /images/edits；Responses模式会追加 /responses。</span>
        </el-form-item>
        <el-form-item label="图片 API Key">
          <el-input v-model="secrets.ecom_image_api_key" type="password" show-password :placeholder="settings.configured.ecom_image ? '图片 Key 已配置，留空表示不修改' : '输入图片 LLM API Key'" />
        </el-form-item>
        <el-form-item label="图片模型">
          <el-input v-model="settings.ecom_image_model" :placeholder="settings.ecom_image_api_mode === 'images' ? '例如 gpt-image-2' : '填写支持图片生成工具的模型'" />
        </el-form-item>
        <el-form-item label="连接测试">
          <el-button :loading="testingEcomImage" @click="testEcomImageConnection">保存配置并生成 1 张测试图</el-button>
          <span class="field-help">该测试会真实调用图片模型，可能产生一次图片生成费用。</span>
        </el-form-item>

        <div class="ecom-config-group"><b>联网竞品搜索</b><span>对应六步工作流中的自动竞品研究，可关闭后改用手动输入</span></div>
        <el-form-item label="启用 Tavily">
          <el-switch v-model="settings.ecom_search_enabled" />
        </el-form-item>
        <el-form-item label="Tavily API Key">
          <el-input v-model="secrets.ecom_tavily_api_key" type="password" show-password :disabled="!settings.ecom_search_enabled" :placeholder="settings.configured.ecom_tavily ? 'Tavily Key 已配置，留空表示不修改' : '输入 Tavily API Key'" />
        </el-form-item>
        <el-alert type="info" :closable="false" show-icon title="所有 Key 均由 OutoCut 本地引擎加密保存，不再保存在生成套图页面的浏览器存储中。" />
      </el-form>
    </section>

    <el-collapse v-model="activePanels" class="settings-collapse" @change="(names: string[]) => names.includes('logs') && refreshLogs()">
      <el-collapse-item name="rewrite">
        <template #title><div class="collapse-heading"><b>大模型改写配置</b><span>只配置可调整的改写要求，底层规则由系统自动处理</span></div></template>
        <div class="collapse-content-card">
          <div class="rewrite-tip"><span>可填写目标语言、语气、行业表达、禁用词等补充要求；行数、格式和变量规则不需要手动设置。</span><el-button @click="resetRewriteInstructions">恢复默认</el-button></div>
          <span class="field-help">改写使用的供应商与模型在“文案配置设置”的“文案改写模型”中指定；此处只填写改写补充要求。</span>
          <el-input v-model="settings.rewrite_instructions" type="textarea" :rows="5" placeholder="填写大模型改写补充要求" />
        </div>
      </el-collapse-item>
      <el-collapse-item name="logs">
        <template #title><div class="collapse-heading"><b>日志</b><span>默认收起，可展开查看或复制</span></div></template>
        <div class="collapse-content-card log-panel">
          <div class="toolbar"><el-button @click="refreshLogs">刷新</el-button><el-button @click="copyLogs">复制</el-button><el-button type="danger" plain @click="clearLogs">清空</el-button></div>
          <el-input type="textarea" :model-value="logs.length ? logs.join('\n') : '暂无日志'" :rows="11" readonly />
        </div>
      </el-collapse-item>
    </el-collapse>
    <VoiceManagerDialog v-model="voiceManagerOpen" :selected-voice-id="managedVoiceId" @select="selectManagedVoice" />
  </div>
</template>
