<template>
  <section class="week-page" aria-labelledby="week-page-title">
    <div class="page-heading">
      <div>
        <h2 id="week-page-title">周课表</h2>
        <p>一周课程，一目了然</p>
      </div>
      <div v-if="metadata" class="semester-label">
        <el-icon><Calendar /></el-icon>
        <span>{{ metadata.semester_start_date.slice(0, 4) }} 学年 · 共 {{ metadata.total_weeks }} 周</span>
      </div>
    </div>

    <div class="timetable-panel">
      <div v-if="isAdmin" class="student-toolbar">
        <label for="week-student-selector">查看学生</label>
        <el-select id="week-student-selector" v-model="selectedStudentId" clearable filterable
          :loading="studentsLoading" :placeholder="`我的课表（${userInfo?.username || '管理员'}）`"
          aria-label="选择要查看课表的学生" @change="selectStudent">
          <el-option v-for="student in studentOptions" :key="student.id" :value="student.id"
            :label="`${student.name || student.username}（${student.username}）`" />
        </el-select>
        <span class="viewing-user" aria-live="polite">正在查看 {{ viewingUserLabel }} 的课表</span>
      </div>
      <div v-if="isAdmin && studentsError" class="student-load-error" role="alert">
        <span>{{ studentsError }}</span><el-button text type="primary" @click="loadStudents">重试学生列表</el-button>
      </div>
      <div class="week-navigation">
        <el-button :disabled="!metadata || selectedWeek <= 1" @click="changeWeek(-1)">
          <el-icon><ArrowLeft /></el-icon><span>上一周</span>
        </el-button>
        <div class="week-heading" aria-live="polite">
          <div class="week-number">
            <strong>{{ selectedWeek ? `第 ${selectedWeek} 周` : '正在加载课表' }}</strong>
            <span v-if="isCurrentWeek" class="current-week-badge">本周</span>
          </div>
          <span v-if="dateRange" class="week-range">{{ dateRange }}</span>
          <span v-else class="week-range">正在读取学期安排</span>
        </div>
        <el-button :disabled="!metadata || selectedWeek >= metadata.total_weeks" @click="changeWeek(1)">
          <span>下一周</span><el-icon><ArrowRight /></el-icon>
        </el-button>
        <el-button class="current-week-button" plain type="primary"
          :disabled="!metadata?.current_week || isCurrentWeek"
          :title="metadata && !metadata.current_week ? '今天不在本学期日期范围内' : '回到今天所在周'"
          @click="loadWeek(metadata.current_week)">回到本周</el-button>
      </div>

      <div class="timetable-notes">
        <span><i class="today-legend"></i>今天的课程</span>
        <span class="keyboard-hint">← → 方向键切换周次</span>
      </div>
      <el-alert v-if="notice" :title="notice" type="warning" :closable="false" show-icon
        class="holiday-notice" />

      <div ref="gridViewport" class="grid-viewport" :aria-busy="loading" tabindex="0"
        aria-label="周课表，可横向滚动查看周一至周日">
        <table class="week-grid" :class="{ 'is-loading': loading }">
          <caption class="sr-only">{{ selectedWeek ? `第 ${selectedWeek} 周课程安排` : '周课程安排' }}</caption>
          <colgroup><col class="period-column" /><col v-for="weekday in 7" :key="weekday" /></colgroup>
          <thead>
            <tr>
              <th class="period-header" scope="col">节次</th>
              <th v-for="day in displayDays" :key="day.weekday" scope="col"
                :class="{ 'is-today': day.is_today, 'is-holiday': day.is_holiday }">
                <div class="day-label">{{ WEEKDAY_LABELS[day.weekday - 1] }}</div>
                <div class="day-date">{{ day.date ? formatMonthDay(day.date) : '—' }}</div>
                <div class="day-marker">
                  <span v-if="day.is_holiday" class="holiday-day">{{ day.holiday_name }}</span>
                  <span v-else-if="day.weekday > 5 && day.is_class_day" class="makeup-day">调休上课</span>
                  <span v-else-if="day.is_today" class="today-day">今天</span>
                  <span v-else-if="day.weekday > 5" class="rest-day">休息日</span>
                </div>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, index) in gridRows" :key="row.period || index">
              <th scope="row" class="period-cell">
                <span v-if="row.period" class="period-name">{{ row.period }}</span>
                <el-skeleton-item v-else variant="text" class="period-skeleton" />
                <span class="period-detail">共 2 节</span>
              </th>
              <td v-for="cell in row.cells" :key="cell.day.weekday" class="schedule-cell"
                :class="{ 'holiday-cell': cell.day.is_holiday, 'weekend-cell': cell.day.weekday > 5 }">
                <article v-if="cell.course" class="course-card" :class="{ 'today-course': cell.day.is_today }"
                  :style="courseColor(cell.course.course_id)" :title="courseTitle(cell.course)"
                  :aria-label="courseTitle(cell.course)" tabindex="0" role="link"
                  @click="$router.push(`/manager/course/${cell.course.course_id}`)"
                  @keydown.enter.prevent="$router.push(`/manager/course/${cell.course.course_id}`)"
                  @keydown.space.prevent="$router.push(`/manager/course/${cell.course.course_id}`)">
                  <h3 class="course-name">{{ cell.course.course_name }}</h3>
                  <p class="course-teacher">{{ cell.course.teacher || '教师未填写' }}</p>
                  <p class="course-period">{{ cell.course.period }}</p>
                  <p class="course-location"><el-icon><Location /></el-icon>{{ cell.course.location || '地点未填写' }}</p>
                  <div class="course-footer"><span>{{ cell.course.start_week }}-{{ cell.course.end_week }}周</span>
                    <span v-if="cell.day.is_today" class="course-today-label">今天</span></div>
                </article>
                <div v-else-if="loading" class="cell-skeleton" aria-hidden="true">
                  <el-skeleton-item variant="text" /><el-skeleton-item variant="text" />
                </div>
                <span v-else class="empty-cell" aria-label="本节无课">—</span>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="error" class="grid-message error-message" role="alert">
          <el-icon><WarningFilled /></el-icon><strong>课表加载失败</strong>
          <p>{{ error }}</p><el-button type="primary" @click="retry">重新加载</el-button>
        </div>
        <div v-else-if="loading" class="loading-status" role="status">
          <el-icon class="is-loading"><Loading /></el-icon><span>正在加载第 {{ selectedWeek || '本' }} 周</span>
        </div>
      </div>
      <div v-if="!loading && !error && weekView && !hasCourses" class="empty-week" role="status">
        <el-icon><Calendar /></el-icon><span>本周暂无课程安排，空档已为你保留。</span>
      </div>
      <div class="timetable-footer">
        <span>同一课程使用固定颜色 · 放假日期不排课</span>
        <span class="scroll-hint">窄屏可左右滚动查看完整课表</span>
      </div>

      <!-- 课程时间轴：把 start_week/end_week 可视化 -->
      <div v-if="!loading && !error && timeline.length" class="timeline-panel">
        <div class="timeline-head">
          <strong>课程时间轴</strong>
          <span class="timeline-hint">共 {{ timeline.length }} 门课 · 竖线是第 {{ selectedWeek }} 周</span>
        </div>
        <div class="timeline-scale">
          <span v-for="mark in timelineMarks" :key="mark" class="timeline-mark" :style="{ left: `${markPercent(mark)}%` }">
            {{ mark }}
          </span>
        </div>
        <div class="timeline-rows">
          <div v-for="item in timeline" :key="item.courseId" class="timeline-row">
            <div class="timeline-label" :title="item.name">
              <span class="timeline-name">{{ item.name }}</span>
              <small>{{ item.teacher || '未填写' }}</small>
            </div>
            <div class="timeline-track">
              <div
                class="timeline-bar"
                :style="{ ...courseColor(item.courseId), left: `${item.leftPercent}%`, width: `${item.widthPercent}%` }"
                :title="`${item.name}｜第 ${item.startWeek}-${item.endWeek} 周｜${item.ended ? '已结课' : `还剩 ${item.remaining} 周`}`"
              >
                <span class="timeline-bar-text">{{ item.startWeek }}-{{ item.endWeek }}周</span>
              </div>
              <div class="timeline-cursor" :style="{ left: `${item.currentPercent}%` }"></div>
            </div>
            <el-tag
              class="timeline-tag"
              size="small"
              :type="item.ended ? 'info' : item.remaining <= 2 ? 'warning' : 'success'"
              effect="plain"
            >{{ item.ended ? '已结课' : `剩 ${item.remaining} 周` }}</el-tag>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ArrowLeft, ArrowRight, Calendar, Loading, Location, WarningFilled } from '@element-plus/icons-vue'
