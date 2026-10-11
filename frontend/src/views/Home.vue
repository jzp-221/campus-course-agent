<template>
  <div class="home-page">
    <!-- 欢迎区 -->
    <el-card shadow="never" class="hero-card">
      <div class="hero-row">
        <div class="hero-left">
          <h2 class="greeting">{{ greeting }}，{{ userInfo?.name || '同学' }}</h2>
          <p class="hero-sub">
            <span>{{ todayLabel }}</span>
            <template v-if="today?.holiday_name">
              <el-tag size="small" type="warning" effect="plain">{{ today.holiday_name }}放假</el-tag>
            </template>
            <template v-else-if="today && !today.is_class_day">
              <el-tag size="small" type="info" effect="plain">休息日</el-tag>
            </template>
            <template v-else-if="today && today.weekday >= 6">
              <el-tag size="small" type="success" effect="plain">调休上课</el-tag>
            </template>
          </p>
        </div>
        <div class="hero-right">
          <template v-if="semester">
            <div class="week-badge">
              <span class="week-current">第 {{ semester.week }} 周</span>
              <span class="week-total">/ 共 {{ semester.totalWeeks }} 周</span>
            </div>
            <el-progress
              :percentage="semester.percent"
              :stroke-width="8"
              :show-text="false"
              class="week-progress"
            />
            <p class="semester-range">{{ semester.rangeText }}</p>
          </template>
          <el-skeleton v-else-if="weekLoading" :rows="2" animated class="hero-skeleton" />
          <p v-else class="hint">{{ weekError ? '学期信息加载失败' : '当前不在学期内' }}</p>
        </div>
      </div>
    </el-card>

    <el-alert v-if="!isAdmin && !inbox.error.value && overdueItems.length" class="deadline-alert" type="warning" :closable="false" show-icon>
      <template #title>进入系统时提醒：{{ overdueItems.length }} 项作业或考试已逾期未完成</template>
      <div class="overdue-summary"><span>{{ overdueItems.map(item => `${item.course_name} · ${item.title}`).join('；') }}</span><el-button link type="warning" @click="router.push({ name: 'Assignment', query: { due: 'overdue' } })">查看逾期事项</el-button></div>
    </el-alert>

    <!-- 主内容区 -->
    <div class="grid">
      <!-- 今日课程 -->
      <el-card shadow="never" class="panel today-panel">
        <template #header>
          <div class="panel-head">
            <span class="panel-title">今日课程</span>
            <el-tag v-if="todayCourses.length" size="small" type="primary" effect="light">
              {{ todayCourses.length }} 门
            </el-tag>
          </div>
        </template>
        <el-skeleton v-if="weekLoading" :rows="3" animated />
        <template v-else>
          <el-alert v-if="weekError" type="error" :title="weekError" :closable="false" show-icon>
            <el-button link @click="loadWeek">重新加载课表</el-button>
          </el-alert>
          <el-empty
            v-else-if="!today"
            :image-size="70"
            :description="outsideSemester ? '当前不在学期内，请到周课表查看学期安排' : '暂时读不到今天的课表，请稍后重试'"
          />
          <el-empty
            v-else-if="!todayCourses.length"
            :image-size="70"
            description="今天没有课，好好休息"
          />
          <ul v-else class="course-list">
            <li v-for="course in todayCourses" :key="course.period + course.course_name">
              <div class="course-main">
                <span class="course-name">{{ course.course_name }}</span>
                <span class="course-period">{{ course.period }}</span>
              </div>
              <div class="course-meta">
                <span v-if="course.teacher">{{ course.teacher }}</span>
                <span v-if="course.location">{{ course.location }}</span>
                <span v-if="course.start_week" class="course-weeks">
                  第 {{ course.start_week }}-{{ course.end_week }} 周
                </span>
              </div>
            </li>
          </ul>
          <p v-if="tomorrow" class="tomorrow-line">
            <span class="tomorrow-label">明天</span>
            <template v-if="tomorrowCourses.length">
              {{ tomorrowDateLabel }} 有 {{ tomorrowCourses.length }} 门课
            </template>
            <template v-else>{{ tomorrowDateLabel }} 没有课</template>
          </p>
          <p v-else-if="nextWeekError" class="hint warn">{{ nextWeekError }} <el-button link @click="loadWeek">重试明日预告</el-button></p>
        </template>
      </el-card>

      <!-- 即将结束的课程 -->
      <el-card shadow="never" class="panel">
        <template #header>
          <div class="panel-head">
            <span class="panel-title">即将结束的课程</span>
          </div>
        </template>
        <el-skeleton v-if="weekLoading" :rows="3" animated />
        <template v-else>
          <el-alert v-if="weekError" type="error" title="课表加载失败，暂时无法判断结课信息" :closable="false" />
          <el-empty
            v-else-if="!endingCourses.length"
            :image-size="70"
            :description="outsideSemester ? '当前不在学期内' : nearestEnding ? '近期没有课程结课' : '本学期暂时没有结课信息'"
          >
            <p v-if="nearestEnding" class="hint">
              最近一次结课：{{ nearestEnding.course_name }}（第 {{ nearestEnding.end_week }} 周，还剩
              {{ nearestEnding.remaining }} 周）
            </p>
          </el-empty>
          <ul v-else class="ending-list">
            <li v-for="course in endingCourses" :key="course.course_id">
              <span class="ending-name">{{ course.course_name }}</span>
              <el-tag
                size="small"
                :type="course.remaining <= 1 ? 'danger' : 'warning'"
                effect="plain"
              >
                {{ course.remaining <= 0 ? '本周结课' : `还剩 ${course.remaining} 周` }}
              </el-tag>
            </li>
          </ul>
          <p v-if="endingCourses.length" class="hint">
            结课后该时段会空出，可提前安排复习或选课。
          </p>
        </template>
      </el-card>

      <!-- 最近学习计划 -->
      <el-card v-if="!isAdmin" shadow="never" class="panel deadline-panel">
        <template #header><div class="panel-head"><span class="panel-title">近期截止</span><el-link type="primary" :underline="false" @click="go('Assignment')">全部截止事项</el-link></div></template>
        <p class="deadline-explainer">进入系统时提醒 · 近 3 天到期，不提供后台推送</p>
        <el-skeleton v-if="inbox.loading.value" :rows="3" animated />
        <el-alert v-else-if="inbox.error.value" type="error" :title="inbox.error.value" :closable="false"><el-button link @click="loadDeadlines">重试</el-button></el-alert>
        <el-empty v-else-if="!upcomingItems.length" description="近 3 天没有截止事项" :image-size="70" />
        <ul v-else class="deadline-list">
          <li v-for="item in upcomingItems" :key="item.id">
            <div class="deadline-item-head"><router-link :to="`/manager/course/${item.course_id}`">{{ item.title }}</router-link><el-tag size="small" :type="item.type === 'exam' ? 'danger' : 'info'">{{ item.type === 'exam' ? '考试' : '作业' }}</el-tag></div>
            <p>{{ item.course_name }} · {{ formatLocalDateTime(item.due_at) }}</p>
            <div class="deadline-countdown"><el-tag type="warning" effect="plain" size="small">{{ dueLabel(item, reminderNow) }}</el-tag><span v-if="deadlineNotice(item.due_at, calendar)">{{ deadlineNotice(item.due_at, calendar) }}</span></div>
          </li>
        </ul>
        <p v-if="calendarError" class="hint warn">假期信息暂时不可用，未判断截止日是否放假。</p>
      </el-card>

      <!-- 最近学习计划（原文本计划保留） -->
      <el-card v-if="!isAdmin" shadow="never" class="panel">
        <template #header>
          <div class="panel-head">
            <span class="panel-title">最近的学习计划</span>
            <el-link type="primary" :underline="false" @click="go('StudyPlan')">
              全部（{{ planTotal ?? '—' }}）
            </el-link>
          </div>
        </template>
        <el-skeleton v-if="planLoading" :rows="3" animated />
        <template v-else>
          <el-alert v-if="planError" type="error" :title="planError" :closable="false" show-icon>
            <el-button link @click="loadPlans">重新加载学习计划</el-button>
          </el-alert>
          <el-empty
            v-else-if="!latestPlan"
            :image-size="70"
            description="还没有学习计划，去写一份吧"
          >
            <el-button type="primary" @click="go('StudyPlan')">去写计划</el-button>
          </el-empty>
          <div v-else class="plan-box">
            <p class="plan-time">{{ formatPlanTime(latestPlan.create_time) }}</p>
            <p class="plan-content">{{ planSummary }}</p>
            <el-link type="primary" :underline="false" @click="go('StudyPlan')">
              查看完整计划
            </el-link>
          </div>
        </template>
      </el-card>

      <!-- 管理员数据概览 -->
      <el-card v-if="isAdmin" shadow="never" class="panel">
        <template #header>
          <div class="panel-head">
            <span class="panel-title">数据概览</span>
            <el-tag size="small" type="info" effect="plain">管理员</el-tag>
          </div>
        </template>
        <el-skeleton v-if="statsLoading" :rows="3" animated />
        <div v-else class="stat-grid">
          <div v-for="item in stats" :key="item.label" class="stat-item">
            <span class="stat-value">{{ item.value ?? '—' }}</span>
            <span class="stat-label">{{ item.label }}</span>
          </div>
        </div>
        <template v-if="!statsLoading && statsErrors.length">
          <el-alert v-for="message in statsErrors" :key="message" :title="message" type="error" :closable="false" class="stats-error" />
          <el-button link @click="loadStats">重新加载统计</el-button>
        </template>
        <p v-if="!statsLoading && kbWarning" class="hint warn">{{ kbWarning }}</p>
      </el-card>
    </div>

    <!-- 快捷入口 -->
    <el-card shadow="never" class="quick-card">
      <div class="quick-row">
        <el-button
          v-for="shortcut in shortcuts"
          :key="shortcut.route"
          type="primary"
          :plain="shortcut.route !== 'Assistant'"
          size="large"
          @click="go(shortcut.route)"
        >
          <el-icon class="quick-icon"><component :is="shortcut.icon" /></el-icon>
          {{ shortcut.label }}
        </el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, markRaw, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Calendar, ChatDotRound, Notebook, Tickets } from '@element-plus/icons-vue'
