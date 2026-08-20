<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, post } from '@/api'
import type { VoiceCloneResult, VoiceProfile } from '@/types'

const props = defineProps<{ modelValue: boolean; selectedVoiceId?: string | null }>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  select: [voice: VoiceProfile]
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})
const activeTab = ref<'list' | 'clone'>('list')
const voices = ref<VoiceProfile[]>([])
const loading = ref(false)
const cloning = ref(false)
const previewingId = ref('')
const previewUrl = ref('')
const search = ref('')
const voiceType = ref<'all' | VoiceProfile['type']>('all')
const previewText = ref('欢迎使用 OutoCut，这是一段 MiniMax 音色试听。')
const cloneForm = reactive({
  source_path: '',
  voice_name: '',
  voice_id: '',
  preview_text: '欢迎使用 OutoCut，声音复刻测试成功。',
  model: 'speech-2.8-hd',
})

const filteredVoices = computed(() => {
  const keyword = search.value.trim().toLocaleLowerCase()
  return voices.value.filter((voice) => {
    if (voiceType.value !== 'all' && voice.type !== voiceType.value) return false
    if (!keyword) return true
    return `${voice.voice_name} ${voice.voice_id}`.toLocaleLowerCase().includes(keyword)
  })
})

const typeLabels: Record<VoiceProfile['type'], string> = {
  system_voice: '系统音色',
  voice_cloning: '复刻音色',
  voice_generation: '生成音色',
}

async function loadVoices(showMessage = false) {
  loading.value = true
  try {
    voices.value = await api<VoiceProfile[]>('/voices')
    if (showMessage) ElMessage.success(`已刷新 ${voices.value.length} 个 MiniMax 音色`)
  } catch (error) {
    voices.value = []
    ElMessage.error(error instanceof Error ? error.message : String(error))
  } finally { loading.value = false }
}

async function chooseCloneFile() {
  const path = await window.outocut?.selectFile([
    { name: '复刻音频', extensions: ['mp3', 'm4a', 'wav'] },
  ])
  if (path) cloneForm.source_path = path
}

function selectVoice(voice: VoiceProfile) {
  emit('select', voice)
  ElMessage.success(`已选择音色：${voice.voice_name}`)
}

async function playVoice(voice: VoiceProfile) {
  if (!previewText.value.trim()) return ElMessage.warning('请填写试听文本')
  previewingId.value = voice.voice_id
  try {
    let path = voice.preview_path || ''
    if (!path) {
      const result = await post<{ path: string }>('/tts', {
        text: previewText.value.trim(),
        voice_id: voice.voice_id,
        model: 'speech-2.8-hd',
        speed: 1,
      })
      path = result.path
      voice.preview_path = path
    }
    previewUrl.value = await window.outocut?.pathToFileUrl(path) || ''
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error))
  } finally { previewingId.value = '' }
}

async function cloneVoice() {
  const id = cloneForm.voice_id.trim()
  if (!cloneForm.source_path) return ElMessage.warning('请选择用于复刻的音频文件')
  if (!cloneForm.voice_name.trim()) return ElMessage.warning('请填写音色名称')
  if (!/^[A-Za-z][A-Za-z0-9_-]{7,255}$/.test(id) || /[-_]$/.test(id)) {
    return ElMessage.warning('voice_id 至少8位，以字母开头，且不能以横线或下划线结尾')
  }
  if (!cloneForm.preview_text.trim()) return ElMessage.warning('请填写复刻试听文本')
  cloning.value = true
  try {
    const result = await post<VoiceCloneResult>('/voices/clone', {
      ...cloneForm,
      voice_name: cloneForm.voice_name.trim(),
      voice_id: id,
      preview_text: cloneForm.preview_text.trim(),
    })
    await loadVoices()
    const saved = voices.value.find((voice) => voice.voice_id === result.voice.voice_id) || result.voice
    emit('select', saved)
    if (saved.preview_path) previewUrl.value = await window.outocut?.pathToFileUrl(saved.preview_path) || ''
    if (result.preview_error) ElMessage.warning(`音色已保存，但试听生成失败：${result.preview_error}`)
    else ElMessage.success('声音复刻完成，音色和试听文件已保存')
    activeTab.value = 'list'
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error))
  } finally { cloning.value = false }
}