import { getWeekScheduleApi } from '@/api/schedule'
import { getUserPageList } from '@/api/user'
import { useUser } from '@/utils/user'
import { adjacentWeek, buildCourseTimeline, buildWeekRows, courseColor, courseTitle, createWeekLoader, datesForWeek,
  formatChineseDate, formatMonthDay, holidayNotice, keyboardWeekDirection, WEEKDAY_LABELS } from '@/utils/weekSchedule'

defineOptions({ name: 'ScheduleWeek' })
const { userInfo } = useUser()
const isAdmin = computed(() => userInfo.value?.role === 'admin')
const selectedStudentId = ref(null)
const studentOptions = ref([])
const studentsLoading = ref(false)
const studentsError = ref('')
const targetUserId = computed(() => isAdmin.value && selectedStudentId.value != null
  ? selectedStudentId.value : userInfo.value?.id)
const selectedWeek = ref(null)
const metadata = ref(null)
const data = ref(null)
const loading = ref(false)
const error = ref('')
const gridViewport = ref(null)
const loader = createWeekLoader(getWeekScheduleApi)
let requestId = 0
let disposed = false

const weekView = computed(() => data.value?.week === selectedWeek.value &&
  data.value?.user_id === targetUserId.value ? data.value : null)
const viewingUserLabel = computed(() => {
  const user = weekView.value || (targetUserId.value === userInfo.value?.id ? userInfo.value :
    studentOptions.value.find((student) => student.id === targetUserId.value))
  const name = user?.name || user?.username || `学生 ID ${targetUserId.value}`
  return user?.username && name !== user.username ? `${name}（${user.username}）` : name
})
const isCurrentWeek = computed(() => !!metadata.value?.current_week && selectedWeek.value === metadata.value.current_week)