import { useUser } from '@/utils/user'
import { getWeekScheduleApi } from '@/api/schedule'
import { listStudyPlans } from '@/api/studyPlan'
import { listCoursesApi } from '@/api/course'
import { getUserPageList } from '@/api/user'
import { listDocumentsApi } from '@/api/document'
import { getLearningContextApi } from '@/api/learningContext'
import { useLearningInbox } from '@/stores/learningInbox'
import { deadlineNotice, dueLabel, ensureSuccess, fetchAllPages, filterAssignments, formatLocalDateTime, isOverdue, sortByDue } from '@/utils/learningItems'

defineOptions({ name: 'Home' })

const router = useRouter()
const { userInfo } = useUser()
const inbox = useLearningInbox()
const calendar = ref(null)
const calendarError = ref(false)
const reminderNow = ref(new Date())
const upcomingItems = computed(() => sortByDue(filterAssignments(inbox.assignments.value, { status: 'pending', dueRange: '3' }, reminderNow.value)))
const overdueItems = computed(() => sortByDue(inbox.assignments.value.filter(item => isOverdue(item, reminderNow.value))))

async function loadDeadlines() {
  if (isAdmin.value) return
  reminderNow.value = new Date()
  await Promise.allSettled([
    inbox.refresh(true),
    getLearningContextApi().then(res => { calendar.value = ensureSuccess(res); calendarError.value = false })
      .catch(() => { calendar.value = null; calendarError.value = true })
  ])
}

