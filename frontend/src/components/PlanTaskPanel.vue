<template>
  <el-card class="task-panel">
    <template #header>
      <div class="task-header">
        <div>
          <h2>计划任务</h2>
          <p>独立管理可勾选的任务；不会修改上方的文本学习计划。</p>
        </div>
        <el-button type="primary" @click="openCreate">新增任务</el-button>
      </div>
    </template>

    <div class="task-toolbar">
      <el-select v-model="params.status" aria-label="计划任务状态" placeholder="全部任务" style="width: 160px" @change="changeStatus">
        <el-option label="全部任务" value="" />
        <el-option label="未完成" value="pending" />
        <el-option label="已完成" value="done" />
      </el-select>
      <el-button :loading="loading" @click="loadTasks">刷新</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="task-error" />

    <div v-loading="loading" class="task-list">
      <el-empty v-if="!tasks.length && !loading && !error" description="把学习安排拆成小任务，完成后在这里勾选。" />
      <article
        v-for="task in tasks"
        :key="task.id"
        class="task-item"
        :class="{ 'task-overdue': dueState(task) === 'overdue', 'task-done': task.status === 'done' }"
      >
        <el-checkbox
          :model-value="task.status === 'done'"
          :disabled="pendingIds.has(task.id)"
          :aria-label="`${task.status === 'done' ? '取消完成' : '完成'}：${task.title}`"
          @change="(checked) => toggleTask(task, checked)"
        />
        <div class="task-content">
          <div class="task-title">{{ task.title }}</div>
          <div class="task-meta">
            <span>{{ task.course_name || '未关联课程' }}</span>
            <span :class="{ 'deadline-overdue': dueState(task) === 'overdue' }">
              {{ task.due_at ? `截止：${formatLocalDateTime(task.due_at)}` : '未设截止' }}
            </span>
            <el-tag v-if="dueState(task) === 'overdue'" type="danger" size="small">已逾期</el-tag>
            <el-tag v-else-if="dueState(task) === 'soon'" type="warning" size="small">3 天内到期</el-tag>
            <el-tag v-if="task.status === 'done'" type="success" size="small">已完成</el-tag>
            <el-tag v-if="task.defer_count > 0" type="warning" size="small">已延期 {{ task.defer_count }} 次</el-tag>
          </div>
        </div>
        <div class="task-actions">
          <el-button link type="primary" :disabled="pendingIds.has(task.id)" @click="openEdit(task)">编辑</el-button>
          <el-button
            link
            type="warning"
            :disabled="pendingIds.has(task.id) || task.status === 'done'"
            @click="openDefer(task)"
          >延期</el-button>
          <el-button link type="danger" :disabled="pendingIds.has(task.id)" @click="removeTask(task)">删除</el-button>
        </div>
      </article>
    </div>
    <div class="task-pagination">
      <el-pagination
        :current-page="params.page"
        :page-size="params.pageSize"
        :total="total"
        background
        layout="total, prev, pager, next"
        @current-change="changePage"
      />
    </div>

    <el-dialog
      v-model="editorVisible"
      :title="editingId === null ? '新增计划任务' : '编辑计划任务'"
      width="min(560px, 92vw)"
      :close-on-click-modal="!saving"
      :close-on-press-escape="!saving"
      :show-close="!saving"
      append-to-body
      destroy-on-close
    >
      <el-form label-width="90px" @submit.prevent="saveTask">
        <el-form-item label="任务标题" required>
          <el-input v-model="form.title" maxlength="200" show-word-limit :disabled="saving" placeholder="例如：完成实验报告二" />
        </el-form-item>
        <el-form-item label="关联课程">
          <el-select v-model="form.course_id" clearable filterable :disabled="saving" placeholder="可不关联课程" style="width: 100%">
            <el-option v-for="course in courses" :key="course.id" :value="course.id" :label="course.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="截止时间">
          <el-date-picker
            v-model="form.due_at"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            format="YYYY-MM-DD HH:mm"
            clearable
            :disabled="saving"
            placeholder="可不设截止时间"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="状态">
          <el-radio-group v-model="form.status" :disabled="saving">
            <el-radio value="pending">未完成</el-radio>
            <el-radio value="done">已完成</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <el-alert v-if="formError" :title="formError" type="error" :closable="false" />
      <template #footer>
        <el-button :disabled="saving" @click="editorVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveTask">保存任务</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="deferVisible"
      title="延期计划任务"
      width="min(440px, 92vw)"
      :close-on-click-modal="!deferring"
      :close-on-press-escape="!deferring"
      :show-close="!deferring"
      append-to-body
      destroy-on-close
    >
      <p class="defer-title">{{ deferTask?.title }}</p>
      <p class="defer-hint">
        {{ deferTask?.due_at ? `原截止：${formatLocalDateTime(deferTask.due_at)}` : '未设截止：将以当前本地时间作为延期起点。' }}
      </p>
      <el-form label-width="90px" @submit.prevent="submitDefer">
        <el-form-item label="延期天数">
          <el-input-number v-model="deferDays" :min="1" :max="30" :precision="0" :disabled="deferring" />
          <span class="defer-unit">天</span>
        </el-form-item>
      </el-form>
      <p class="defer-hint">每次成功延期，延期次数增加 1；默认往后推 1 天。</p>
      <el-alert v-if="deferError" :title="deferError" type="error" :closable="false" />
      <template #footer>
        <el-button :disabled="deferring" @click="deferVisible = false">取消</el-button>
        <el-button type="primary" :loading="deferring" @click="submitDefer">确认延期</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createPlanTaskApi, deferPlanTaskApi, deletePlanTaskApi, listPlanTasksApi, updatePlanTaskApi
} from '@/api/planTask'
import { getLearningContextApi } from '@/api/learningContext'
import { dueState, ensureSuccess, formatLocalDateTime } from '@/utils/learningItems'

