<template>
  <section v-if="isAdmin" class="document-page" aria-labelledby="document-page-title">
    <el-card class="document-panel">
      <template #header>
        <div class="page-heading">
          <div>
            <h2 id="document-page-title">知识库文档</h2>
            <p>上传课程资料，让学生在助手里获得有来源的回答。</p>
          </div>
          <el-tag type="info" effect="plain">管理员管理</el-tag>
        </div>
      </template>

      <div class="indexing-flow" aria-label="文档入库流程">
        <span v-for="(step, index) in flowSteps" :key="step" class="flow-step">
          <span class="step-number">{{ index + 1 }}</span>{{ step }}
          <span v-if="index < flowSteps.length - 1" class="flow-arrow" aria-hidden="true">→</span>
        </span>
      </div>

      <div class="upload-area">
        <el-upload ref="uploadRef" v-model:file-list="selectedFiles" drag multiple
          :auto-upload="false" :disabled="uploading" :accept="acceptedTypes"
          :on-change="selectionChanged" :on-remove="selectionChanged">
          <el-icon class="upload-icon"><UploadFilled /></el-icon>
          <div class="upload-title">将资料拖到这里，或<span>点击选择文件</span></div>
          <template #tip>
            <p class="upload-tip">支持 .txt / .md / .pdf / .docx；可多选，整批一次上传。任意文件入库失败，整批回滚。</p>
            <p class="upload-tip">PDF 需要有文字层，扫描版 PDF 暂不支持。</p>
          </template>
        </el-upload>
        <div class="upload-toolbar">
          <span class="selected-count">已选择 {{ selectedFiles.length }} 个文件</span>
          <el-button :disabled="uploading || !selectedFiles.length" @click="clearSelection">清空选择</el-button>
          <el-button type="primary" :icon="Upload" :loading="uploading"
            :disabled="!selectedFiles.length || !batchValidation.ok" @click="uploadBatch">整批上传并入库</el-button>
        </div>
        <el-alert v-if="!batchValidation.ok && selectedFiles.length" :title="batchValidation.message"
          type="error" :closable="false" show-icon class="upload-alert" />
        <div v-if="uploading" class="upload-progress" role="status" aria-live="polite">
          <el-progress :percentage="uploadProgress" :indeterminate="uploadProgress >= 100" />
          <p>{{ uploadProgress >= 100 ? '文件发送完成，正在抽取文本、分块与向量化，请稍候…' : '正在上传整批文件…' }}</p>
        </div>
        <el-alert v-if="uploadError" :title="uploadError" type="error" :closable="false"
          show-icon class="upload-alert" />
        <el-alert v-if="uploadResult" :title="uploadResult" type="success" :closable="false"
          show-icon class="upload-alert" />
      </div>

      <div class="list-heading">
        <h3>已上传资料 <span>{{ total }} 份</span></h3>
        <el-button :icon="Refresh" :loading="loading" @click="loadDocuments">刷新列表</el-button>
      </div>
      <el-alert v-if="listError" :title="listError" type="error" :closable="false" show-icon class="list-error" />
      <el-alert v-if="actionError" :title="actionError" type="error" :closable="false" show-icon class="list-error" />
      <el-table v-loading="loading" :data="documents" style="width: 100%"
        :header-cell-style="{ backgroundColor: '#f5f7fa', color: '#333' }">
        <el-table-column prop="filename" label="文件名" min-width="220" show-overflow-tooltip />
        <el-table-column label="入库状态" width="160">
          <template #default="{ row }">
            <el-tooltip :content="row.error_message || documentStatus(row.status).label" placement="top"
              :disabled="!row.error_message">
              <el-tag :type="documentStatus(row.status).type">{{ documentStatus(row.status).label }}</el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="chunk_count" label="分块数" width="90" align="center" />
        <el-table-column label="上传时间" width="180">
          <template #default="{ row }">{{ formatTime(row.create_time) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="270" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" text bg :disabled="pendingIds.has(row.id)" @click="openPreview(row)">预览</el-button>
            <el-button type="primary" text bg :loading="reindexingId === row.id"
              :disabled="isReindexDisabled(row, pendingIds)" @click="reindexDocument(row)">重新入库</el-button>
            <el-button type="danger" text bg :loading="deletingId === row.id"
              :disabled="pendingIds.has(row.id) || row.status === 'processing'" @click="removeDocument(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty v-if="!loading && !listError" description="先上传课程资料，学生才能在助手里提问" :image-size="90" />
          <span v-else>{{ listError ? '文档列表加载失败，请点击刷新列表重试' : '正在加载课程资料…' }}</span>
        </template>
      </el-table>
      <div class="pagination">
        <el-pagination v-model:current-page="params.page" v-model:page-size="params.pageSize"
          :total="total" background layout="total, prev, pager, next" @current-change="loadDocuments" />
      </div>
    </el-card>

    <el-dialog v-model="previewVisible" :title="previewFilename" width="min(900px, 92vw)"
      class="document-preview-dialog" destroy-on-close>
      <div v-loading="previewLoading" class="preview-body">
        <el-alert v-if="previewError" :title="previewError" type="error" :closable="false" show-icon />
        <template v-else-if="preview">
          <div class="preview-meta">
            <el-tag :type="documentStatus(preview.status).type">{{ documentStatus(preview.status).label }}</el-tag>
            <span>{{ preview.chunk_count }} 个分块</span>
            <span>抽取文本 · {{ (preview.extracted_text || '').length.toLocaleString() }} 字符</span>
          </div>
          <pre class="extracted-text" tabindex="0" aria-label="文档抽取文本">{{ preview.extracted_text || '暂无抽取文本' }}</pre>
        </template>
      </div>
      <template #footer><el-button @click="previewVisible = false">关闭</el-button></template>
    </el-dialog>
  </section>
  <el-result v-else icon="warning" title="无权限访问" sub-title="知识库文档管理仅限管理员使用" />
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { Refresh, Upload, UploadFilled } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listDocumentsApi, uploadDocumentsApi, previewDocumentApi, reindexDocumentApi, deleteDocumentApi } from '@/api/document'
import { useUser } from '@/utils/user'
import { SUPPORTED_DOCUMENT_SUFFIXES, validateDocumentFiles, documentStatus, isReindexDisabled,
  submitDocumentBatch, createDocumentActionRunner, documentErrorMessage } from '@/utils/documentManagement'