const weekLoading = ref(false)
const weekError = ref('')
const nextWeekError = ref('')
const weekData = ref(null)
/** 今天在周日时，明天属于下一周，需要额外取一周的数据 */
const nextWeekData = ref(null)
const planList = ref([])
const planTotal = ref(null)
const planLoading = ref(false)
const planError = ref('')
const courseTotal = ref(null)
const userTotal = ref(null)
const docCount = ref(null)
const chunkTotal = ref(null)
const statsLoading = ref(false)
const statErrors = ref({})
const statsErrors = computed(() => Object.values(statErrors.value).filter(Boolean))
const outsideSemester = computed(() => weekData.value?.current_week === null)

const isAdmin = computed(() => userInfo.value?.role === 'admin')
const WEEKDAY = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

const greeting = computed(() => {
  const hour = new Date().getHours()
  if (hour < 6) return '夜深了'
  if (hour < 12) return '早上好'
  if (hour < 14) return '中午好'
  if (hour < 18) return '下午好'
  return '晚上好'
})

const today = computed(() => {
  const days = weekData.value?.days || []
  return days.find((day) => day.is_today) || null
})

const tomorrow = computed(() => {
  const days = weekData.value?.days || []
  const index = days.findIndex((day) => day.is_today)
  if (index < 0) return null
  if (index < days.length - 1) return days[index + 1]
  // 今天是周日：明天是下一周的周一
  return nextWeekData.value?.days?.[0] || null
})

