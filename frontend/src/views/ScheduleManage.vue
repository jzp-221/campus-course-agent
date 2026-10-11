<template>
  <div>
    <el-card>
      <template #header>
        <div style="font-size: 16px; font-weight: bold">课表管理</div>
      </template>
      <div style="margin-bottom: 10px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center">
        <el-select
          v-model="params.userId"
          placeholder="按用户查看"
          clearable
          filterable
          style="width: 220px"
          @change="handleSearch"
          @clear="handleSearch"
        >
          <el-option
            v-for="u in studentOptions"
            :key="u.id"
            :label="`${u.name}（${u.username}）`"
            :value="u.id"
          />
        </el-select>
        <el-date-picker
          v-model="params.date"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="按日期筛选"
          clearable
          @change="handleSearch"
          @clear="handleSearch"
        />
        <el-button type="primary" :icon="Search" @click="handleSearch">查询</el-button>
        <el-button type="success" :icon="Plus" @click="handleCreate">新增课表</el-button>
      </div>
      <el-alert v-if="configError" :title="configError" type="error" :closable="false" show-icon>
        <el-button link :loading="configLoading" @click="loadConfig">重新加载学期配置</el-button>
      </el-alert>
      <el-alert v-if="studentsError" :title="studentsError" type="error" :closable="false"><el-button link @click="loadStudents">重试学生列表</el-button></el-alert>
      <el-alert v-if="coursesError" :title="coursesError" type="error" :closable="false"><el-button link @click="loadCourses">重试课程列表</el-button></el-alert>
      <el-alert v-if="listError" :title="listError" type="error" :closable="false"><el-button link @click="load">重试课表列表</el-button></el-alert>
      <el-alert v-if="actionError" :title="actionError" type="error" :closable="false" />
      <el-table
        v-loading="loading"
        :header-cell-style="{ backgroundColor: '#f5f7fa', color: '#333' }"
        :data="tableData"
        style="width: 100%"
      >
        <el-table-column label="学生" min-width="140">
          <template #default="{ row }">
            {{ row.user_name }}（{{ row.username }}）
          </template>
        </el-table-column>
        <el-table-column prop="class_date" label="日期" width="120" />
        <el-table-column prop="period" label="节次" width="110" />
        <el-table-column prop="course_name" label="课程" min-width="140" />
        <el-table-column prop="teacher" label="教师" width="110" />
        <el-table-column label="周次范围" width="110"><template #default="{ row }">{{ row.start_week }}–{{ row.end_week }}周</template></el-table-column>
        <el-table-column prop="location" label="地点" min-width="120" />
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" text bg :disabled="formLoading || pendingIds.has(row.id)" @click="handleEdit(row)">编辑</el-button>
            <el-button type="danger" text bg :disabled="pendingIds.has(row.id)" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div style="margin-top: 10px; display: flex; justify-content: flex-end">
        <el-pagination
          v-model:current-page="params.page"
          v-model:page-size="params.pageSize"
          :total="total"
          background
          layout="total, prev, pager, next"
          @current-change="load"
        />
      </div>
    </el-card>
    <el-dialog
      v-model="dialogVisible"
      :title="form.id ? '编辑课表' : '新增课表'"
      width="480"
      destroy-on-close
      :close-on-click-modal="!formLoading"
      :close-on-press-escape="!formLoading"
      :show-close="!formLoading"
    >
      <el-alert v-if="formError" :title="formError" type="error" :closable="false" show-icon />
      <el-alert v-if="!configReady" :title="configLoading ? '正在读取标准节次与学期周数…' : configError || '学期配置尚未就绪'" type="warning" :closable="false">
        <el-button link :loading="configLoading" @click="loadConfig">重新加载学期配置</el-button>
      </el-alert>
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        :disabled="formLoading"
        label-width="90px"
        style="padding-right: 20px; padding-top: 8px"
      >
        <el-form-item label="学生" prop="user_id">
          <el-select v-model="form.user_id" placeholder="选择学生" filterable style="width: 100%">
            <el-option
              v-for="u in studentOptions"
              :key="u.id"
              :label="`${u.name}（${u.username}）`"
              :value="u.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="课程" prop="course_id">
          <el-select v-model="form.course_id" placeholder="选择课程" filterable style="width: 100%">
            <el-option
              v-for="c in courseOptions"
              :key="c.id"
              :label="c.code ? `${c.name}（${c.code}）` : c.name"
              :value="c.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="上课日期" prop="class_date">
          <el-date-picker
            v-model="form.class_date"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="选择日期"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="节次" prop="period">
          <el-select v-model="form.period" placeholder="选择标准节次" :loading="configLoading" :disabled="!configReady" style="width: 100%">
            <el-option v-for="period in periods" :key="period" :label="period" :value="period" />
          </el-select>
        </el-form-item>
        <el-form-item label="教师" prop="teacher"><el-input v-model="form.teacher" maxlength="100" placeholder="可不填写，清空将移除原教师" /></el-form-item>
        <el-form-item label="起始周" prop="start_week"><el-input-number v-model="form.start_week" :min="1" :max="totalWeeks || undefined" :precision="0" :disabled="!configReady" /></el-form-item>
        <el-form-item label="结束周" prop="end_week"><el-input-number v-model="form.end_week" :min="1" :max="totalWeeks || undefined" :precision="0" :disabled="!configReady" /></el-form-item>
        <el-form-item label="地点" prop="location">
          <el-input v-model="form.location" placeholder="例如：教学楼 A201" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="formLoading" @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="formLoading" :disabled="!configReady" @click="handleSave">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>
