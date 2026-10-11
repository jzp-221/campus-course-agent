<template>
  <div class="course-detail-page">
    <div class="detail-heading">
      <div><el-button link @click="router.back()">← 返回</el-button><h2>{{ detail?.course?.name || '课程详情' }}</h2><p>把上课安排、课程资料与截止事项放在一起。</p></div>
      <el-button @click="load">刷新课程信息</el-button>
    </div>
    <el-skeleton v-if="loading" :rows="8" animated />
    <el-alert v-else-if="error" :title="error" type="error" :closable="false" show-icon><el-button link @click="load">重试</el-button></el-alert>
    <template v-else-if="detail">
      <el-card shadow="never" class="course-info">
        <template #header><div class="panel-heading"><strong>课程信息</strong><el-tag effect="plain">{{ detail.course.code }}</el-tag></div></template>
        <p class="description">{{ detail.course.description || '课程暂无简介' }}</p>
        <div class="semester-meta"><el-tag type="primary">{{ detail.semester.current_week ? `当前学期第 ${detail.semester.current_week} 周` : '当前不在学期内' }}</el-tag><el-tag v-if="detail.start_week" type="success">第 {{ detail.start_week }}–{{ detail.end_week }} 周上课</el-tag><span v-else>本账号暂无该课程安排</span><span>学期共 {{ detail.semester.total_weeks }} 周</span></div>
        <el-progress v-if="detail.start_week && detail.semester.current_week" :percentage="progress" :stroke-width="7" :show-text="false" class="course-progress" />
      </el-card>
      <div class="detail-grid">
        <el-card shadow="never" class="detail-card">
          <template #header><div class="panel-heading"><strong>上课安排</strong><el-tag type="info" effect="plain">本人 {{ detail.schedules.length }} 条</el-tag></div></template>
          <p v-if="isAdmin" class="small-note">此处为管理员本账号安排，未展示学生的私人课表。</p>
          <el-table :data="detail.schedules" max-height="340" empty-text="本账号没有该课程的上课安排">
            <el-table-column prop="class_date" label="日期" width="110" />
            <el-table-column label="星期" width="65"><template #default="{ row }">{{ weekdayLabels[row.weekday - 1] }}</template></el-table-column>
            <el-table-column prop="period" label="节次" width="80" />
            <el-table-column prop="teacher" label="教师" width="80" />
            <el-table-column prop="location" label="地点" min-width="105" show-overflow-tooltip />
            <el-table-column label="周次" width="90"><template #default="{ row }">{{ row.start_week }}–{{ row.end_week }}周</template></el-table-column>
          </el-table>
        </el-card>
        <el-card shadow="never" class="detail-card">
          <template #header><div class="panel-heading"><strong>课程资料</strong><el-button link type="primary" :loading="docsLoading" @click="loadDocs">重新检索</el-button></div></template>
          <p class="small-note">按课程名称检索知识库，保留原文与来源。</p>
          <el-skeleton v-if="docsLoading" :rows="4" animated />
          <el-alert v-else-if="docsError" type="error" :title="docsError" :closable="false" show-icon />
          <el-empty v-else-if="!docs.length" description="知识库中暂无该课程资料" :image-size="70" />
          <el-collapse v-else v-model="openDocs">
            <el-collapse-item v-for="(item, index) in docs" :key="`${item.document_id}-${item.chunk_index}`" :name="index">
              <template #title><span class="source-name">{{ item.source }}</span></template>
              <div class="fragment-text">{{ item.text }}</div>
            </el-collapse-item>
          </el-collapse>
        </el-card>
        <el-card shadow="never" class="detail-card assignments-card">
          <template #header><div class="panel-heading"><strong>作业与考试</strong><el-tag v-if="!isAdmin" type="warning">未完成 {{ pendingCount(assignments) }} 项</el-tag></div></template>
          <el-alert v-if="isAdmin" title="管理员视角不展示学生作业" type="info" :closable="false" show-icon />
          <template v-else>
            <el-skeleton v-if="assignmentsLoading" :rows="3" animated />
            <el-alert v-else-if="assignmentsError" :title="assignmentsError" type="error" :closable="false" />
            <el-empty v-else-if="!assignments.length" description="这门课还没有作业或考试" :image-size="65" />
            <el-table v-else :data="sortByDue(assignments)" :row-class-name="({ row }) => `deadline-${dueState(row)}`">
              <el-table-column prop="title" label="标题" min-width="210" show-overflow-tooltip />
              <el-table-column label="类型" width="85"><template #default="{ row }"><el-tag :type="row.type === 'exam' ? 'danger' : 'info'">{{ row.type === 'exam' ? '考试' : '作业' }}</el-tag></template></el-table-column>
              <el-table-column label="截止时间" width="180"><template #default="{ row }">{{ formatLocalDateTime(row.due_at) }}</template></el-table-column>
              <el-table-column label="状态" width="100"><template #default="{ row }">{{ row.status === 'done' ? '已完成' : dueState(row) === 'overdue' ? '已逾期' : '未完成' }}</template></el-table-column>
            </el-table>
            <div class="assignment-link"><el-button type="primary" plain @click="router.push({ name: 'Assignment', query: { course_id: courseId } })">管理这门课的截止事项</el-button></div>
          </template>
        </el-card>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUser } from '@/utils/user'