const todayCourses = computed(() => today.value?.courses || [])
const tomorrowCourses = computed(() => tomorrow.value?.courses || [])

function labelOf(day) {
  if (!day) return ''
  const month = Number(day.date.slice(5, 7))
  const date = Number(day.date.slice(8, 10))
  return `${month}月${date}日 ${WEEKDAY[day.weekday - 1] || ''}`
}

const todayLabel = computed(() => labelOf(today.value) || (weekLoading.value ? '正在读取学期信息…' :
  weekError.value ? '今天的课表暂时不可用' : outsideSemester.value ? '当前不在学期内' : '暂时读不到今天的课表'))
const tomorrowDateLabel = computed(() => labelOf(tomorrow.value))

const semester = computed(() => {
  const data = weekData.value
  if (!data || outsideSemester.value || typeof data.week !== 'number' || !data.total_weeks) return null
  const percent = Math.min(100, Math.round((data.week / data.total_weeks) * 100))
  return {
    week: data.week,
    totalWeeks: data.total_weeks,
    percent,
    rangeText: `${data.start_date} ~ ${data.end_date}`
  }
})

/** 本周全部格子展平，用于结课判断（接口不提供 slots，需自行汇总） */
const weekCourses = computed(() => {
  const days = weekData.value?.days || []
  return days.flatMap((day) => day.courses || [])
})

const endingCourses = computed(() => {
  if (outsideSemester.value) return []
  const current = weekData.value?.week
  if (typeof current !== 'number') return []
  const seen = new Map()
  for (const course of weekCourses.value) {
    if (!course || typeof course.end_week !== 'number') continue
    const remaining = course.end_week - current
    if (remaining > 2) continue
    if (!seen.has(course.course_id)) seen.set(course.course_id, { ...course, remaining })
  }
  return [...seen.values()].sort((a, b) => a.remaining - b.remaining)
})

/** 未到提醒期时，用于显示"最近一次结课"，避免卡片长期空白 */
const nearestEnding = computed(() => {
  if (outsideSemester.value) return null
  const current = weekData.value?.week
  if (typeof current !== 'number') return null
  const seen = new Map()
  for (const course of weekCourses.value) {
    if (!course || typeof course.end_week !== 'number') continue
    const remaining = course.end_week - current
    if (remaining < 0) continue
    const exists = seen.get(course.course_id)
    if (!exists || remaining < exists.remaining) {
      seen.set(course.course_id, { ...course, remaining })
    }
  }
  return [...seen.values()].sort((a, b) => a.remaining - b.remaining)[0] || null
})

const latestPlan = computed(() => planList.value[0] || null)
const planSummary = computed(() => {
  const text = (latestPlan.value?.content || '').replace(/\s+/g, ' ').trim()
  return text.length > 120 ? `${text.slice(0, 120)}…` : text
})

const stats = computed(() => [
  { label: '用户', value: userTotal.value },
  { label: '课程', value: courseTotal.value },
  { label: '知识库文档', value: docCount.value },
  { label: '向量分块', value: chunkTotal.value }
])

const kbWarning = computed(() =>
  isAdmin.value && docCount.value === 0
    ? '知识库还没有文档，助手无法回答资料类问题。可先上传课程资料并入库。'
    : ''
)

