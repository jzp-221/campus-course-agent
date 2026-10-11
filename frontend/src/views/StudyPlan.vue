<template>
  <div>
    <el-card>
      <template #header>
        <div style="font-size: 16px; font-weight: bold">文本学习计划</div>
      </template>
      <el-form @submit.prevent="handleSave">
        <el-input
          v-model="content"
          type="textarea"
          :rows="4"
          :maxlength="MAX_CONTENT_LENGTH"
          :disabled="saving"
          show-word-limit
          placeholder="填写学习计划并保存，支持 Markdown 标题和列表"
        />
        <div style="margin-top: 10px">
          <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
        </div>
      </el-form>
      <el-divider />
      <el-table
        :data="tableData"
        v-loading="loading"
        empty-text="尚未保存学习计划"
        :header-cell-style="{ backgroundColor: '#f5f7fa', color: '#333' }"
      >
        <el-table-column label="创建时间" width="180">
          <template #default="{ row }">{{ formatTime(row.create_time) }}</template>
        </el-table-column>
        <el-table-column label="计划内容" min-width="280">
          <template #default="{ row }">
            <div class="plan-preview">{{ previewText(row.content) }}</div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link @click="openDetail(row)">查看</el-button>
            <el-button
              type="primary"
              link
              :disabled="deletingId !== null || editingId !== null"
              @click="openEdit(row)"
            >编辑</el-button>
            <el-button
              type="danger"
              link
              :loading="deletingId === row.id"
              :disabled="deletingId !== null && deletingId !== row.id"
              @click="handleDelete(row)"
            >删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div style="margin-top: 10px; display: flex; justify-content: flex-end">
        <el-pagination
          :current-page="params.page"
          :page-size="params.pageSize"
          :total="total"
          background
          layout="total, prev, pager, next"
          @current-change="handlePageChange"
        />
      </div>
    </el-card>
    <el-divider content-position="left">可勾选任务，与文本计划并存</el-divider>
    <PlanTaskPanel />
    <el-dialog
      v-model="detailVisible"
      title="计划详情"
      width="min(720px, 92vw)"
      destroy-on-close
      append-to-body
    >
      <div class="detail-meta">创建时间：{{ formatTime(detailRow?.create_time) }}</div>
      <div class="plan-md" v-html="detailHtml"></div>
    </el-dialog>
    <el-dialog
      v-model="editVisible"
      title="编辑学习计划"
      width="min(680px, 92vw)"
      destroy-on-close
      append-to-body
    >
      <el-input
        v-model="editContent"
        type="textarea"
        :rows="12"
        :maxlength="MAX_CONTENT_LENGTH"
        :disabled="savingEdit"
        show-word-limit
        placeholder="修改学习计划内容，支持 Markdown 标题和列表"
      />
      <template #footer>
        <el-button :disabled="savingEdit" @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingEdit" @click="submitEdit">保存修改</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
defineOptions({ name: 'StudyPlan' })

import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { createStudyPlan, deleteStudyPlan, listStudyPlans, updateStudyPlan } from '@/api/studyPlan'
import PlanTaskPanel from '@/components/PlanTaskPanel.vue'

const MAX_CONTENT_LENGTH = 2000
const content = ref('')
const saving = ref(false)
const loading = ref(false)
const deletingId = ref(null)
const editVisible = ref(false)
const editingId = ref(null)
const editContent = ref('')
const savingEdit = ref(false)
const params = reactive({ page: 1, pageSize: 10 })
const tableData = ref([])
const total = ref(0)
const detailVisible = ref(false)
const detailRow = ref(null)
let loadVersion = 0

const detailHtml = computed(() => {
  if (!detailRow.value?.content) return ''
  const rawHtml = marked.parse(String(detailRow.value.content).replace(/\r\n/g, '\n'), {
    gfm: true,
    breaks: true,
    async: false
  })
  return DOMPurify.sanitize(rawHtml, { USE_PROFILES: { html: true } })
})