<script setup>
defineOptions({ name: 'ScheduleManage' })
import { Plus, Search } from '@element-plus/icons-vue'
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createScheduleApi,
  deleteScheduleApi,
  listSchedulesApi,
  getWeekScheduleApi,
  updateScheduleApi
} from '@/api/schedule'
import { listCoursesApi } from '@/api/course'
import { getUserPageList } from '@/api/user'
import { ensureSuccess } from '@/utils/learningItems'

const params = reactive({
  page: 1,
  pageSize: 10,
  userId: null,
  date: null
})
const loading = ref(false)
const tableData = ref([])
const total = ref(0)

const studentOptions = ref([])
const courseOptions = ref([])
const periods = ref([])
const totalWeeks = ref(null)
const configLoading = ref(false)
const configError = ref('')
const configReady = computed(() => periods.value.length === 4 && Number.isInteger(totalWeeks.value) && totalWeeks.value > 0)
const studentsError = ref('')
const coursesError = ref('')
const listError = ref('')
const actionError = ref('')
const formError = ref('')
const pendingIds = reactive(new Set())
let disposed = false
let loadVersion = 0

const dialogVisible = ref(false)
const formRef = ref()
const formLoading = ref(false)
const form = reactive({
  id: null,
  user_id: null,
  course_id: null,
  class_date: '',
  period: '',
  location: '',
  teacher: '',
  start_week: 1,
  end_week: null
})

const validateWeek = (_rule, value, callback) => {
  if (!configReady.value) return callback(new Error('请先加载学期配置'))
  if (!Number.isInteger(value) || value < 1 || value > totalWeeks.value) return callback(new Error(`周次须为 1～${totalWeeks.value} 的整数`))
  if (Number.isInteger(form.start_week) && Number.isInteger(form.end_week) && form.start_week > form.end_week) return callback(new Error('起始周不能晚于结束周'))
  callback()
}
const rules = {
  user_id: [{ type: 'number', min: 1, required: true, message: '请选择学生', trigger: 'change' }],
  course_id: [{ type: 'number', min: 1, required: true, message: '请选择课程', trigger: 'change' }],
  class_date: [{ required: true, message: '请选择上课日期', trigger: 'change' }],
  period: [{ validator: (_rule, value, callback) => callback(configReady.value && periods.value.includes(value) ? undefined : new Error('请选择四档标准节次')), trigger: 'change' }],
  teacher: [{ max: 100, message: '教师姓名不能超过 100 字', trigger: 'blur' }],
  start_week: [{ validator: validateWeek, trigger: 'change' }],
  end_week: [{ validator: validateWeek, trigger: 'change' }],
  location: [{ required: true, message: '请输入地点', trigger: 'blur' }]
}

const resetForm = () => {
  Object.assign(form, {
    id: null,
    user_id: params.userId || null,
    course_id: null,
    class_date: '',
    period: '',
    location: '',
    teacher: '',
    start_week: 1,
    end_week: totalWeeks.value
  })
  formError.value = ''
  formRef.value?.clearValidate()
}

const errorText = (failure, fallback) => failure?.response?.data?.message || failure?.message || fallback

const loadConfig = async () => {
  if (configLoading.value || disposed) return
  configLoading.value = true
  configError.value = ''
  try {
    const data = ensureSuccess(await getWeekScheduleApi())
    if (!Array.isArray(data?.periods) || data.periods.length !== 4 || new Set(data.periods).size !== 4 ||
        data.periods.some(period => typeof period !== 'string' || !period.trim()) ||
        !Number.isInteger(data.total_weeks) || data.total_weeks < 1) {
      throw new Error('标准节次与学期周数配置格式不正确')
    }
    if (disposed) return
    periods.value = data.periods
    totalWeeks.value = data.total_weeks
    if (form.end_week == null) form.end_week = data.total_weeks
  } catch (failure) {
    if (!disposed) {
      periods.value = []
      totalWeeks.value = null
      configError.value = `学期配置加载失败：${errorText(failure, '请重试')}`
    }
  } finally { if (!disposed) configLoading.value = false }
}