const tasks = ref([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
const courses = ref([])
const params = reactive({ page: 1, pageSize: 10, status: '' })
const pendingIds = reactive(new Set())
const editorVisible = ref(false)
const editingId = ref(null)
const saving = ref(false)
const formError = ref('')
const form = reactive({ title: '', course_id: null, due_at: null, status: 'pending' })
const deferVisible = ref(false)
const deferTask = ref(null)
const deferDays = ref(1)
const deferring = ref(false)
const deferError = ref('')
let loadVersion = 0
let disposed = false

const failureText = (failure) => failure?.message || '请求失败，请重试'

async function loadTasks() {
  if (disposed) return
  const version = ++loadVersion
  loading.value = true
  error.value = ''
  try {
    const data = ensureSuccess(await listPlanTasksApi({
      page: params.page, page_size: params.pageSize,
      ...(params.status ? { status: params.status } : {})
    }))
    if (disposed || version !== loadVersion) return
    total.value = data?.total || 0
    const lastPage = Math.max(1, Math.ceil(total.value / params.pageSize))
    if (params.page > lastPage) {
      params.page = lastPage
      await loadTasks()
      return
    }
    tasks.value = data?.list || []
  } catch (failure) {
    if (!disposed && version === loadVersion) error.value = failureText(failure)
  } finally {
    if (!disposed && version === loadVersion) loading.value = false
  }
}

async function loadCourses() {
  try {
    const data = ensureSuccess(await getLearningContextApi())
    if (!disposed) courses.value = data?.courses || []
  } catch (failure) {
    if (!disposed) error.value = `课程选项加载失败：${failureText(failure)}`
  }
}

function changeStatus() {
  params.page = 1
  return loadTasks()
}

function changePage(page) {
  if (page === params.page) return
  params.page = page
  return loadTasks()
}

function openCreate() {
  if (saving.value) return
  editingId.value = null
  Object.assign(form, { title: '', course_id: null, due_at: null, status: 'pending' })
  formError.value = ''
  editorVisible.value = true
}

function openEdit(task) {
  if (saving.value || pendingIds.has(task.id)) return
  editingId.value = task.id
  Object.assign(form, {
    title: task.title, course_id: task.course_id ?? null,
    due_at: task.due_at ?? null, status: task.status
  })
  formError.value = ''
  editorVisible.value = true
}

async function saveTask() {
  if (saving.value) return
  const title = form.title.trim()
  if (!title || title.length > 200) {
    formError.value = '任务标题去空白后须为 1～200 字'
    return
  }
  const id = editingId.value
  if (id !== null && pendingIds.has(id)) return
  const payload = {
    title, course_id: form.course_id || null, due_at: form.due_at || null, status: form.status
  }
  saving.value = true
  if (id !== null) pendingIds.add(id)
  formError.value = ''
  try {
    ensureSuccess(await (id === null ? createPlanTaskApi(payload) : updatePlanTaskApi(id, payload)))
    editorVisible.value = false
    ElMessage.success(id === null ? '任务已添加' : '任务已更新')
    if (id === null) params.page = 1
    await loadTasks()
  } catch (failure) {
    formError.value = failureText(failure)
  } finally {
    saving.value = false
    if (id !== null) pendingIds.delete(id)
  }
}

async function toggleTask(task, checked) {
  if (pendingIds.has(task.id)) return
  pendingIds.add(task.id)
  error.value = ''
  try {
    ensureSuccess(await updatePlanTaskApi(task.id, { status: checked ? 'done' : 'pending' }))
    await loadTasks()
  } catch (failure) {
    error.value = failureText(failure)
  } finally {
    pendingIds.delete(task.id)
  }
}

function openDefer(task) {
  if (deferring.value || pendingIds.has(task.id) || task.status === 'done') return
  deferTask.value = task
  deferDays.value = 1
  deferError.value = ''
  deferVisible.value = true
}

async function submitDefer() {
  const task = deferTask.value
  if (!task || deferring.value || pendingIds.has(task.id)) return
  if (!Number.isInteger(deferDays.value) || deferDays.value < 1 || deferDays.value > 30) {
    deferError.value = '延期天数须为 1～30 的整数'
    return
  }
  deferring.value = true
  pendingIds.add(task.id)
  deferError.value = ''
  try {
    // Render the authoritative counter/deadline from the next list response.
    ensureSuccess(await deferPlanTaskApi(task.id, deferDays.value))
    deferVisible.value = false
    ElMessage.success(`已延期 ${deferDays.value} 天`)
    await loadTasks()
  } catch (failure) {
    deferError.value = failureText(failure)
  } finally {
    deferring.value = false
    pendingIds.delete(task.id)
  }
}

async function removeTask(task) {
  if (pendingIds.has(task.id)) return
  pendingIds.add(task.id)
  error.value = ''
  let confirmed = false
  try {
    await ElMessageBox.confirm(`确认删除任务“${task.title}”？`, '删除计划任务', { type: 'warning' })
    confirmed = true
    ensureSuccess(await deletePlanTaskApi(task.id))
    ElMessage.success('任务已删除')
    await loadTasks()
  } catch (failure) {
    if (confirmed) error.value = failureText(failure)
  } finally {
    pendingIds.delete(task.id)
  }
}

onMounted(() => Promise.allSettled([loadTasks(), loadCourses()]))
onBeforeUnmount(() => { disposed = true; loadVersion += 1 })
</script>

<style scoped>
.task-panel { margin-top: 8px; }
.task-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.task-header h2 { margin: 0; font-size: 17px; color: var(--el-text-color-primary); }
.task-header p { margin: 7px 0 0; font-size: 13px; color: var(--el-text-color-secondary); }
.task-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
.task-error { margin-bottom: 12px; }
.task-list { min-height: 120px; }
.task-item { display: flex; align-items: center; gap: 12px; padding: 17px 14px; margin-bottom: 10px;
  border: 1px solid var(--el-border-color-lighter); border-left: 3px solid var(--el-color-primary-light-5); border-radius: 8px; }
.task-content { flex: 1; min-width: 0; }
.task-title { line-height: 1.55; color: var(--el-text-color-primary); overflow-wrap: anywhere; }
.task-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 7px 14px; margin-top: 7px;
  font-size: 12px; color: var(--el-text-color-secondary); }
.task-actions { display: flex; gap: 4px; flex-shrink: 0; }
.task-overdue { background: var(--el-color-danger-light-9); border-left-color: var(--el-color-danger); }
.deadline-overdue { color: var(--el-color-danger); }
.task-done { background: var(--el-fill-color-light); border-left-color: var(--el-border-color); }
.task-done .task-title { color: var(--el-text-color-secondary); text-decoration: line-through; }
.task-pagination { display: flex; justify-content: flex-end; margin-top: 18px; }
.defer-title { margin-top: 0; font-weight: 600; overflow-wrap: anywhere; }
.defer-hint { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.defer-unit { margin-left: 10px; }
@media (max-width: 680px) {
  .task-header { align-items: flex-start; }
  .task-item { align-items: flex-start; flex-wrap: wrap; }
  .task-content { min-width: calc(100% - 50px); }
  .task-actions { margin-left: 30px; }
}
</style>