import { getCourseDetailApi, getCourseDocsApi } from '@/api/learningContext'
import { listAssignmentsApi } from '@/api/assignment'
import { dueState, ensureSuccess, fetchAllPages, formatLocalDateTime, pendingCount, sortByDue } from '@/utils/learningItems'

defineOptions({ name: 'CourseDetail' })
const route = useRoute()
const router = useRouter()
const { userInfo } = useUser()
const isAdmin = computed(() => userInfo.value?.role === 'admin')
const courseId = computed(() => Number(route.params.id))
const detail = ref(null)
const loading = ref(false)
const error = ref('')
const docs = ref([])
const docsLoading = ref(false)
const docsError = ref('')
const openDocs = ref([0])
const assignments = ref([])
const assignmentsLoading = ref(false)
const assignmentsError = ref('')
const weekdayLabels = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
let revision = 0
const progress = computed(() => Math.max(0, Math.min(100, Math.round((detail.value.semester.current_week - detail.value.start_week + 1) / (detail.value.end_week - detail.value.start_week + 1) * 100))))

async function loadDocs() {
  const id = courseId.value
  const current = revision
  docsLoading.value = true
  docsError.value = ''
  docs.value = []
  try {
    const data = ensureSuccess(await getCourseDocsApi(id))
    if (id === courseId.value && current === revision) docs.value = data.items
  } catch (failure) {
    if (id === courseId.value && current === revision) docsError.value = failure.message || '当前无法查询资料（检索失败），请稍后重试'
  } finally {
    if (id === courseId.value && current === revision) docsLoading.value = false
  }
}

async function load() {
  const current = ++revision
  const id = courseId.value
  detail.value = null
  docs.value = []
  assignments.value = []
  docsError.value = ''
  assignmentsError.value = ''
  error.value = ''
  loading.value = false
  docsLoading.value = false
  assignmentsLoading.value = false
  if (!Number.isSafeInteger(id) || id < 1) { error.value = '课程不存在'; return }
  loading.value = true
  try {
    const data = ensureSuccess(await getCourseDetailApi(id))
    if (current !== revision) return
    detail.value = data
    void loadDocs()
    if (!isAdmin.value) {
      assignmentsLoading.value = true
      try {
        const rows = await fetchAllPages(listAssignmentsApi, { course_id: id })
        if (current === revision) assignments.value = rows
      } catch (failure) {
        if (current === revision) assignmentsError.value = failure.message || '作业加载失败，请重试'
      } finally {
        if (current === revision) assignmentsLoading.value = false
      }
    }
  } catch (failure) {
    if (current === revision) error.value = failure.message || '课程信息加载失败，请重试'
  } finally {
    if (current === revision) loading.value = false
  }
}
watch(() => route.params.id, load, { immediate: true })
</script>

<style scoped>
.course-detail-page { max-width: 1240px; margin: auto; }
.detail-heading, .panel-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.detail-heading { margin-bottom: 18px; }.detail-heading h2 { margin: 8px 0; color: #253047; }.detail-heading p, .small-note { color: #909399; font-size: 13px; }
.course-info, .detail-card { border-radius: 14px; }.course-info { margin-bottom: 18px; }.description { line-height: 1.7; color: #606266; white-space: pre-wrap; }
.semester-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; color: #909399; font-size: 13px; }.course-progress { margin-top: 18px; }
.detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }.assignments-card { grid-column: 1 / -1; }.assignment-link { margin-top: 16px; }
.fragment-text { white-space: pre-wrap; max-height: 280px; overflow: auto; line-height: 1.7; color: #606266; background: #f8fafc; padding: 12px; border-radius: 8px; overflow-wrap: anywhere; }.source-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; }
:deep(.deadline-overdue td) { color: #f56c6c; background: #fef0f0 !important; }:deep(.deadline-done td) { color: #909399; }
@media(max-width: 1000px) { .detail-grid { grid-template-columns: 1fr; } }
</style>