const shortcuts = computed(() => {
  const list = [
    { label: '问课程助手', route: 'Assistant', icon: markRaw(ChatDotRound) },
    { label: '看周课表', route: 'ScheduleWeek', icon: markRaw(Calendar) }
  ]
  if (!isAdmin.value) list.push({ label: '写学习计划', route: 'StudyPlan', icon: markRaw(Notebook) })
  if (!isAdmin.value) list.push({ label: '作业与考试', route: 'Assignment', icon: markRaw(Tickets) })
  return list
})

function formatPlanTime(value) {
  if (!value) return ''
  return String(value).replace('T', ' ').slice(0, 16)
}

function go(routeName) {
  router.push({ name: routeName })
}

/** 各项互不影响：失败只影响对应卡片，不整页报错 */
async function loadWeek() {
  if (weekLoading.value) return
  weekLoading.value = true
  weekError.value = ''
  nextWeekError.value = ''
  weekData.value = null
  nextWeekData.value = null
  try {
    const data = ensureSuccess(await getWeekScheduleApi())
    if (!Array.isArray(data?.days) || data.days.length !== 7 || data.days.some(day => !day || !Array.isArray(day.courses))) throw new Error('课表数据格式不正确')
    weekData.value = data
    // 今天是周日时，明天落在下一周，补取一次下周首日
    const days = data.days
    const index = days.findIndex((day) => day.is_today)
    if (index === days.length - 1 && typeof data.week === 'number' && data.week < data.total_weeks) {
      try {
        const next = ensureSuccess(await getWeekScheduleApi(data.week + 1))
        if (!Array.isArray(next?.days) || !next.days.length) throw new Error('明日课表数据格式不正确')
        nextWeekData.value = next
      } catch (failure) {
        nextWeekError.value = failureMessage(failure, '明日课表加载失败，请重试')
      }
    }
  } catch (failure) {
    weekError.value = failureMessage(failure, '课表加载失败，请重试')
  } finally {
    weekLoading.value = false
  }
}

function failureMessage(failure, prefix) {
  const detail = failure?.response?.data?.message || failure?.message
  return detail ? `${prefix}：${detail}` : prefix
}

async function loadPlans() {
  if (planLoading.value) return
  planLoading.value = true
  planError.value = ''
  planList.value = []
  planTotal.value = null
  try {
    const data = ensureSuccess(await listStudyPlans({ page: 1, page_size: 1 }))
    if (!Array.isArray(data?.list) || !Number.isInteger(data.total) || data.total < 0) throw new Error('学习计划数据格式不正确')
    planList.value = data.list
    planTotal.value = data.total
  } catch (failure) {
    planError.value = failureMessage(failure, '学习计划加载失败，请重试')
  } finally {
    planLoading.value = false
  }
}

async function loadStats() {
  if (!isAdmin.value || statsLoading.value) return
  statsLoading.value = true
  statErrors.value = {}
  courseTotal.value = userTotal.value = docCount.value = chunkTotal.value = null
  await Promise.allSettled([
    (async () => {
      try {
        const courses = ensureSuccess(await listCoursesApi())
        if (!Array.isArray(courses)) throw new Error('课程统计数据格式不正确')
        courseTotal.value = courses.length
      } catch (failure) { statErrors.value.course = failureMessage(failure, '课程统计加载失败') }
    })(),
    (async () => {
      try {
        const users = ensureSuccess(await getUserPageList({ page: 1, page_size: 1 }))
        if (!Number.isInteger(users?.total) || users.total < 0) throw new Error('用户统计数据格式不正确')
        userTotal.value = users.total
      } catch (failure) { statErrors.value.user = failureMessage(failure, '用户统计加载失败') }
    })(),
    (async () => {
      try {
        const documents = await fetchAllPages(listDocumentsApi)
        docCount.value = documents.length
        chunkTotal.value = documents.reduce((sum, item) => sum + (item.chunk_count || 0), 0)
      } catch (failure) { statErrors.value.documents = failureMessage(failure, '知识库统计加载失败') }
    })()
  ])
  statsLoading.value = false
}

async function loadAll() {
  const tasks = [loadWeek()]
  if (!isAdmin.value) {
    tasks.push(loadDeadlines())
    tasks.push(loadPlans())
  }
  if (isAdmin.value) {
    tasks.push(loadStats())
  }
  await Promise.allSettled(tasks)
}

onMounted(loadAll)
</script>

