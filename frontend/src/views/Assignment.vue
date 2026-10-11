<template>
  <section class="assignment-page" aria-labelledby="assignment-title">
    <el-card class="assignment-panel">
      <template #header>
        <div class="page-heading">
          <div><h2 id="assignment-title">作业与考试</h2><p>记录课程要求与截止时间，完成一项，就少一份牵挂。</p></div>
          <el-button type="primary" :icon="Plus" @click="openCreate">新增事项</el-button>
        </div>
      </template>

      <div class="assignment-summary" aria-label="事项统计">
        <div><span>未完成</span><strong>{{ inbox.pendingCount.value }}</strong></div>
        <div class="summary-overdue"><span>已逾期未完成</span><strong>{{ overdueCount }}</strong></div>
        <div class="summary-soon"><span>3 天内到期</span><strong>{{ soonCount }}</strong></div>
      </div>

      <div class="filters" aria-label="筛选作业与考试">
        <el-select v-model="filters.type" aria-label="事项类型" placeholder="全部类型" @change="resetPage">
          <el-option label="全部类型" value="" /><el-option label="作业" value="assignment" /><el-option label="考试" value="exam" />
        </el-select>
        <el-select v-model="filters.status" aria-label="完成状态" placeholder="全部状态" @change="resetPage">
          <el-option label="全部状态" value="" /><el-option label="未完成" value="pending" /><el-option label="已完成" value="done" />
        </el-select>
        <el-select v-model="filters.dueRange" aria-label="到期范围" @change="resetPage">
          <el-option label="全部时间" value="all" /><el-option label="3 天内到期" value="3" />
          <el-option label="7 天内到期" value="7" /><el-option label="已逾期" value="overdue" />
        </el-select>
        <el-button :icon="Refresh" :loading="inbox.loading.value" @click="refresh">刷新</el-button>
      </div>
      <div v-if="selectedCourseId" class="course-filter"><span>当前课程：</span><el-tag closable @close="clearCourseFilter">{{ selectedCourseName }}</el-tag></div>
      <el-alert v-if="inbox.error.value" :title="inbox.error.value" type="error" :closable="false" show-icon class="page-alert" />
      <el-alert v-if="contextError" :title="contextError" type="warning" :closable="false" show-icon class="page-alert">
        <el-button text @click="loadContext">重新加载课程与假期信息</el-button>
      </el-alert>
      <el-alert v-if="actionError" :title="actionError" type="error" :closable="false" show-icon class="page-alert" />

      <el-table v-loading="inbox.loading.value" :data="pageItems" :row-class-name="rowClass"
        :header-cell-style="{ backgroundColor: '#f5f7fa', color: '#333' }" style="width: 100%">
        <el-table-column label="标题" min-width="190">
          <template #default="{ row }"><span class="item-title" :title="row.title">{{ row.title }}</span></template>
        </el-table-column>
        <el-table-column label="类型" width="75">
          <template #default="{ row }"><el-tag :type="row.type === 'exam' ? 'danger' : 'info'" effect="light">{{ row.type === 'exam' ? '考试' : '作业' }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="course_name" label="关联课程" min-width="110" show-overflow-tooltip />
        <el-table-column label="截止 / 考试时间" min-width="230">
          <template #default="{ row }">
            <div class="deadline" :class="`deadline-${dueState(row)}`">{{ formatLocalDateTime(row.due_at) }}</div>
            <div v-if="row.status !== 'done'" class="deadline-state" :class="`deadline-${dueState(row)}`">{{ deadlineLabel(row) }}</div>
            <div v-if="notice(row)" class="deadline-notice">{{ notice(row) }}</div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="95">
          <template #default="{ row }"><el-tag :type="row.status === 'done' ? 'success' : 'warning'" effect="plain">{{ row.status === 'done' ? '已完成' : '未完成' }}</el-tag></template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button text type="success" :disabled="pendingIds.has(row.id)" :loading="pendingIds.has(row.id)" @click="toggleDone(row)">{{ row.status === 'done' ? '取消完成' : '标记完成' }}</el-button>
            <el-button text type="primary" :disabled="pendingIds.has(row.id)" @click="openEdit(row)">编辑</el-button>
            <el-button text type="danger" :disabled="pendingIds.has(row.id)" @click="removeItem(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty v-if="!inbox.loading.value && !inbox.error.value" :description="hasFilters ? '没有符合筛选条件的事项' : '添加作业和考试，重要的截止时间就不会遗漏'" :image-size="90" />
          <span v-else>{{ inbox.error.value ? '加载失败，请点击刷新重试' : '正在加载作业与考试…' }}</span>
        </template>
      </el-table>
      <div class="list-footer"><span>仅展示当前账号的事项</span>
        <el-pagination v-model:current-page="pagination.page" v-model:page-size="pagination.pageSize"
          :total="filteredItems.length" :page-sizes="[10, 20, 50]" background layout="total, sizes, prev, pager, next"
          @size-change="resetPage" />
      </div>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="form.id ? '编辑事项' : '新增作业或考试'" width="min(580px, 94vw)" :close-on-click-modal="!formLoading" :before-close="closeDialog">
      <el-alert v-if="formError" :title="formError" type="error" :closable="false" show-icon class="page-alert" />
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px" :disabled="formLoading">
        <el-form-item label="关联课程" prop="course_id">
          <el-select v-model="form.course_id" filterable placeholder="选择课程" :loading="contextLoading" style="width: 100%">
            <el-option v-for="course in courses" :key="course.id" :label="course.name" :value="course.id" />
          </el-select>
          <span v-if="contextError" class="form-help">课程加载失败，请先关闭弹窗并重新加载。</span>
        </el-form-item>
        <el-form-item label="类型" prop="type"><el-radio-group v-model="form.type"><el-radio value="assignment">作业</el-radio><el-radio value="exam">考试</el-radio></el-radio-group></el-form-item>
        <el-form-item label="标题" prop="title"><el-input v-model="form.title" maxlength="200" show-word-limit placeholder="例如：第 3 章课后习题" /></el-form-item>
        <el-form-item :label="form.type === 'exam' ? '考试时间' : '截止时间'" prop="due_at">
          <el-date-picker v-model="form.due_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" format="YYYY-MM-DD HH:mm" placeholder="选择日期与时间" style="width: 100%" />
          <span v-if="form.due_at && notice(form)" class="form-help">{{ notice(form) }}</span>
        </el-form-item>
        <el-form-item label="状态" prop="status"><el-radio-group v-model="form.status"><el-radio value="pending">未完成</el-radio><el-radio value="done">已完成</el-radio></el-radio-group></el-form-item>
        <el-form-item label="备注" prop="note"><el-input v-model="form.note" type="textarea" :rows="3" maxlength="500" show-word-limit placeholder="填写提交方式、考试范围等" /></el-form-item>
      </el-form>
      <template #footer><el-button :disabled="formLoading" @click="dialogVisible = false">取消</el-button><el-button type="primary" :loading="formLoading" @click="saveItem">保存</el-button></template>
    </el-dialog>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { createAssignmentApi, updateAssignmentApi, deleteAssignmentApi } from '@/api/assignment'