const loadStudents = async () => {
  studentsError.value = ''
  try {
    const data = ensureSuccess(await getUserPageList({ page: 1, page_size: 200 }))
    if (!disposed) studentOptions.value = (data?.list || []).filter((u) => u.role === 'student')
  } catch (failure) { if (!disposed) studentsError.value = `学生列表加载失败：${errorText(failure, '请重试')}` }
}

const loadCourses = async () => {
  coursesError.value = ''
  try {
    const data = ensureSuccess(await listCoursesApi())
    if (!disposed) courseOptions.value = (data || []).filter((c) => c.status === 1)
  } catch (failure) { if (!disposed) coursesError.value = `课程列表加载失败：${errorText(failure, '请重试')}` }
}

const load = async () => {
  if (disposed) return false
  const revision = ++loadVersion
  loading.value = true
  listError.value = ''
  try {
    const data = ensureSuccess(await listSchedulesApi({
      page: params.page,
      page_size: params.pageSize,
      user_id: params.userId ?? undefined,
      date: params.date || undefined
    }))
    if (disposed || revision !== loadVersion) return false
    tableData.value = data?.list || []
    total.value = data?.total || 0
    return true
  } catch (failure) {
    if (!disposed && revision === loadVersion) listError.value = errorText(failure, '课表列表加载失败，请重试')
    return false
  } finally {
    if (!disposed && revision === loadVersion) loading.value = false
  }
}

const handleSearch = () => {
  params.page = 1
  load()
}

const handleCreate = () => {
  if (formLoading.value) return
  resetForm()
  dialogVisible.value = true
}

const handleEdit = (row) => {
  if (formLoading.value || pendingIds.has(row.id)) return
  resetForm()
  Object.assign(form, {
    id: row.id,
    user_id: row.user_id,
    course_id: row.course_id,
    class_date: row.class_date,
    period: row.period,
    location: row.location,
    teacher: row.teacher ?? '',
    start_week: row.start_week ?? 1,
    end_week: row.end_week ?? totalWeeks.value
  })
  dialogVisible.value = true
}

const handleSave = async () => {
  if (formLoading.value || disposed) return
  formLoading.value = true
  formError.value = ''
  actionError.value = ''
  try {
    if (!configReady.value) { formError.value = '学期配置尚未就绪，请先重新加载'; return }
    const valid = await formRef.value.validate().catch(() => false)
    if (!valid || disposed) return
    const payload = {
      user_id: form.user_id,
      course_id: form.course_id,
      class_date: form.class_date,
      period: form.period,
      location: form.location,
      teacher: form.teacher.trim() || null,
      start_week: form.start_week,
      end_week: form.end_week
    }
    ensureSuccess(await (form.id
      ? updateScheduleApi(form.id, payload)
      : createScheduleApi(payload)))
    if (disposed) return
    dialogVisible.value = false
    ElMessage.success('操作成功')
    if (!await load() && !disposed) actionError.value = '课表已保存，列表刷新失败，请重试加载列表'
  } catch (failure) {
    if (!disposed) formError.value = errorText(failure, '保存失败，请重试')
  } finally {
    formLoading.value = false
  }
}

const handleDelete = async (row) => {
  if (pendingIds.has(row.id) || disposed) return
  pendingIds.add(row.id)
  actionError.value = ''
  try {
    await ElMessageBox.confirm(
      `确认删除「${row.user_name}」在 ${row.class_date} 的「${row.course_name}」课表？`,
      '确认删除',
      { type: 'warning' }
    )
    if (disposed) return
    ensureSuccess(await deleteScheduleApi(row.id))
    if (disposed) return
    ElMessage.success('已删除')
    if (!await load() && !disposed) actionError.value = '课表已删除，列表刷新失败，请重试加载列表'
  } catch (failure) {
    if (!disposed && failure !== 'cancel' && failure !== 'close') actionError.value = errorText(failure, '删除失败，请重试')
  } finally { pendingIds.delete(row.id) }
}

onMounted(async () => {
  await Promise.allSettled([loadStudents(), loadCourses(), loadConfig(), load()])
})
onBeforeUnmount(() => { disposed = true; loadVersion++ })
</script>