const previewText = (text, maxLength = 120) => {
  if (!text) return '-'
  const plain = String(text)
    .replace(/\r\n/g, '\n')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/\|/g, ' ')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/[*_~`>#\-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  if (!plain) return '-'
  return plain.length > maxLength ? `${plain.slice(0, maxLength)}…` : plain
}

const formatTime = (value) => {
  if (!value) return '-'
  const text = String(value).replace('T', ' ')
  return text.length >= 19 ? text.slice(0, 19) : text
}

const openDetail = (row) => {
  detailRow.value = row
  detailVisible.value = true
}

const load = async () => {
  const version = ++loadVersion
  loading.value = true
  try {
    const res = await listStudyPlans({ page: params.page, page_size: params.pageSize })
    if (version !== loadVersion || res.code !== 200) return
    total.value = res.data?.total || 0
    const lastPage = Math.max(1, Math.ceil(total.value / params.pageSize))
    if (params.page > lastPage) {
      params.page = lastPage
      await load()
      return
    }
    tableData.value = res.data?.list || []
  } catch {
    // 请求拦截器已提示失败；保留当前列表，允许用户重试。
  } finally {
    if (version === loadVersion) loading.value = false
  }
}

const handlePageChange = (page) => {
  if (page === params.page) return
  params.page = page
  load()
}

const handleSave = async () => {
  if (saving.value) return
  const text = content.value.trim()
  if (!text) {
    ElMessage.warning('请填写学习计划内容')
    return
  }
  if (text.length > MAX_CONTENT_LENGTH) {
    ElMessage.warning(`计划内容不能超过 ${MAX_CONTENT_LENGTH} 字`)
    return
  }
  saving.value = true
  try {
    const res = await createStudyPlan(text)
    if (res.code === 200) {
      ElMessage.success('已保存')
      content.value = ''
      params.page = 1
      await load()
    }
  } catch {
    // 请求失败时保留输入内容，错误提示由请求拦截器处理。
  } finally {
    saving.value = false
  }
}

const openEdit = (row) => {
  editingId.value = row.id
  editContent.value = row.content || ''
  editVisible.value = true
}

const submitEdit = async () => {
  if (savingEdit.value || editingId.value === null) return
  const text = editContent.value.trim()
  if (!text) {
    ElMessage.warning('计划内容不能为空')
    return
  }
  if (text.length > MAX_CONTENT_LENGTH) {
    ElMessage.warning(`计划内容不能超过 ${MAX_CONTENT_LENGTH} 字`)
    return
  }
  savingEdit.value = true
  try {
    const res = await updateStudyPlan(editingId.value, text)
    if (res.code === 200) {
      // 详情弹窗可能同时打开且内容已过期，关闭它避免显示旧正文。
      if (detailRow.value?.id === editingId.value) detailVisible.value = false
      ElMessage.success('修改已保存')
      editVisible.value = false
      editingId.value = null
      await load()
    }
  } catch {
    // 失败时保留编辑内容并保持弹窗打开，错误提示由请求拦截器处理。
  } finally {
    savingEdit.value = false
  }
}

const handleDelete = async (row) => {
  if (deletingId.value !== null) return
  deletingId.value = row.id
  try {
    await ElMessageBox.confirm('确认删除这条学习计划？', '确认删除', { type: 'warning' })
    const res = await deleteStudyPlan(row.id)
    if (res.code === 200) {
      ElMessage.success('已删除')
      if (detailRow.value?.id === row.id) detailVisible.value = false
      await load()
    }
  } catch {
    // 取消确认无需提示；请求失败已由请求拦截器提示。
  } finally {
    deletingId.value = null
  }
}

onMounted(load)
</script>

<style scoped>
.plan-preview {
  display: -webkit-box;
  overflow: hidden;
  line-height: 1.5;
  color: #606266;
  word-break: break-word;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.detail-meta {
  margin-bottom: 12px;
  color: #909399;
  font-size: 13px;
}

.plan-md {
  max-height: 60vh;
  overflow: auto;
  padding: 4px 0;
  line-height: 1.6;
  overflow-wrap: anywhere;
}

.plan-md :deep(p),
.plan-md :deep(ul),
.plan-md :deep(ol) {
  margin: 0.35em 0;
}

.plan-md :deep(ul),
.plan-md :deep(ol) {
  padding-left: 1.5em;
}

.plan-md :deep(pre) {
  overflow-x: auto;
  padding: 12px;
  border-radius: 4px;
  background: #f5f7fa;
}

.plan-md :deep(img) {
  max-width: 100%;
}

.plan-md :deep(table) {
  border-collapse: collapse;
}

.plan-md :deep(th),
.plan-md :deep(td) {
  border: 1px solid #dcdfe6;
  padding: 6px 10px;
}
</style>