import { getLearningContextApi } from '@/api/learningContext'
import { useLearningInbox } from '@/stores/learningInbox'
import { deadlineNotice, formatLocalDateTime, parseLocalDateTime, remainingDays, dueState, filterAssignments, sortByDue, ensureSuccess } from '@/utils/learningItems'

defineOptions({ name: 'Assignment' })
const inbox = useLearningInbox()
const route = useRoute()
const router = useRouter()
const courses = ref([])
const calendar = ref(null)
const contextError = ref('')
const contextLoading = ref(false)
const actionError = ref('')
const pendingIds = reactive(new Set())
const filters = reactive({ type: '', status: '', dueRange: normalizeDueRange(route.query.due) })
const pagination = reactive({ page: 1, pageSize: 10 })
const selectedCourseId = computed(() => {
  const value = Number(route.query.course_id)
  return Number.isInteger(value) && value > 0 ? value : null
})
const selectedCourseName = computed(() => courses.value.find(course => course.id === selectedCourseId.value)?.name
  || inbox.assignments.value.find(item => item.course_id === selectedCourseId.value)?.course_name || `课程 #${selectedCourseId.value}`)
const filteredItems = computed(() => sortByDue(filterAssignments(inbox.assignments.value, filters)
  .filter(item => !selectedCourseId.value || item.course_id === selectedCourseId.value)))