defineOptions({ name: 'Document' })
const { userInfo } = useUser()
const isAdmin = computed(() => userInfo.value?.role === 'admin')
const flowSteps = ['上传', '抽取文本', '分块', '向量化', '可被助手检索']
const acceptedTypes = SUPPORTED_DOCUMENT_SUFFIXES.join(',')
const selectedFiles = ref([])
const uploadRef = ref(null)
const uploading = ref(false)
const uploadProgress = ref(0)
const uploadError = ref('')
const uploadResult = ref('')
const batchValidation = computed(() => validateDocumentFiles(selectedFiles.value))
const params = reactive({ page: 1, pageSize: 10 })
const documents = ref([])
const total = ref(0)
const loading = ref(false)
const listError = ref('')
const actionError = ref('')
const pendingIds = reactive(new Set())
const runAction = createDocumentActionRunner(pendingIds)
const reindexingId = ref(null)
const deletingId = ref(null)
const previewVisible = ref(false)
const previewFilename = ref('抽取文本预览')
const previewLoading = ref(false)
const previewError = ref('')
const preview = ref(null)
let listRequestId = 0
let previewRequestId = 0
let disposed = false

const formatTime = (value) => value ? value.replace('T', ' ').slice(0, 19) : '—'
const selectionChanged = () => { uploadError.value = ''; uploadResult.value = '' }
const clearSelection = () => {
  if (uploading.value) return
  uploadRef.value?.clearFiles()
  selectedFiles.value = []
  selectionChanged()
}