<style scoped>
.home-page { max-width: 1200px; margin: 0 auto; }
.deadline-alert { margin-bottom: 18px; border-radius: 12px; }.overdue-summary { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.deadline-explainer { font-size: 12px; color: #909399; margin-top: 0; }.deadline-list { list-style: none; padding: 0; margin: 0; }.deadline-list li { border-bottom: 1px dashed #ebeef5; padding: 12px 0; }.deadline-list li:last-child { border-bottom: 0; }.deadline-item-head, .deadline-countdown { display: flex; align-items: center; justify-content: space-between; gap: 8px; }.deadline-item-head a { color: #29354a; font-weight: 600; text-decoration: none; }.deadline-list p { color: #909399; font-size: 12px; margin: 8px 0; }.deadline-countdown span { color: #b88230; font-size: 12px; }.deadline-countdown { flex-wrap: wrap; }

/* 欢迎区 */
.hero-card { border-radius: 14px; margin-bottom: 18px; }
.hero-row { display: flex; align-items: center; justify-content: space-between; gap: 24px; flex-wrap: wrap; }
.greeting { margin: 0 0 8px; font-size: 22px; color: #253047; }
.hero-sub { display: flex; align-items: center; gap: 8px; margin: 0; color: #7a8598; font-size: 14px; }
.hero-right { min-width: 260px; }
.week-badge { display: flex; align-items: baseline; gap: 6px; }
.week-current { font-size: 20px; font-weight: 600; color: #253047; }
.week-total { font-size: 13px; color: #8b95a5; }
.week-progress { margin: 10px 0 6px; }
.semester-range { margin: 0; font-size: 12px; color: #9aa4b2; }
.hero-skeleton { width: 260px; }

/* 主内容网格 */
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); gap: 18px; margin-bottom: 18px; }
.panel { border-radius: 14px; }
.panel-head { display: flex; align-items: center; justify-content: space-between; }
.panel-title { font-weight: 600; color: #253047; }

/* 课程列表 */
.course-list { margin: 0; padding: 0; list-style: none; }
.course-list li { padding: 12px 0; border-bottom: 1px dashed #eef1f6; }
.course-list li:last-child { border-bottom: none; }
.course-main { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.course-name { font-weight: 600; color: #29354a; }
.course-period { font-size: 12px; color: #6f7d92; background: #f3f5f9; border-radius: 6px; padding: 2px 8px; }
.course-meta { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 6px; font-size: 12px; color: #8b95a5; }
.tomorrow-line { margin: 14px 0 0; padding-top: 12px; border-top: 1px solid #f1f4f9; font-size: 13px; color: #64728a; }
.tomorrow-label { display: inline-block; margin-right: 8px; padding: 1px 8px; border-radius: 6px; background: #eef4ff; color: #3a6fd8; font-size: 12px; }

/* 结课提醒 */
.ending-list { margin: 0; padding: 0; list-style: none; }
.ending-list li { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 11px 0; border-bottom: 1px dashed #eef1f6; }
.ending-list li:last-child { border-bottom: none; }
.ending-name { color: #29354a; }
.hint { margin: 12px 0 0; font-size: 12px; color: #9aa4b2; }
.hint.warn { color: #c45656; }

/* 学习计划 */
.plan-box { display: flex; flex-direction: column; gap: 8px; }
.plan-time { margin: 0; font-size: 12px; color: #9aa4b2; }
.plan-content { margin: 0; line-height: 1.7; color: #3c4a5f; font-size: 14px; }

/* 管理员统计 */
.stat-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; }
.stat-item { display: flex; flex-direction: column; align-items: center; padding: 16px 8px; border-radius: 10px; background: #f7f9fc; }
.stat-value { font-size: 24px; font-weight: 600; color: #253047; }
.stat-label { margin-top: 4px; font-size: 12px; color: #8b95a5; }

/* 快捷入口 */
.quick-card { border-radius: 14px; }
.quick-row { display: flex; gap: 14px; flex-wrap: wrap; }
.quick-icon { margin-right: 6px; }

@media (max-width: 720px) {
  .hero-row { flex-direction: column; align-items: flex-start; }
  .hero-right { width: 100%; min-width: 0; }
  .hero-skeleton { width: 100%; }
}
</style>