const pageItems = computed(() => filteredItems.value.slice((pagination.page - 1) * pagination.pageSize, pagination.page * pagination.pageSize))
const hasFilters = computed(() => selectedCourseId.value || filters.type || filters.status || filters.dueRange !== 'all')
const overdueCount = computed(() => inbox.assignments.value.filter((item) => dueState(item) === 'overdue').length)
const soonCount = computed(() => inbox.assignments.value.filter((item) => dueState(item) === 'soon').length)
const dialogVisible = ref(false)
const formRef = ref(null)
const formLoading = ref(false)
const formError = ref('')
const form = reactive({ id: null, course_id: null, type: 'assignment', title: '', due_at: '', status: 'pending', note: '' })
let disposed = false

const allowed = (values, label) => (_rule, value, callback) => {
  callback(values.includes(value) ? undefined : new Error(`请选择合法的${label}`))
}
const rules = {
  course_id: [{ type: 'number', required: true, message: '请选择关联课程', trigger: 'change' }],
  type: [{ validator: allowed(['assignment', 'exam'], '类型'), trigger: 'change' }],
  title: [{ validator: (_rule, value, callback) => {
    const title = String(value || '').trim()
    callback(!title ? new Error('请输入标题') : title.length > 200 ? new Error('标题不能超过 200 字') : undefined)
  }, trigger: 'blur' }],
  due_at: [{ validator: (_rule, value, callback) => {
    callback(value && parseLocalDateTime(value) ? undefined : new Error('请选择合法的截止时间或考试时间'))
  }, trigger: 'change' }],
  status: [{ validator: allowed(['pending', 'done'], '状态'), trigger: 'change' }],
  note: [{ max: 500, message: '备注不能超过 500 字', trigger: 'blur' }]
}

function errorMessage(error, fallback) { return error?.response?.data?.message || error?.message || fallback }
function normalizeDueRange(value) { return ['3', '7', 'overdue'].includes(value) ? value : 'all' }
function resetPage() { pagination.page = 1 }
function clearCourseFilter() {
  const query = { ...route.query }
  delete query.course_id
  router.replace({ path: route.path, query })
}
function notice(row) { return deadlineNotice(row.due_at, calendar.value) }
function rowClass({ row }) { return `assignment-row assignment-${dueState(row)}` }
function deadlineLabel(row) {
  const state = dueState(row)
  if (state === 'overdue') return '已逾期，请尽快处理'
  const days = remainingDays(row.due_at)
  if (days === 0) return '今天到期'
  return Number.isFinite(days) ? `还有 ${days} 天到期` : ''
}
function closeDialog(done) { if (!formLoading.value) done() }
function resetForm() {
  Object.assign(form, { id: null, course_id: null, type: 'assignment', title: '', due_at: '', status: 'pending', note: '' })
  formError.value = ''
  formRef.value?.clearValidate()
}
function openCreate() { if (formLoading.value) return; resetForm(); dialogVisible.value = true }
function openEdit(row) {
  if (pendingIds.has(row.id) || formLoading.value) return
  resetForm()
  Object.assign(form, { id: row.id, course_id: row.course_id, type: row.type, title: row.title,
    due_at: row.due_at, status: row.status, note: row.note ?? '' })
  dialogVisible.value = true
}

async function loadContext() {
  if (contextLoading.value || disposed) return
  contextLoading.value = true
  contextError.value = ''
  try {
    const data = ensureSuccess(await getLearningContextApi())
    if (disposed) return
    courses.value = data.courses || []
    calendar.value = { holidays: data.holidays || [], makeup_workdays: data.makeup_workdays || [] }
  } catch (error) {
    if (!disposed) contextError.value = errorMessage(error, '课程与假期信息加载失败')
  } finally { if (!disposed) contextLoading.value = false }
}

async function refresh() {
  actionError.value = ''
  try { await inbox.refresh(true) } catch (error) {
    if (!disposed) actionError.value = errorMessage(error, '加载失败，请重试')
  }
}

async function refreshAfterWrite() {
  try { await inbox.refresh(true) } catch (error) {
    if (!disposed) actionError.value = `操作已保存，列表刷新失败：${errorMessage(error, '请点击刷新重试')}`
  }
}

async function saveItem() {
  if (formLoading.value || disposed) return
  // Lock before the asynchronous form validator so fast clicks cannot send two POSTs.
  formLoading.value = true
  formError.value = ''
  try {
    const valid = await formRef.value?.validate().catch(() => false)
    if (!valid) return
    const payload = { course_id: form.course_id, type: form.type, title: form.title.trim(),
      due_at: form.due_at, status: form.status, note: form.note }
    ensureSuccess(await (form.id ? updateAssignmentApi(form.id, payload) : createAssignmentApi(payload)))
    if (disposed) return
    dialogVisible.value = false
    ElMessage.success('事项已保存')
    await refreshAfterWrite()
  } catch (error) { if (!disposed) formError.value = errorMessage(error, '保存失败，请重试') }
  finally { if (!disposed) formLoading.value = false }
}