watch(() => props.modelValue, (open) => {
  if (open) void loadVoices()
  else previewUrl.value = ''
})
</script>

<template>
  <el-dialog v-model="visible" title="MiniMax 音色管理" width="900px" class="voice-manager-dialog" destroy-on-close>
    <div class="dialog-tip">读取当前 MiniMax 账号的真实音色。试听和复刻预览会调用 T2A，并按 MiniMax 规则计费。</div>
    <el-tabs v-model="activeTab" class="voice-manager-tabs">
      <el-tab-pane label="音色列表" name="list">
        <div class="voice-manager-toolbar">
          <el-input v-model="search" clearable placeholder="搜索音色名称或 voice_id" />
          <el-select v-model="voiceType">
            <el-option label="全部类型" value="all" />
            <el-option label="系统音色" value="system_voice" />
            <el-option label="复刻音色" value="voice_cloning" />
            <el-option label="生成音色" value="voice_generation" />
          </el-select>
          <el-button type="primary" plain :loading="loading" @click="loadVoices(true)">刷新</el-button>
        </div>
        <div class="voice-preview-text"><span>试听文本</span><el-input v-model="previewText" maxlength="300" /></div>
        <div v-loading="loading" class="voice-profile-list">
          <div v-if="!filteredVoices.length && !loading" class="empty-state">暂无可用音色，请检查 MiniMax Key 或刷新列表</div>
          <article v-for="voice in filteredVoices" :key="voice.voice_id" class="voice-profile-card" :class="{ selected: selectedVoiceId === voice.voice_id }">
            <div class="voice-profile-main">
              <div><strong>{{ voice.voice_name }}</strong><el-tag size="small" effect="plain">{{ typeLabels[voice.type] }}</el-tag></div>
              <code>{{ voice.voice_id }}</code>
              <p v-if="voice.description.length">{{ voice.description.join('；') }}</p>
            </div>
            <div class="voice-profile-actions">
              <el-button type="primary" plain :loading="previewingId === voice.voice_id" @click="playVoice(voice)">试听</el-button>
              <el-button type="primary" :plain="selectedVoiceId !== voice.voice_id" @click="selectVoice(voice)">{{ selectedVoiceId === voice.voice_id ? '已选择' : '选择' }}</el-button>
            </div>
          </article>
        </div>
        <audio v-if="previewUrl" :key="previewUrl" class="voice-audio-player" :src="previewUrl" controls autoplay />
      </el-tab-pane>

      <el-tab-pane label="声音复刻" name="clone">
        <div class="clone-voice-form">
          <label>复刻文件</label>
          <div class="clone-file-row"><el-input v-model="cloneForm.source_path" readonly placeholder="选择 MP3、M4A 或 WAV 文件" /><el-button type="primary" plain @click="chooseCloneFile">选择文件</el-button></div>
          <p class="clone-form-help">音频须为 10 秒至 5 分钟，且不超过 20MB。建议使用单人、无混响、环境安静的清晰人声。</p>
          <label>音色名称</label>
          <el-input v-model="cloneForm.voice_name" maxlength="100" placeholder="例如：品牌主理人" />
          <label>真实 voice_id</label>
          <el-input v-model="cloneForm.voice_id" maxlength="256" placeholder="例如：BrandOwner2026" />
          <p class="clone-form-help">至少8位，以英文字母开头，只能包含字母、数字、-、_，且不能以 - 或 _ 结尾。</p>
          <label>试听文案</label>
          <el-input v-model="cloneForm.preview_text" type="textarea" :rows="4" maxlength="1000" show-word-limit />
          <label>模型</label>
          <el-select v-model="cloneForm.model">
            <el-option label="speech-2.8-hd（高质量）" value="speech-2.8-hd" />
            <el-option label="speech-2.8-turbo（快速）" value="speech-2.8-turbo" />
          </el-select>
          <div class="clone-submit-row"><el-button type="primary" :loading="cloning" @click="cloneVoice">上传、复刻并保存</el-button></div>
        </div>
      </el-tab-pane>
    </el-tabs>
  </el-dialog>
</template>