/** 课程时间轴：数据来自当前展示的这一周，把各课起止周画成横条 */
const timeline = computed(() => buildCourseTimeline(weekView.value, selectedWeek.value))
const timelineMarks = computed(() => {
  const total = Number(weekView.value?.total_weeks)
  if (!Number.isInteger(total) || total < 1) return []
  const step = total > 16 ? 4 : 2
  const marks = []
  for (let week = 1; week <= total; week += step) marks.push(week)
  if (marks[marks.length - 1] !== total) marks.push(total)
  return marks
})
const markPercent = (week) => {
  const total = Number(weekView.value?.total_weeks)
  return Number.isInteger(total) && total > 0 ? ((week - 1) / total) * 100 : 0
}
const displayDays = computed(() => {
  if (weekView.value) return [...weekView.value.days].sort((a, b) => a.weekday - b.weekday)
  const dates = metadata.value && selectedWeek.value ? datesForWeek(metadata.value.semester_start_date, selectedWeek.value) : []
  return Array.from({ length: 7 }, (_, index) => ({ weekday: index + 1, date: dates[index] || '', courses: [] }))
})
const gridRows = computed(() => weekView.value ? buildWeekRows(weekView.value) :
  Array.from({ length: 4 }, (_, index) => ({ period: metadata.value?.periods[index] || '',
    cells: displayDays.value.map((day) => ({ day, course: null })) })))
const dateRange = computed(() => {
  const days = displayDays.value
  return days[0].date ? `${formatChineseDate(days[0].date)} - ${formatChineseDate(days[6].date)}` : ''
})
const notice = computed(() => holidayNotice(weekView.value))
const hasCourses = computed(() => weekView.value?.days.some((day) => day.courses.length > 0))

async function loadWeek(week, options = {}) {
  if (disposed) return
  const thisRequest = ++requestId
  const userId = isAdmin.value ? selectedStudentId.value ?? undefined : undefined
  const scroll = { left: gridViewport.value?.scrollLeft || 0, top: gridViewport.value?.scrollTop || 0 }
  if (week !== undefined && week !== null) selectedWeek.value = week
  loading.value = true
  error.value = ''
  try {
    const response = await loader.load(week, { ...options, userId })
    if (disposed || thisRequest !== requestId) return
    if (response.user_id !== targetUserId.value) throw new Error('课表返回用户与所选用户不一致，请重新加载')
    metadata.value = response
    selectedWeek.value = response.week
    data.value = response
  } catch (cause) {
    if (disposed || thisRequest !== requestId) return
    data.value = null
    error.value = cause.message || '请检查网络后重新加载'
  } finally {
    if (!disposed && thisRequest === requestId) {
      loading.value = false
      await nextTick()
      if (!disposed && thisRequest === requestId && gridViewport.value) {
        gridViewport.value.scrollLeft = scroll.left
        gridViewport.value.scrollTop = scroll.top
      }
    }
  }
}