async function loadDocuments() {
  if (!isAdmin.value || disposed) return
  const requestId = ++listRequestId
  loading.value = true
  listError.value = ''
  try {
    const response = await listDocumentsApi({ page: params.page, page_size: params.pageSize })
    if (disposed || requestId !== listRequestId) return
    if (response.code !== 200) throw new Error(response.message || '文档列表加载失败')
    total.value = response.data?.total || 0
    if (params.page > 1 && params.page > Math.max(1, Math.ceil(total.value / params.pageSize))) {
      params.page = Math.max(1, Math.ceil(total.value / params.pageSize))
      return loadDocuments()
    }
    documents.value = response.data?.list || []
  } catch (error) {
    if (!disposed && requestId === listRequestId) listError.value = documentErrorMessage(error, '文档列表加载失败')
  } finally {
    if (!disposed && requestId === listRequestId) loading.value = false
  }
}

async function uploadBatch() {
  if (!isAdmin.value || uploading.value || disposed) return
  uploadError.value = ''
  uploadResult.value = ''
  if (!batchValidation.value.ok) {
    uploadError.value = batchValidation.value.message
    return
  }
  uploading.value = true
  uploadProgress.value = 0
  const batch = [...selectedFiles.value]
  try {
    const response = await submitDocumentBatch(batch, uploadDocumentsApi, {
      onProgress(event) {
        if (disposed) return
        if (event.total) uploadProgress.value = Math.min(100, Math.round(event.loaded / event.total * 100))
        else if (typeof event.progress === 'number') uploadProgress.value = Math.round(event.progress * 100)
      }
    })
    if (disposed) return
    if (response.code !== 200) throw new Error(response.message || '整批文档入库失败')
    if (!Array.isArray(response.data) || response.data.length !== batch.length) {
      throw new Error('上传结果不完整，暂时无法确认整批入库成功。请刷新列表核对后再重试。')
    }
    const count = response.data.length
    uploadResult.value = `整批 ${count} 份资料已入库，可供助手检索。`
    uploadRef.value?.clearFiles()
    selectedFiles.value = []
    params.page = 1
    await loadDocuments()
  } catch (error) {
    if (!disposed) uploadError.value = documentErrorMessage(error, '整批文档入库失败，请重试')
  } finally {
    if (!disposed) uploading.value = false
  }
}

async function openPreview(row) {
  if (!isAdmin.value || disposed) return
  const requestId = ++previewRequestId
  previewFilename.value = row.filename
  preview.value = null
  previewError.value = ''
  previewVisible.value = true
  previewLoading.value = true
  try {
    const response = await previewDocumentApi(row.id)
    if (disposed || requestId !== previewRequestId) return
    if (response.code !== 200) throw new Error(response.message || '预览加载失败')
    preview.value = response.data
  } catch (error) {
    if (!disposed && requestId === previewRequestId) previewError.value = documentErrorMessage(error, '预览加载失败')
  } finally {
    if (!disposed && requestId === previewRequestId) previewLoading.value = false
  }
}

async function reindexDocument(row) {
  if (!isAdmin.value || disposed || isReindexDisabled(row, pendingIds)) return
  await runAction(row.id, async () => {
    try {
      await ElMessageBox.confirm(`确认重新入库「${row.filename}」？将从原始文件重新抽取文本、分块并更新向量。`, '重新入库', {
        type: 'warning', confirmButtonText: '重新入库', cancelButtonText: '取消'
      })
      if (disposed || !isAdmin.value) return
      actionError.value = ''
      reindexingId.value = row.id
      const oldStatus = row.status
      row.status = 'processing'
      try {
        const response = await reindexDocumentApi(row.id)
        if (response.code !== 200) throw new Error(response.message || '重新入库失败')
        if (!disposed) ElMessage.success(`「${row.filename}」已重新入库`)
      } finally {
        row.status = oldStatus
        await loadDocuments()
      }
    } catch (error) {
      if (error !== 'cancel' && error !== 'close' && !disposed) actionError.value = documentErrorMessage(error, '重新入库失败')
    } finally {
      reindexingId.value = null
    }
  })
}