async function toggleDone(row) {
  if (pendingIds.has(row.id) || disposed) return
  pendingIds.add(row.id)
  actionError.value = ''
  try {
    ensureSuccess(await updateAssignmentApi(row.id, { status: row.status === 'done' ? 'pending' : 'done' }))
    if (disposed) return
    ElMessage.success(row.status === 'done' ? '已取消完成' : '已标记完成')
    await refreshAfterWrite()
  } catch (error) { if (!disposed) actionError.value = errorMessage(error, '状态更新失败，请重试') }
  finally { pendingIds.delete(row.id) }
}

async function removeItem(row) {
  if (pendingIds.has(row.id) || disposed) return
  pendingIds.add(row.id)
  actionError.value = ''
  try {
    await ElMessageBox.confirm(`确认删除「${row.title}」？删除后无法恢复。`, '删除事项', { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' })
    if (disposed) return
    ensureSuccess(await deleteAssignmentApi(row.id))
    if (disposed) return
    ElMessage.success('事项已删除')
    await refreshAfterWrite()
  } catch (error) {
    if (!disposed && error !== 'cancel' && error !== 'close') actionError.value = errorMessage(error, '删除失败，请重试')
  } finally { pendingIds.delete(row.id) }
}

watch(() => filteredItems.value.length, (total) => {
  pagination.page = Math.min(pagination.page, Math.max(1, Math.ceil(total / pagination.pageSize)))
})
watch(selectedCourseId, resetPage)
watch(() => route.query.due, (value) => {
  filters.dueRange = normalizeDueRange(value)
  resetPage()
})
onMounted(async () => {
  await Promise.allSettled([loadContext(), inbox.refresh()])
})
onBeforeUnmount(() => { disposed = true })
</script>

<style scoped>
.assignment-panel { border-radius: 12px; }
.page-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.page-heading h2 { margin: 0; font-size: 20px; color: var(--el-text-color-primary); }
.page-heading p { margin: 8px 0 0; font-size: 13px; color: var(--el-text-color-secondary); }
.assignment-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 22px; }
.assignment-summary > div { border: 1px solid var(--el-border-color-lighter); border-radius: 10px; padding: 16px 18px; background: var(--el-fill-color-extra-light); display: flex; justify-content: space-between; align-items: center; }
.assignment-summary span { font-size: 13px; color: var(--el-text-color-regular); }
.assignment-summary strong { font-size: 24px; color: var(--el-color-primary); }
.summary-overdue strong { color: var(--el-color-danger); }
.summary-soon strong { color: var(--el-color-warning); }
.filters { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 18px; }
.filters .el-select { width: 165px; }
.course-filter { display: flex; align-items: center; gap: 8px; margin: -4px 0 16px; color: var(--el-text-color-secondary); font-size: 13px; }
.page-alert { margin-bottom: 16px; }
.item-title { font-weight: 500; }
.deadline-state { margin-top: 4px; font-size: 12px; }
.deadline-overdue { color: var(--el-color-danger); }
.deadline-soon { color: #a86712; }
.deadline-notice { margin-top: 5px; font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.5; }
.form-help { display: block; margin-top: 5px; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.5; }
.list-footer { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 20px; }
.list-footer > span { font-size: 12px; color: var(--el-text-color-secondary); }
:deep(.assignment-overdue > td.el-table__cell) { background: var(--el-color-danger-light-9); }
:deep(.assignment-overdue > td.el-table__cell:first-child) { box-shadow: inset 3px 0 var(--el-color-danger); }
:deep(.assignment-done > td.el-table__cell) { color: var(--el-text-color-placeholder); background: var(--el-fill-color-extra-light); }
:deep(.assignment-done .item-title) { text-decoration: line-through; font-weight: 400; }
:deep(.assignment-done .deadline) { color: var(--el-text-color-placeholder); }
@media (max-width: 720px) {
  .assignment-summary { gap: 8px; }
  .assignment-summary > div { padding: 12px 8px; flex-direction: column; gap: 6px; }
  .assignment-summary span { font-size: 12px; }
  .page-heading { align-items: flex-start; }
  .list-footer { flex-direction: column; align-items: flex-start; }
  .filters .el-select { width: 145px; }
}
</style>