function selectStudent(id) {
  selectedStudentId.value = id === '' || id == null ? null : id
  return loadWeek(selectedWeek.value ?? undefined)
}

async function loadStudents() {
  if (!isAdmin.value || studentsLoading.value || disposed) return
  studentsLoading.value = true
  studentsError.value = ''
  try {
    const students = []
    let page = 1
    while (!disposed) {
      const response = await getUserPageList({ page, page_size: 200 })
      if (disposed) return
      if (response?.code !== 200 || !response.data) throw new Error(response?.message || '学生列表加载失败')
      const users = response.data.list || []
      students.push(...users.filter((user) => user.role === 'student'))
      if (page * 200 >= response.data.total || !users.length) break
      page++
    }
    if (!disposed) studentOptions.value = students
  } catch (cause) {
    if (!disposed) studentsError.value = cause.message || '学生列表加载失败，请重试'
  } finally {
    if (!disposed) studentsLoading.value = false
  }
}

function changeWeek(direction) {
  if (!metadata.value) return
  const week = adjacentWeek(selectedWeek.value, direction, metadata.value.total_weeks)
  if (week !== selectedWeek.value) return loadWeek(week)
}
function retry() { return loadWeek(selectedWeek.value ?? undefined, { fresh: true }) }
function handleKey(event) {
  if ([...document.querySelectorAll('.el-overlay-dialog, [role="dialog"]')]
    .some((dialog) => dialog.getClientRects().length)) return
  const direction = keyboardWeekDirection(event)
  if (!direction || !metadata.value) return
  event.preventDefault()
  changeWeek(direction)
}
onMounted(() => {
  window.addEventListener('keydown', handleKey)
  loadWeek()
  loadStudents()
})
onBeforeUnmount(() => {
  disposed = true
  ++requestId
  loader.dispose()
  window.removeEventListener('keydown', handleKey)
})
</script>