async function removeDocument(row) {
  if (!isAdmin.value || disposed || pendingIds.has(row.id) || row.status === 'processing') return
  await runAction(row.id, async () => {
    try {
      await ElMessageBox.confirm(`确认删除「${row.filename}」？将删除原始文件和记录，并同时清理该文档的全部向量，删除后助手将无法检索该资料。`, '删除文档', {
        type: 'warning', confirmButtonText: '删除文档', cancelButtonText: '取消'
      })
      if (disposed || !isAdmin.value) return
      actionError.value = ''
      deletingId.value = row.id
      const response = await deleteDocumentApi(row.id)
      if (response.code !== 200) throw new Error(response.message || '删除失败')
      if (!disposed) ElMessage.success(`「${row.filename}」已删除，关联向量已清理`)
      await loadDocuments()
    } catch (error) {
      if (error !== 'cancel' && error !== 'close' && !disposed) actionError.value = documentErrorMessage(error, '删除失败')
    } finally {
      deletingId.value = null
    }
  })
}

onMounted(() => { if (isAdmin.value) loadDocuments() })
onBeforeUnmount(() => { disposed = true; listRequestId++; previewRequestId++ })
</script>

<style scoped>
.document-page { color: var(--el-text-color-primary); }
.document-panel { border-radius: 10px; }
.page-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.page-heading h2 { margin: 0 0 6px; font-size: 18px; }
.page-heading p { margin: 0; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.indexing-flow { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 20px; padding: 14px 16px; background: var(--el-color-primary-light-9); border-radius: 8px; font-size: 13px; }
.flow-step { display: inline-flex; align-items: center; gap: 7px; white-space: nowrap; }
.step-number { display: inline-flex; align-items: center; justify-content: center; width: 21px; height: 21px; border-radius: 50%; background: var(--el-color-primary-light-7); color: var(--el-color-primary); font-size: 12px; }
.flow-arrow { margin-left: 8px; color: var(--el-text-color-placeholder); }
.upload-area { border: 1px solid var(--el-border-color-light); border-radius: 8px; padding: 18px; }
.upload-area :deep(.el-upload) { display: block; }
.upload-area :deep(.el-upload-dragger) { padding: 24px 14px; }
.upload-icon { font-size: 36px; color: var(--el-color-primary); margin-bottom: 10px; }
.upload-title { color: var(--el-text-color-regular); font-size: 14px; }
.upload-title span { color: var(--el-color-primary); margin-left: 5px; }
.upload-tip { margin: 6px 0; font-size: 12px; line-height: 1.6; color: var(--el-text-color-secondary); }
.upload-toolbar { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: 10px; margin-top: 16px; }
.upload-toolbar :deep(.el-button + .el-button) { margin-left: 0; }
.selected-count { margin-right: auto; font-size: 13px; color: var(--el-text-color-secondary); }
.upload-progress { margin-top: 16px; }
.upload-progress p { font-size: 13px; color: var(--el-text-color-secondary); margin-bottom: 0; }
.upload-alert, .list-error { margin-top: 14px; }
.list-heading { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin: 26px 0 14px; }
.list-heading h3 { margin: 0; font-size: 15px; }
.list-heading h3 span { font-size: 12px; color: var(--el-text-color-secondary); font-weight: normal; margin-left: 8px; }
.list-error { margin: 0 0 12px; }
.pagination { display: flex; justify-content: flex-end; margin-top: 18px; overflow-x: auto; }
.preview-body { min-height: 130px; }
.preview-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 14px; margin-bottom: 14px; color: var(--el-text-color-secondary); font-size: 13px; }
.extracted-text { margin: 0; max-height: 58vh; min-height: 120px; overflow: auto; padding: 18px; border-radius: 8px; border: 1px solid var(--el-border-color-light); background: var(--el-fill-color-light); white-space: pre-wrap; overflow-wrap: anywhere; font-family: inherit; font-size: 14px; line-height: 1.85; }
@media (max-width: 600px) {
  .page-heading { align-items: flex-start; }
  .upload-area { padding: 12px; }
  .indexing-flow { gap: 8px; padding: 12px; }
  .selected-count { width: 100%; }
}
</style>
