<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

const STORAGE_KEY = 'outocut.batch-rename.v1'
const directory = ref('')
const templateName = ref('')
const rule = ref('{模板名称}（{递增序号}）.后缀')
const preview = ref<BatchRenameItem[]>([])
const renaming = ref(false)

try {
  const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
  if (typeof saved.directory === 'string') directory.value = saved.directory
  if (typeof saved.templateName === 'string') templateName.value = saved.templateName
  if (typeof saved.rule === 'string' && saved.rule) rule.value = saved.rule
} catch { /* Ignore invalid old local state. */ }

watch([directory, templateName, rule], () => {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    directory: directory.value,
    templateName: templateName.value,
    rule: rule.value,
  }))
  preview.value = []
})

async function chooseDirectory() {
  const selected = await window.outocut?.selectDirectory()
  if (selected) directory.value = selected
}

function request() {
  return { directory: directory.value, templateName: templateName.value, rule: rule.value }
}

async function createPreview(showMessage = true) {
  if (!window.outocut) {
    ElMessage.warning('批量重命名仅支持桌面客户端')
    return null
  }
  if (!directory.value || !templateName.value.trim() || !rule.value.trim()) {
    ElMessage.warning('请先选择文件夹，并填写模板名称和组合规则')
    return null
  }
  try {
    const result = await window.outocut.previewBatchRename(request())
    preview.value = result.items
    if (showMessage) {
      if (result.items.length) ElMessage.success(`已生成 ${result.items.length} 个文件的重命名预览`)
      else ElMessage.info('所选文件夹中没有可重命名的文件')
    }
    return result
  } catch (error) {
    preview.value = []
    ElMessage.error(error instanceof Error ? error.message : String(error))
    return null
  }
}

async function run() {
  const plan = await createPreview(false)
  if (!plan?.items.length || !window.outocut) return
  try {
    await ElMessageBox.confirm(
      `将直接重命名当前文件夹中的 ${plan.items.length} 个文件，是否继续？`,
      '确认批量重命名',
      { type: 'warning', confirmButtonText: '开始重命名', cancelButtonText: '取消' },
    )
  } catch { return }
  renaming.value = true
  try {
    const result = await window.outocut.executeBatchRename(request())
    preview.value = result.items
    ElMessage.success(`批量重命名完成，共处理 ${result.renamed} 个文件`)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error))
  } finally { renaming.value = false }
}
</script>

<template>
  <div class="rename-page">
    <div class="asset-tip">选择文件夹并设置命名模板；目录、模板名称和组合规则会自动保存。</div>
    <section class="panel settings-section-card batch-rename-card">
      <div class="settings-card-title"><h2>批量重命名</h2></div>
      <div class="batch-rename-body">
        <div class="batch-rename-form">
          <label>目标文件夹</label>
          <el-input v-model="directory" readonly placeholder="选择需要批量重命名文件的文件夹" />
          <el-button type="primary" plain @click="chooseDirectory">选择文件夹</el-button>
          <label>模板名称</label>
          <el-input v-model="templateName" maxlength="100" placeholder="例如：产品展示" />
          <span />
          <label>组合规则</label>
          <el-input v-model="rule" placeholder="{模板名称}（{递增序号}）.后缀" />
          <span />
        </div>
        <p class="batch-rename-help">支持变量：{模板名称}、{递增序号}、后缀。仅处理当前文件夹中的文件，按原文件名排序并保留各自后缀，不扫描子文件夹。</p>
        <div class="batch-rename-actions">
          <el-button type="primary" plain :disabled="renaming" @click="createPreview()">生成预览</el-button>
          <el-button class="batch-rename-start" type="primary" plain :loading="renaming" :disabled="!preview.length" @click="run">开始重命名</el-button>
        </div>
        <el-table v-if="preview.length" :data="preview" max-height="320" class="batch-rename-preview">
          <el-table-column type="index" label="序号" width="70" align="center" />
          <el-table-column prop="original_name" label="原文件名" min-width="260" show-overflow-tooltip />
          <el-table-column prop="new_name" label="新文件名" min-width="260" show-overflow-tooltip />
        </el-table>
      </div>
    </section>
  </div>
</template>
