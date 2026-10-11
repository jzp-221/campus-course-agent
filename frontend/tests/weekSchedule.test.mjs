import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import * as Icons from '@element-plus/icons-vue'
import { renderToString } from '@vue/server-renderer'
import * as weekUtils from '../src/utils/weekSchedule.js'

const { adjacentWeek, buildCourseTimeline, buildWeekRows, courseColor, createWeekLoader, datesForWeek,
  holidayNotice, keyboardWeekDirection } = weekUtils
const source = readFileSync(new URL('../src/views/ScheduleWeek.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source, { filename: 'ScheduleWeek.vue' })
const script = compileScript(descriptor, { id: 'schedule-week-test' }).content
const template = compileTemplate({ source: descriptor.template.content,
  filename: 'ScheduleWeek.vue', id: 'schedule-week-test' }).code
const periods = ['1-2节', '3-4节', '5-6节', '7-8节']

function weekData(week = 5, { courses = true, userId = 2 } = {}) {
  const dates = datesForWeek('2026-09-07', week)
  const days = dates.map((date, index) => ({
    date, weekday: index + 1, is_today: date === '2026-10-10',
    is_holiday: date >= '2026-10-01' && date <= '2026-10-07',
    holiday_name: date >= '2026-10-01' && date <= '2026-10-07' ? '国庆节' : null,
    is_class_day: date === '2026-09-20' || date === '2026-10-10' ||
      (index < 5 && !(date >= '2026-10-01' && date <= '2026-10-07')),
    courses: []
  }))
  if (courses) {
    const day = week === 2 ? days[6] : days[5]
    day.courses.push({ id: userId * 100 + 500, user_id: userId, course_id: 61, course_name: 'Web 应用开发',
      period: periods[2], teacher: '胡佳宁', location: '信息楼机房402', start_week: 1, end_week: 20 })
  }
  return { user_id: userId, username: userId === 1 ? 'admin' : userId === 2 ? 'aaa' : 'bbb',
    name: userId === 1 ? '管理员' : userId === 2 ? '学生甲' : '学生乙',
    week, start_date: dates[0], end_date: dates[6], is_current_week: week === 5,
    total_weeks: 20, current_week: 5, today: '2026-10-10', semester_start_date: '2026-09-07',
    semester_end_date: '2027-01-24', periods, days, has_holidays: week === 4 || week === 5,
    holiday_notice: week === 4 || week === 5 ? '10/1-10/7 国庆节放假' : '' }
}
const response = (data) => ({ code: 200, data })
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }

function evaluateModule(code, modules) {
  const bindings = {}
  const executable = code.replace(/import\s+\{([\s\S]*?)\}\s+from\s+['"]([^'"]+)['"]\s*/g,
    (_, imports, name) => {
      for (const binding of imports.split(',')) {
        const [original, alias] = binding.trim().split(/\s+as\s+/)
        bindings[alias || original] = modules[name][original]
      }
      return ''
    }).replace(/^export default/m, 'return').replace(/^export function render/m, 'return function render')
  return new Function(...Object.keys(bindings), executable)(...Object.values(bindings))
}

function fixture(api = async (week) => response(weekData(week ?? 5)), {
  user = { id: 2, username: 'aaa', name: '学生甲', role: 'student' },
  usersApi = async () => ({ code: 200, data: { list: [], total: 0 } })
} = {}) {
  let mount, unmount
  const component = evaluateModule(script, {
    vue: { ...Vue, onMounted: (handler) => { mount = handler },
      onBeforeUnmount: (handler) => { unmount = handler } },
    '@element-plus/icons-vue': Icons,
    '@/api/schedule': { getWeekScheduleApi: api },
    '@/api/user': { getUserPageList: usersApi },
    '@/utils/user': { useUser: () => ({ userInfo: Vue.ref(user) }) },
    '@/utils/weekSchedule': weekUtils
  })
  const state = component.setup({}, { expose: () => {} })
  const render = evaluateModule(template, { vue: Vue })
  async function html() {
    const app = Vue.createSSRApp({ setup: () => ({ ...state }), render, components: Icons })
    app.component('ElButton', { props: ['disabled'], setup: (props, { slots }) =>
      () => Vue.h('button', { disabled: props.disabled }, slots.default?.()) })
    app.component('ElIcon', { setup: (_, { slots }) => () => Vue.h('span', slots.default?.()) })
    app.component('ElSkeletonItem', { setup: () => () => Vue.h('span', { class: 'skeleton' }) })
    app.component('ElAlert', { props: ['title'], setup: (props) => () => Vue.h('div', { role: 'alert' }, props.title) })
    app.component('ElSelect', { setup: (_, { slots }) => () => Vue.h('select', slots.default?.()) })
    app.component('ElOption', { props: ['label', 'value'], setup: (props) => () =>
      Vue.h('option', { value: props.value }, props.label) })
    return renderToString(app)
  }
  return { state, html, mount: () => mount(), unmount: () => unmount() }
}

test('week grid keeps 28 cells, orders weekdays and puts a makeup lesson in its actual weekend cell', () => {
  const data = weekData(5)
  data.days.reverse()
  const rows = buildWeekRows(data)
  assert.equal(rows.length, 4)
  assert.ok(rows.every((row) => row.cells.length === 7))
  assert.deepEqual(rows[0].cells.map((cell) => cell.day.weekday), [1, 2, 3, 4, 5, 6, 7])
  assert.equal(rows[2].cells[5].course.course_name, 'Web 应用开发')
  assert.equal(rows[2].cells[6].course, null)
  assert.equal(rows.flatMap((row) => row.cells).filter((cell) => cell.course).length, 1)
})

test('a course ID selects the same Element Plus color across weeks and serialized IDs', () => {
  const first = courseColor(61)
  const nextWeek = courseColor(JSON.parse(JSON.stringify({ course_id: 61 })).course_id)
  assert.deepEqual(first, nextWeek)
  assert.deepEqual(first, courseColor('61'))
  assert.ok(Object.values(first).every((color) => color.startsWith('var(--el-color-')))
})

test('navigation clamps week one and twenty; dates cross the year without local timezone drift', () => {
  assert.equal(adjacentWeek(1, -1, 20), 1)
  assert.equal(adjacentWeek(20, 1, 20), 20)
  assert.equal(adjacentWeek(5, -1, 20), 4)
  assert.deepEqual([datesForWeek('2026-09-07', 1)[0], datesForWeek('2026-09-07', 20)[6]],
    ['2026-09-07', '2027-01-24'])
})

test('the rendered page retains all columns and cells and displays holiday, makeup, and full course information', async () => {
  const { state, html } = fixture()
  await state.loadWeek(5)
  const page = await html()
  assert.equal((page.match(/class="schedule-cell/g) || []).length, 28)
  assert.equal((page.match(/class="day-label"/g) || []).length, 7)
  assert.equal((page.match(/class="period-cell"/g) || []).length, 4)
  assert.match(page, /10\/1-10\/7 国庆节放假/)
  assert.match(page, /调休上课/)
  assert.match(page, /today-course/)
  assert.match(page, /胡佳宁/)
  assert.match(page, /信息楼机房402/)
  assert.match(page, /1-20周/)
  assert.match(page, /title="Web 应用开发/)
  assert.match(page, /class="current-week-badge">本周/)
})

test('the actual template renders the week-two Sunday course and empty ordinary Sunday', async () => {
  const { state, html } = fixture()
  await state.loadWeek(2)
  assert.equal(state.gridRows.value[2].cells[6].course.course_id, 61)
  let page = await html()
  assert.match(page, /9\/20/)
  assert.match(page, /makeup-day">调休上课/)
  await state.loadWeek(5)
  assert.equal(state.gridRows.value[2].cells[6].course, null)
  page = await html()
  assert.match(page, /10\/11/)
  assert.match(page, /rest-day">休息日/)
})

test('the rendered navigation disables only the corresponding boundary button', async () => {
  const { state, html } = fixture()
  await state.loadWeek(1)
  let page = await html()
  assert.match(page, /<button[^>]*disabled[^>]*>.*?上一周/)
  assert.doesNotMatch(page, /<button[^>]*disabled[^>]*><span>下一周/)
  await state.loadWeek(20)
  page = await html()
  assert.match(page, /<button[^>]*disabled[^>]*><span>下一周/)
})

test('empty-week and retry states render without hiding the 28-cell timetable', async () => {
  let fail = false
  const { state, html } = fixture(async (week) => {
    if (fail) return { code: 400, message: '课表暂时无法读取' }
    return response(weekData(week, { courses: false }))
  })
  await state.loadWeek(6)
  assert.match(await html(), /本周暂无课程安排/)
  fail = true
  await state.loadWeek(7)
  assert.match(await html(), /课表暂时无法读取/)
  assert.match(await html(), /重新加载/)
  fail = false
  await state.retry()
  assert.equal(state.error.value, '')
  assert.equal(state.weekView.value.week, 7)
})

test('cache avoids repeat requests, deduplicates in-flight requests and fresh retries refetch', async () => {
  let count = 0
  const gate = deferred()
  const loader = createWeekLoader(async () => { count++; await gate.promise; return response(weekData(5)) })
  const first = loader.load(5), second = loader.load(5)
  assert.equal(first, second)
  gate.resolve()
  await first
  await loader.load(5)
  assert.equal(count, 1)
  await loader.load(5, { fresh: true })
  assert.equal(count, 2)
  loader.dispose()
})

test('a delayed old week cannot overwrite a newer selection, and scroll position survives switching', async () => {
  const gates = new Map([[6, deferred()], [7, deferred()]])
  const { state } = fixture((week) => week === 5 ? response(weekData(5)) : gates.get(week).promise)
  await state.loadWeek(5)
  state.gridViewport.value = { scrollLeft: 240, scrollTop: 115 }
  const six = state.loadWeek(6), seven = state.loadWeek(7)
  gates.get(7).resolve(response(weekData(7)))
  await seven
  gates.get(6).resolve(response(weekData(6)))
  await six
  assert.equal(state.selectedWeek.value, 7)
  assert.equal(state.weekView.value.week, 7)
  assert.deepEqual(state.gridViewport.value, { scrollLeft: 240, scrollTop: 115 })
  await state.loadWeek(6)
  assert.equal(state.weekView.value.week, 6)
})

test('an old request failure does not show an error for the newer week', async () => {
  const gate = deferred()
  const { state } = fixture((week) => week === 6 ? gate.promise : response(weekData(week)))
  await state.loadWeek(5)
  const six = state.loadWeek(6)
  await state.loadWeek(7)
  gate.reject(new Error('第六周请求失败'))
  await six
  assert.equal(state.error.value, '')
  assert.equal(state.weekView.value.week, 7)
})

test('unmount invalidates late responses and aborts requests instead of updating the closed page', async () => {
  const gate = deferred()
  let signal
  const { state, unmount } = fixture((_, options) => { signal = options.signal; return gate.promise })
  const loading = state.loadWeek(5)
  await Promise.resolve()
  const previousWindow = globalThis.window
  globalThis.window = { removeEventListener() {} }
  try { unmount() } finally { globalThis.window = previousWindow }
  assert.equal(signal.aborted, true)
  gate.resolve(response(weekData(5)))
  await loading
  assert.equal(state.data.value, null)
})

test('keyboard flipping ignores editable inputs, dialogs, modifiers and composition', () => {
  assert.equal(keyboardWeekDirection({ key: 'ArrowLeft' }), -1)
  assert.equal(keyboardWeekDirection({ key: 'ArrowRight' }), 1)
  for (const extras of [{ ctrlKey: true }, { isComposing: true },
    { target: { isContentEditable: true } }, { target: { closest: () => ({}) } }]) {
    assert.equal(keyboardWeekDirection({ key: 'ArrowRight', ...extras }), 0)
  }
})

test('holiday notice distinguishes holiday weeks and ordinary weeks; duplicate cell data is explicit failure', () => {
  assert.equal(holidayNotice(weekData(5)), '10/1-10/7 国庆节放假')
  assert.equal(holidayNotice(weekData(2)), '')
  const broken = weekData(5)
  broken.days[5].courses.push({ ...broken.days[5].courses[0], course_id: 99 })
  assert.throws(() => buildWeekRows(broken), /冲突/)
})

const admin = { id: 1, username: 'admin', name: '管理员', role: 'admin' }

test('student page has no user selector and sends no selectable user ID', async () => {
  const calls = []
  let userListCalls = 0
  const { state, html } = fixture(async (week, options) => {
    calls.push(options.userId)
    return response(weekData(week ?? 5))
  }, { usersApi: async () => { userListCalls++; return { code: 200, data: { list: [], total: 0 } } } })
  await state.loadStudents()
  await state.loadWeek(5)
  await state.selectStudent(3)
  assert.equal(state.targetUserId.value, 2)
  assert.equal(state.weekView.value.user_id, 2)
  assert.ok(calls.every((id) => id === undefined))
  assert.equal(userListCalls, 0)
  assert.doesNotMatch(await html(), /week-student-selector|正在查看/)
})

test('administrator selects a student, sees the response identity, and clearing restores their own timetable', async () => {
  const calls = []
  const { state, html } = fixture(async (week, { userId }) => {
    calls.push(userId)
    return response(weekData(week ?? 5, { userId: userId ?? 1, courses: userId !== undefined }))
  }, { user: admin })
  await state.loadWeek(6)
  let page = await html()
  assert.match(page, /week-student-selector/)
  assert.match(page, /正在查看 管理员（admin） 的课表/)
  assert.equal(state.weekView.value.user_id, 1)
  await state.selectStudent(2)
  page = await html()
  assert.match(page, /正在查看 学生甲（aaa） 的课表/)
  assert.equal(state.weekView.value.user_id, 2)
  await state.selectStudent('')
  assert.equal(state.weekView.value.user_id, 1)
  assert.equal(state.selectedWeek.value, 6)
  assert.deepEqual(calls, [undefined, 2])
})

test('administrator cache separates students and keeps the same course color and viewport across changes', async () => {
  const calls = []
  const { state } = fixture(async (week, { userId }) => {
    calls.push([week, userId])
    return response(weekData(week, { userId: userId ?? 1 }))
  }, { user: admin })
  state.gridViewport.value = { scrollLeft: 320, scrollTop: 80 }
  await state.selectStudent(2)
  const color = courseColor(state.weekView.value.days[5].courses[0].course_id)
  await state.selectStudent(3)
  assert.equal(state.weekView.value.user_id, 3)
  await state.selectStudent(2)
  assert.equal(state.weekView.value.user_id, 2)
  assert.deepEqual(courseColor(state.weekView.value.days[5].courses[0].course_id), color)
  assert.deepEqual(calls, [[undefined, 2], [5, 3]])
  assert.deepEqual(state.gridViewport.value, { scrollLeft: 320, scrollTop: 80 })
})

test('same-week in-flight requests for different students cannot be shared or overwrite a newer user', async () => {
  const gates = new Map([[2, deferred()], [3, deferred()]])
  const { state } = fixture((_, { userId }) => gates.get(userId).promise, { user: admin })
  const aaa = state.selectStudent(2)
  const bbb = state.selectStudent(3)
  assert.equal(state.weekView.value, null)
  gates.get(3).resolve(response(weekData(5, { userId: 3 })))
  await bbb
  gates.get(2).resolve(response(weekData(5, { userId: 2 })))
  await aaa
  assert.equal(state.weekView.value.user_id, 3)
  assert.equal(state.selectedStudentId.value, 3)
  await state.selectStudent(2)
  assert.equal(state.weekView.value.user_id, 2)
})

test('a failed user switch hides the previous student data and exposes the business error with retry', async () => {
  const { state, html } = fixture(async (week, { userId }) => userId === 3
    ? { code: 404, message: '学生不存在' }
    : response(weekData(week ?? 5, { userId: 2 })), { user: admin })
  await state.selectStudent(2)
  await state.selectStudent(3)
  assert.equal(state.weekView.value, null)
  const page = await html()
  assert.match(page, /学生不存在/)
  assert.match(page, /重新加载/)
  assert.doesNotMatch(page, /class="course-card/)
  assert.match(page, /正在查看 学生 ID 3 的课表/)
})

test('administrator student options paginate, filter roles, and retry list failures without hiding the grid', async () => {
  let fail = true
  const pages = []
  const { state, html } = fixture(async (week) => response(weekData(week ?? 5, { userId: 1 })), {
    user: admin,
    usersApi: async ({ page }) => {
      if (fail) return { code: 400, message: '学生列表暂时无法读取' }
      pages.push(page)
      return { code: 200, data: { total: 201, list: page === 1
        ? [admin, { id: 2, username: 'aaa', name: '学生甲', role: 'student' }]
        : [{ id: 3, username: 'bbb', name: '学生乙', role: 'student' }] } }
    }
  })
  await state.loadWeek(5)
  await state.loadStudents()
  assert.match(await html(), /学生列表暂时无法读取/)
  assert.equal((await html()).match(/class="schedule-cell/g).length, 28)
  fail = false
  await state.loadStudents()
  assert.equal(state.studentsError.value, '')
  assert.deepEqual(pages, [1, 2])
  assert.deepEqual(state.studentOptions.value.map((user) => user.id), [2, 3])
  assert.match(await html(), /学生甲（aaa）/)
  assert.match(await html(), /学生乙（bbb）/)
})

test('the transport includes explicit administrator selection and omits user_id for self/student requests', async () => {
  const apiSource = readFileSync(new URL('../src/api/schedule.js', import.meta.url), 'utf8')
    .replace(/^import request from [^\n]+\n/m, '').replace(/export function/g, 'function')
  const calls = []
  const getWeek = new Function('request', `${apiSource}\nreturn getWeekScheduleApi`)(
    (options) => { calls.push(options); return options })
  const controller = new AbortController()
  getWeek(6, { userId: 2, signal: controller.signal })
  getWeek(6)
  getWeek()
  assert.deepEqual(calls.map((call) => call.params), [{ week: 6, user_id: 2 }, { week: 6 }, {}])
  assert.equal(calls[0].signal, controller.signal)
})

test('buildCourseTimeline 把起止周换算成百分比横条', () => {
  const data = weekData(6)
  data.total_weeks = 20
  data.days.forEach((day) => { day.courses = [] })
  data.days[0].courses = [
    { course_id: 2, course_name: '数据结构', teacher: '林雨桐', period: '5-6节', location: '实验楼D501', start_week: 1, end_week: 20 },
    { course_id: 63, course_name: '计算机图形学', teacher: '马振宇', period: '1-2节', location: '实验楼D501', start_week: 3, end_week: 8 },
    { course_id: 67, course_name: '概率论与数理统计', teacher: '郑子涵', period: '1-2节', location: '教学楼B305', start_week: 1, end_week: 8 }
  ]
  const rows = buildCourseTimeline(data, 6)
  assert.equal(rows.length, 3, '同一课程只保留一条，且按起始周排序')
  const byName = Object.fromEntries(rows.map((row) => [row.name, row]))
  assert.deepEqual(rows.map((row) => row.name), ['概率论与数理统计', '数据结构', '计算机图形学'],
    '同起始周时按结束周升序，再按起始周升序')

  const full = byName['数据结构']
  assert.equal(full.leftPercent, 0)
  assert.equal(full.widthPercent, 100, '1-20 周应铺满整条时间轴')
  assert.equal(full.remaining, 14)
  assert.equal(full.ended, false)

  const graphics = byName['计算机图形学']
  assert.equal(graphics.startWeek, 3)
  assert.equal(graphics.endWeek, 8)
  assert.equal(graphics.leftPercent, 10, '(3-1)/20')
  assert.equal(graphics.widthPercent, 30, '(8-3+1)/20')
  assert.equal(graphics.remaining, 2)
  assert.equal(graphics.currentPercent, 27.5, '第6周游标位于 (6-0.5)/20')
})

test('buildCourseTimeline 标记已结课并去除重复课程', () => {
  const data = weekData(12)
  data.total_weeks = 20
  data.days.forEach((day) => { day.courses = [] })
  data.days[2].courses = [
    { course_id: 63, course_name: '计算机图形学', teacher: '马振宇', period: '1-2节', start_week: 3, end_week: 8 }
  ]
  data.days[3].courses = [
    { course_id: 63, course_name: '计算机图形学', teacher: '马振宇', period: '1-2节', start_week: 3, end_week: 8 }
  ]
  const rows = buildCourseTimeline(data, 12)
  assert.equal(rows.length, 1, '同一 course_id 在多天出现只保留一条')
  assert.equal(rows[0].ended, true)
  assert.equal(rows[0].remaining, 0, '已结课时剩余周数不为负')
})

test('buildCourseTimeline 对缺失或异常字段保持安全', () => {
  assert.deepEqual(buildCourseTimeline(null, 5), [])
  assert.deepEqual(buildCourseTimeline({ total_weeks: 20, days: [] }, 5), [])
  assert.deepEqual(buildCourseTimeline({ days: [] }, 5), [], '缺少 total_weeks 时返回空')
  assert.deepEqual(buildCourseTimeline({ total_weeks: '20', days: [] }, 5), [])

  const data = { total_weeks: 20, week: 5, days: [{ courses: [
    { course_id: 1, course_name: '缺周次' },
    { course_id: 2, course_name: '起止颠倒', start_week: 9, end_week: 3 },
    { course_id: 3, course_name: '超范围', start_week: 1, end_week: 99 }
  ] }] }
  const rows = buildCourseTimeline(data, 5)
  assert.equal(rows.length, 1, '无效周次应被丢弃')
  assert.equal(rows[0].name, '超范围')
  assert.equal(rows[0].widthPercent, 100, '超出总周数时截断到总周数')
})