<style scoped>
.week-page { width: 100%; min-width: 0; max-width: 1500px; margin: 0 auto; color: var(--el-text-color-primary); }
.page-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 22px; }
.page-heading h2 { font-size: 24px; font-weight: 650; letter-spacing: .4px; margin-bottom: 7px; }
.page-heading p { font-size: 13px; color: var(--el-text-color-secondary); }
.semester-label { display: flex; align-items: center; gap: 7px; color: var(--el-text-color-secondary); font-size: 13px; }
.timetable-panel { border: 1px solid var(--el-border-color-light); border-radius: 14px; padding: 22px; background: var(--el-bg-color); box-shadow: 0 5px 24px rgba(30, 47, 75, .035); }
.student-toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; padding-bottom: 16px; margin-bottom: 18px; border-bottom: 1px solid var(--el-border-color-lighter); }
.student-toolbar label { font-size: 13px; color: var(--el-text-color-regular); }
.student-toolbar .el-select { width: 240px; }
.viewing-user { font-size: 12px; color: var(--el-text-color-secondary); }
.student-load-error { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; font-size: 12px; color: var(--el-color-danger); }
.week-navigation { display: flex; align-items: center; justify-content: center; gap: 16px; position: relative; margin-bottom: 18px; }
.week-navigation .el-button { margin-left: 0; }
.week-navigation .el-icon + span, .week-navigation span + .el-icon { margin-left: 6px; }
.week-heading { min-width: 220px; text-align: center; }
.week-number { display: flex; justify-content: center; align-items: center; gap: 9px; min-height: 25px; }
.week-number strong { font-size: 19px; font-weight: 650; }
.current-week-badge { border-radius: 20px; background: var(--el-color-primary-light-9); color: var(--el-color-primary); font-size: 11px; padding: 3px 9px; font-weight: 600; }
.week-range { display: block; font-size: 12px; color: var(--el-text-color-secondary); margin-top: 5px; }
.current-week-button { position: absolute; right: 0; }
.timetable-notes { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; color: var(--el-text-color-secondary); font-size: 12px; }
.timetable-notes > span:first-child { display: flex; align-items: center; gap: 6px; }
.today-legend { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--el-color-primary); }
.holiday-notice { margin-bottom: 14px; border: 1px solid var(--el-color-warning-light-8); border-radius: 8px; }
.grid-viewport { position: relative; overflow: auto; max-height: 74vh; border: 1px solid var(--el-border-color-light); border-radius: 10px; isolation: isolate; }
.grid-viewport:focus-visible { outline: 2px solid var(--el-color-primary-light-5); outline-offset: 3px; }
.week-grid { width: 100%; min-width: 1100px; table-layout: fixed; border-collapse: separate; border-spacing: 0; }
.period-column { width: 96px; }
.week-grid th, .week-grid td { border-right: 1px solid var(--el-border-color-lighter); border-bottom: 1px solid var(--el-border-color-lighter); }
.week-grid tr > :last-child { border-right: none; }
.week-grid tbody tr:last-child > * { border-bottom: none; }
.week-grid thead th { position: sticky; top: 0; z-index: 3; height: 96px; padding: 12px 6px 9px; text-align: center; background: #f8fafc; font-weight: 500; }
.week-grid thead .period-header { left: 0; z-index: 5; font-size: 13px; color: var(--el-text-color-secondary); }
.day-label { font-size: 14px; font-weight: 600; }
.day-date { margin-top: 5px; font-size: 12px; color: var(--el-text-color-secondary); }
.day-marker { height: 18px; margin-top: 5px; font-size: 10px; line-height: 18px; }
.holiday-day { color: var(--el-color-warning-dark-2); }
.makeup-day { color: var(--el-color-success-dark-2); }
.rest-day { color: var(--el-text-color-placeholder); }
.today-day { color: var(--el-color-primary); font-weight: 600; }
.week-grid thead .is-today { background: var(--el-color-primary-light-9); box-shadow: inset 0 3px var(--el-color-primary); }
.week-grid thead .is-holiday { background: var(--el-color-warning-light-9); }
.period-cell { position: sticky; left: 0; z-index: 2; width: 96px; padding: 12px 6px; background: #f8fafc; font-weight: 500; }
.period-name { display: block; font-size: 14px; font-weight: 600; white-space: nowrap; }
.period-detail { display: block; color: var(--el-text-color-placeholder); font-size: 11px; margin-top: 9px; }
.period-skeleton { width: 48px; }
.schedule-cell { height: 178px; padding: 8px; vertical-align: top; background: var(--el-bg-color); }
.weekend-cell { background: #fcfcfd; }
.holiday-cell { background: #fffcf6; }
.course-card { width: 100%; height: 160px; min-width: 0; border: 1px solid var(--course-border); border-left: 3px solid var(--course-accent); border-radius: 8px; padding: 11px 10px; background: var(--course-bg); box-shadow: 0 2px 5px rgba(30, 47, 75, .035); display: flex; flex-direction: column; gap: 8px; overflow: hidden; }
.course-card { cursor: pointer; }
.course-card:focus-visible { outline: 2px solid var(--course-accent); outline-offset: 1px; }
.course-name, .course-teacher, .course-period, .course-location { white-space: nowrap; text-overflow: ellipsis; overflow: hidden; min-width: 0; line-height: 17px; flex-shrink: 0; }
.course-name { font-size: 13px; font-weight: 650; color: var(--course-text); }
.course-teacher { font-size: 12px; color: var(--el-text-color-regular); }
.course-period { font-size: 11px; color: var(--el-text-color-secondary); }
.course-location { font-size: 11px; color: var(--el-text-color-secondary); }
.course-location .el-icon { margin-right: 3px; vertical-align: -2px; }
.course-footer { display: flex; align-items: center; justify-content: space-between; gap: 4px; margin-top: auto; font-size: 10px; color: var(--course-text); white-space: nowrap; }
.course-today-label { font-weight: 650; color: var(--el-color-primary); }
.today-course { outline: 2px solid var(--el-color-primary-light-3); outline-offset: -1px; box-shadow: 0 2px 8px rgba(64, 158, 255, .12); }
.empty-cell { display: flex; align-items: center; justify-content: center; height: 160px; color: var(--el-border-color); font-size: 16px; }
.cell-skeleton { height: 160px; padding: 16px 5px; opacity: .5; }
.cell-skeleton .el-skeleton__item + .el-skeleton__item { margin-top: 18px; width: 70%; }
.loading-status { position: sticky; z-index: 6; bottom: 15px; left: 50%; display: flex; justify-content: center; align-items: center; gap: 8px; width: 190px; margin: -45px auto 15px; padding: 10px; color: var(--el-color-primary); font-size: 12px; background: var(--el-bg-color); border: 1px solid var(--el-color-primary-light-7); box-shadow: 0 3px 12px rgba(30, 47, 75, .08); border-radius: 20px; }
.grid-message { position: absolute; z-index: 6; inset: 96px 0 0; min-width: min(100%, 300px); background: rgba(255,255,255,.94); display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; padding: 24px; text-align: center; }
.error-message > .el-icon { font-size: 28px; color: var(--el-color-warning); }
.error-message p { color: var(--el-text-color-secondary); font-size: 13px; max-width: 400px; overflow-wrap: anywhere; }
.empty-week { display: flex; align-items: center; justify-content: center; gap: 7px; padding: 14px 0 0; color: var(--el-text-color-secondary); font-size: 13px; }
.timetable-footer { display: flex; justify-content: space-between; gap: 12px; margin-top: 14px; font-size: 11px; color: var(--el-text-color-placeholder); }

/* 课程时间轴 */
.timeline-panel { margin-top: 26px; padding-top: 20px; border-top: 1px dashed var(--el-border-color-light); }
.timeline-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.timeline-head strong { color: #253047; }
.timeline-hint { font-size: 12px; color: var(--el-text-color-secondary); }
.timeline-scale { position: relative; height: 16px; margin-left: 150px; margin-right: 74px; }
.timeline-mark { position: absolute; transform: translateX(-50%); font-size: 11px; color: var(--el-text-color-placeholder); }
.timeline-rows { display: flex; flex-direction: column; gap: 8px; margin-top: 4px; }
.timeline-row { display: grid; grid-template-columns: 144px minmax(0, 1fr) 68px; align-items: center; gap: 6px; }
.timeline-label { min-width: 0; line-height: 1.25; }
.timeline-name { display: block; font-size: 13px; color: #29354a; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.timeline-label small { color: var(--el-text-color-placeholder); font-size: 11px; }
.timeline-track { position: relative; height: 22px; border-radius: 6px; background: repeating-linear-gradient(90deg, var(--el-fill-color-lighter) 0 1px, transparent 1px 5%); }
.timeline-bar { position: absolute; top: 3px; height: 16px; display: flex; align-items: center;
  padding: 0 8px; border-radius: 8px; border: 1px solid var(--course-border); background: var(--course-bg); overflow: hidden; }
.timeline-bar-text { font-size: 11px; color: var(--course-text); white-space: nowrap; }
.timeline-cursor { position: absolute; top: 0; width: 2px; height: 22px; background: var(--el-color-primary); opacity: .55; }
.timeline-tag { justify-self: end; }
@media (max-width: 720px) {
  .timeline-scale { margin-left: 0; margin-right: 0; }
  .timeline-row { grid-template-columns: 92px minmax(0, 1fr); }
  .timeline-tag { grid-column: 2; justify-self: start; }
}
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }
@media (max-width: 1250px) { .current-week-button { position: static; } .week-navigation { gap: 10px; } }
@media (max-width: 720px) {
  .page-heading { align-items: flex-start; flex-direction: column; gap: 10px; margin-bottom: 16px; }
  .page-heading h2 { font-size: 21px; }
  .timetable-panel { padding: 14px; }
  .student-toolbar .el-select { max-width: 100%; }
  .viewing-user { width: 100%; }
  .week-navigation { flex-wrap: wrap; gap: 10px 6px; }
  .week-heading { min-width: 140px; flex: 1; }
  .week-number strong { font-size: 17px; }
  .week-navigation .el-button { padding: 8px; }
  .week-navigation .current-week-button { width: 100%; margin-top: 2px; }
  .keyboard-hint { display: none; }
  .timetable-footer { flex-direction: column; gap: 5px; }
}
</style>
