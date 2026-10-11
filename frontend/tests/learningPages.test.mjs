import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'
import * as Vue from 'vue'
import * as Icons from '@element-plus/icons-vue'
import * as helpers from '../src/utils/learningItems.js'

function evaluate(code, modules) {
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

function compilePage(filename) {
  const source = readFileSync(new URL(`../src/views/${filename}`, import.meta.url), 'utf8')
  const { descriptor } = parse(source, { filename })
  return {
    source,
    script: compileScript(descriptor, { id: filename }).content,
    template: compileTemplate({ source: descriptor.template.content, filename, id: filename }).code
  }
}

const home = compilePage('Home.vue')
const courseDetail = compilePage('CourseDetail.vue')
const now = () => new Date(2026, 9, 11, 12, 0, 0)
const item = (extras = {}) => ({ id: 101, user_id: 2, course_id: 2, course_name: '数据结构',
  title: '实验报告二', type: 'assignment', due_at: '2026-10-12T18:00:00', status: 'pending', ...extras })
const week = () => ({ week: 6, total_weeks: 20, start_date: '2026-10-12', end_date: '2026-10-18',
  days: Array.from({ length: 7 }, (_, index) => ({ date: `2026-10-${12 + index}`, weekday: index + 1,
    is_today: index === 2, is_class_day: index < 5, holiday_name: null,
    courses: index === 2 ? [{ course_id: 2, course_name: '数据结构', period: '7-8节',
      teacher: '张老师', location: '实验楼301', start_week: 3, end_week: 8 }]
      : index === 3 ? [{ course_id: 50, course_name: '计算机网络', period: '3-4节', start_week: 1, end_week: 20 }]
      : [] })) })
const detail = () => ({ course: { id: 2, name: '数据结构', code: 'DS101', description: '学习数据结构及算法。' },
  start_week: 3, end_week: 8, semester: { current_week: 6, total_weeks: 20 },
  schedules: [{ id: 901, course_id: 2, class_date: '2026-10-14', weekday: 3, period: '7-8节',
    teacher: '张老师', location: '实验楼301', start_week: 3, end_week: 8 }] })
const fragment = () => ({ document_id: 17, chunk_index: 1, source: '数据结构实验教学规定.md',
  text: '实验课前必须完成预习报告。\n报告在实验后第3天提交。' })

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

async function render(page, state) {
  const render = evaluate(page.template, { vue: Vue })
  const app = Vue.createSSRApp({ setup: () => ({ ...state }), render })
  const simple = { setup: (_, { slots }) => () => Vue.h('div', slots.default?.()) }
  for (const name of ['ElTag', 'ElIcon', 'ElCollapse']) app.component(name, simple)
  app.component('ElCard', { setup: (_, { slots }) => () => Vue.h('section', [slots.header?.(), slots.default?.()]) })
  app.component('ElButton', { props: ['disabled', 'loading'], setup: (props, { slots }) => () =>
    Vue.h('button', { disabled: props.disabled, 'data-loading': props.loading }, slots.default?.()) })
  app.component('ElLink', { setup: (_, { slots }) => () => Vue.h('a', slots.default?.()) })
  app.component('RouterLink', { props: ['to'], setup: (props, { slots }) => () =>
    Vue.h('a', { href: typeof props.to === 'string' ? props.to : props.to?.path }, slots.default?.()) })
  app.component('ElProgress', { props: ['percentage'], setup: (props) => () =>
    Vue.h('progress', { value: props.percentage, max: 100 }) })
  app.component('ElSkeleton', { setup: () => () => Vue.h('div', { class: 'loading-placeholder' }, '加载中') })
  app.component('ElEmpty', { props: ['description'], setup: (props, { slots }) => () =>
    Vue.h('div', { class: 'empty-state' }, [props.description, slots.default?.()]) })
  app.component('ElAlert', { props: ['title'], setup: (props, { slots }) => () =>
    Vue.h('div', { role: 'alert' }, [props.title, slots.title?.(), slots.default?.()]) })
  app.component('ElCollapseItem', { setup: (_, { slots }) => () =>
    Vue.h('article', [slots.title?.(), slots.default?.()]) })
  app.component('ElTable', { props: ['data', 'emptyText'], setup: (props, { slots }) => {
    Vue.provide('learning-page-rows', Vue.computed(() => props.data || []))
    return () => Vue.h('div', [slots.default?.(), props.data?.length ? null : props.emptyText])
  } })
  app.component('ElTableColumn', { props: ['prop', 'label'], setup: (props, { slots }) => {
    const rows = Vue.inject('learning-page-rows')
    return () => Vue.h('div', [props.label, ...rows.value.map(row =>
      Vue.h('span', slots.default?.({ row }) || String(row[props.prop] ?? '')))])
  } })
  return renderToString(app)
}

function homeFixture({ role = 'student', rows = [], overrides = {}, inboxError = '' } = {}) {
  const calls = [], navigation = []
  let mount
  const userInfo = Vue.ref({ id: role === 'admin' ? 1 : 2, role, name: role === 'admin' ? '管理员' : 'aaa' })
  const inbox = { assignments: Vue.ref(rows), loading: Vue.ref(false), error: Vue.ref(inboxError),
    refresh: async (force) => {
      calls.push({ name: 'refreshInbox', args: [force] })
      return overrides.refreshInbox?.(inbox, force)
    } }
  const defaults = {
    getWeekScheduleApi: async () => ({ code: 200, data: week() }),
    listStudyPlans: async () => ({ code: 200, data: { list: [{ id: 90,
      content: '复习数据结构并完成实验报告', create_time: '2026-10-10T18:00:00' }], total: 1 } }),
    listCoursesApi: async () => ({ code: 200, data: [{ id: 2 }, { id: 3 }] }),
    getUserPageList: async () => ({ code: 200, data: { list: [], total: 3 } }),
    listDocumentsApi: async () => ({ code: 200, data: { list: [{ id: 17, chunk_count: 7 }, { id: 18, chunk_count: 7 }], total: 2 } }),
    getLearningContextApi: async () => ({ code: 200, data: { holidays: [], makeup_workdays: [] } })
  }
  const api = Object.fromEntries(Object.keys(defaults).map(name => [name, async (...args) => {
    calls.push({ name, args }); return (overrides[name] || defaults[name])(...args)
  }]))
  const component = evaluate(home.script, {
    vue: { ...Vue, onMounted: handler => { mount = handler } },
    'vue-router': { useRouter: () => ({ push: value => navigation.push(value) }) },
    '@element-plus/icons-vue': Icons,
    '@/utils/user': { useUser: () => ({ userInfo }) },
    '@/api/schedule': api, '@/api/studyPlan': api, '@/api/course': api,
    '@/api/user': api, '@/api/document': api, '@/api/learningContext': api,
    '@/stores/learningInbox': { useLearningInbox: () => inbox },
    '@/utils/learningItems': helpers
  })
  const state = component.setup({}, { expose() {} })
  return { state, calls, navigation, inbox, userInfo, mount: async () => {
    await mount(); state.reminderNow.value = now()
  }, html: () => render(home, state) }
}

function courseFixture({ role = 'student', id = '2', overrides = {}, rows = [item()] } = {}) {
  const calls = [], navigation = []
  let routeWatcher
  const route = Vue.reactive({ params: { id } })
  const userInfo = Vue.ref({ id: role === 'admin' ? 1 : 2, role })
  const defaults = {
    getCourseDetailApi: async () => ({ code: 200, data: detail() }),
    getCourseDocsApi: async () => ({ code: 200, data: { items: [fragment()] } }),
    listAssignmentsApi: async () => ({ code: 200, data: { list: rows, total: rows.length } })
  }
  const api = Object.fromEntries(Object.keys(defaults).map(name => [name, async (...args) => {
    calls.push({ name, args }); return (overrides[name] || defaults[name])(...args)
  }]))
  const component = evaluate(courseDetail.script, {
    vue: { ...Vue, watch: (_source, handler) => { routeWatcher = handler } },
    'vue-router': { useRoute: () => route, useRouter: () => ({ push: value => navigation.push(value), back() {} }) },
    '@/utils/user': { useUser: () => ({ userInfo }) },
    '@/api/learningContext': api, '@/api/assignment': api, '@/utils/learningItems': helpers
  })
  const state = component.setup({}, { expose() {} })
  return { state, calls, navigation, route, userInfo, mount: async () => {
    await routeWatcher(); await Vue.nextTick()
  }, html: () => render(courseDetail, state) }
}

test('student dashboard preserves today/tomorrow, ending courses, semester progress, text plan and shortcuts beside deadlines', async () => {
  const f = homeFixture({ rows: [item()] })
  await f.mount()
  const html = await f.html()
  for (const text of ['今日课程', '张老师', '实验楼301', '明天', '有 1 门课', '即将结束的课程',
    '还剩 2 周', '第 6 周', '共 20 周', '最近的学习计划', '复习数据结构并完成实验报告',
    '问课程助手', '看周课表', '写学习计划', '作业与考试', '近期截止']) assert.ok(html.includes(text), text)
  assert.equal(f.state.semester.value.percent, 30)
  assert.deepEqual(f.calls.find(call => call.name === 'listStudyPlans').args, [{ page: 1, page_size: 1 }])
  assert.deepEqual(f.calls.find(call => call.name === 'refreshInbox').args, [true])
})

test('dashboard deadlines are pending, within three days, ascending by due date and separate from overdue reminders', async () => {
  const f = homeFixture({ rows: [
    item({ id: 103, title: '第三天考试', type: 'exam', due_at: '2026-10-14T10:00:00' }),
    item({ id: 104, title: '已经完成', status: 'done' }),
    item({ id: 102, title: '明天作业', due_at: '2026-10-12T18:00:00' }),
    item({ id: 101, title: '今日作业', due_at: '2026-10-11T18:00:00' }),
    item({ id: 105, title: '逾期实验', due_at: '2026-10-10T18:00:00' }),
    item({ id: 106, title: '下周才交', due_at: '2026-10-20T18:00:00' })
  ] })
  await f.mount()
  assert.deepEqual(f.state.upcomingItems.value.map(row => row.id), [101, 102, 103])
  assert.deepEqual(f.state.overdueItems.value.map(row => row.id), [105])
  const html = await f.html()
  assert.ok(html.indexOf('今日作业') < html.indexOf('明天作业'))
  assert.ok(html.indexOf('明天作业') < html.indexOf('第三天考试'))
  assert.match(html, /今天到期/)
  assert.match(html, /明天到期/)
  assert.match(html, /剩余 3 天/)
  assert.match(html, /1 项作业或考试已逾期未完成/)
  assert.match(html, /进入系统时提醒/)
  assert.match(html, /不提供后台推送/)
  assert.doesNotMatch(html, /已经完成|下周才交/)
  assert.match(html, /href="\/manager\/course\/2"/)
})

test('dashboard renders server-provided holiday and ordinary weekend notices through different branches', async () => {
  const f = homeFixture({ rows: [item({ id: 101, due_at: '2026-10-11T18:00:00' }),
    item({ id: 102, due_at: '2026-10-12T18:00:00' })], overrides: {
    getLearningContextApi: async () => ({ code: 200, data: { holidays: [{ name: '测试配置假期',
      start_date: '2026-10-12', end_date: '2026-10-13' }], makeup_workdays: [] } })
  } })
  await f.mount()
  const html = await f.html()
  assert.match(html, /周末不放假，截止日照常/)
  assert.match(html, /该截止日在放假期间（测试配置假期 10\/12-10\/13）/)
})

test('dashboard empty deadlines remain a positive state; failures show a retry rather than an empty success', async () => {
  const empty = homeFixture()
  await empty.mount()
  assert.match(await empty.html(), /近 3 天没有截止事项/)
  const failure = homeFixture({ inboxError: '截止事项加载失败' })
  await failure.mount()
  const failedHtml = await failure.html()
  assert.match(failedHtml, /截止事项加载失败/)
  assert.match(failedHtml, /重试/)
  assert.doesNotMatch(failedHtml, /近 3 天没有截止事项/)
  assert.match(failedHtml, /今日课程|最近的学习计划/)
})

test('calendar failure never invents weekend or holiday labels and leaves actual deadline data available', async () => {
  const f = homeFixture({ rows: [item()], overrides: {
    getLearningContextApi: async () => ({ code: 503, message: '假期配置服务不可用' })
  } })
  await f.mount()
  assert.equal(f.state.calendarError.value, true)
  const html = await f.html()
  assert.match(html, /实验报告二/)
  assert.match(html, /假期信息暂时不可用，未判断截止日是否放假/)
  assert.doesNotMatch(html, /该截止日在放假期间|周末不放假/)
})

test('administrator dashboard keeps counts and knowledge-base warning without requesting or displaying student reminders', async () => {
  const f = homeFixture({ role: 'admin', rows: [item(), item({ id: 102, due_at: '2026-10-10T18:00:00' })],
    overrides: { listDocumentsApi: async () => ({ code: 200, data: { list: [], total: 0 } }) } })
  await f.mount()
  const html = await f.html()
  assert.match(html, /数据概览/)
  assert.match(html, /用户|知识库文档|向量分块/)
  assert.match(html, /知识库还没有文档，助手无法回答资料类问题/)
  assert.equal(f.state.userTotal.value, 3)
  assert.equal(f.state.courseTotal.value, 2)
  assert.doesNotMatch(html, /近期截止|进入系统时提醒|实验报告二|最近的学习计划|写学习计划/)
  assert.equal(f.calls.some(call => ['refreshInbox', 'getLearningContextApi', 'listStudyPlans'].includes(call.name)), false)
  await f.state.loadDeadlines()
  assert.equal(f.calls.some(call => call.name === 'refreshInbox'), false)
})

test('the original Sunday dashboard still loads next Monday from the following week', async () => {
  const current = week()
  current.days.forEach(day => { day.is_today = day.weekday === 7 })
  const nextMonday = { date: '2026-10-19', weekday: 1, courses: [{ course_name: '操作系统' }] }
  const f = homeFixture({ overrides: { getWeekScheduleApi: async requested =>
    ({ code: 200, data: requested === 7 ? { days: [nextMonday] } : current }) } })
  await f.mount()
  assert.deepEqual(f.calls.filter(call => call.name === 'getWeekScheduleApi').map(call => call.args), [[], [7]])
  assert.equal(f.state.tomorrow.value.date, '2026-10-19')
  assert.match(await f.html(), /10月19日 周一 有 1 门课/)
})

test('dashboard schedule network failure renders retry and never claims there are no classes', async () => {
  const f = homeFixture({ overrides: { getWeekScheduleApi: async () => { throw new Error('连接超时') } } })
  await f.mount()
  const html = await f.html()
  assert.match(html, /课表加载失败，请重试：连接超时/)
  assert.match(html, /重新加载课表/)
  assert.match(html, /暂时无法判断结课信息/)
  assert.doesNotMatch(html, /今天没有课，好好休息|本学期暂时没有结课信息|正在读取学期信息/)
  assert.match(html, /复习数据结构并完成实验报告/)
})

test('dashboard schedule business failure is visible and retry restores actual courses', async () => {
  let fail = true
  const f = homeFixture({ overrides: { getWeekScheduleApi: async () => fail
    ? { code: 503, message: '课表服务暂不可用', data: null } : { code: 200, data: week() } } })
  await f.mount()
  assert.match(await f.html(), /课表服务暂不可用/)
  assert.doesNotMatch(await f.html(), /今天没有课，好好休息/)
  fail = false
  await f.state.loadWeek()
  assert.equal(f.state.weekError.value, '')
  assert.match(await f.html(), /数据结构|张老师|实验楼301/)
  assert.doesNotMatch(await f.html(), /课表服务暂不可用|重新加载课表/)
})

test('dashboard genuinely empty successful schedule still displays a positive no-class state', async () => {
  const empty = week()
  empty.days.forEach(day => { day.courses = [] })
  const f = homeFixture({ overrides: { getWeekScheduleApi: async () => ({ code: 200, data: empty }) } })
  await f.mount()
  assert.equal(f.state.weekError.value, '')
  assert.match(await f.html(), /今天没有课，好好休息/)
  assert.doesNotMatch(await f.html(), /课表加载失败|暂时读不到今天的课表/)
})

test('dashboard outside the semester does not describe the fallback week as the current week', async () => {
  const outside = { ...week(), week: 20, current_week: null }
  outside.days.forEach(day => { day.is_today = false })
  const f = homeFixture({ overrides: { getWeekScheduleApi: async () => ({ code: 200, data: outside }) } })
  await f.mount()
  assert.equal(f.state.semester.value, null)
  assert.match(await f.html(), /当前不在学期内，请到周课表查看学期安排/)
  assert.doesNotMatch(await f.html(), /第 20 周|今天没有课，好好休息|正在读取学期信息/)
})

test('dashboard final-week Sunday never requests an out-of-range week 21', async () => {
  const last = { ...week(), week: 20, total_weeks: 20 }
  last.days.forEach(day => { day.is_today = day.weekday === 7 })
  const f = homeFixture({ overrides: { getWeekScheduleApi: async () => ({ code: 200, data: last }) } })
  await f.mount()
  assert.deepEqual(f.calls.filter(call => call.name === 'getWeekScheduleApi').map(call => call.args), [[]])
  assert.equal(f.state.nextWeekError.value, '')
  assert.equal(f.state.tomorrow.value, null)
})

test('dashboard failed next-week preview keeps today valid and reports only the unavailable preview', async () => {
  const sunday = week()
  sunday.days.forEach(day => { day.is_today = day.weekday === 7 })
  sunday.days[6].courses = [{ course_name: '调休实验', period: '1-2节' }]
  const f = homeFixture({ overrides: { getWeekScheduleApi: async requested => requested === 7
    ? { code: 503, message: '下周暂不可用' } : { code: 200, data: sunday } } })
  await f.mount()
  assert.equal(f.state.weekError.value, '')
  assert.match(await f.html(), /调休实验|明日课表加载失败，请重试：下周暂不可用|重试明日预告/)
  assert.doesNotMatch(await f.html(), /今天没有课，好好休息/)
})

test('dashboard text-plan network and business failures are never described as no plan', async () => {
  for (const response of [async () => { throw new Error('请求超时') }, async () => ({ code: 503, message: '计划查询失败' })]) {
    const f = homeFixture({ overrides: { listStudyPlans: response } })
    await f.mount()
    assert.equal(f.state.planTotal.value, null)
    const html = await f.html()
    assert.match(html, /学习计划加载失败，请重试|重新加载学习计划/)
    assert.doesNotMatch(html, /还没有学习计划，去写一份吧/)
    assert.match(html, /数据结构/)
  }
})

test('dashboard text-plan retry distinguishes successful empty and restores returned plans', async () => {
  let response = { code: 503, message: '计划查询失败' }
  const f = homeFixture({ overrides: { listStudyPlans: async () => response } })
  await f.mount()
  response = { code: 200, data: { list: [], total: 0 } }
  await f.state.loadPlans()
  assert.equal(f.state.planError.value, '')
  assert.match(await f.html(), /还没有学习计划，去写一份吧/)
  response = { code: 200, data: { list: [{ content: '重试后读到的计划', create_time: '2026-10-11T12:00:00' }], total: 1 } }
  await f.state.loadPlans()
  assert.equal(f.state.planTotal.value, 1)
  assert.match(await f.html(), /重试后读到的计划/)
  assert.doesNotMatch(await f.html(), /还没有学习计划，去写一份吧/)
})

test('administrator failed knowledge-base statistics show unavailable rather than an empty database', async () => {
  for (const response of [async () => { throw new Error('连接超时') }, async () => ({ code: 503, message: '文档查询失败' })]) {
    const f = homeFixture({ role: 'admin', overrides: { listDocumentsApi: response } })
    await f.mount()
    assert.equal(f.state.docCount.value, null)
    assert.equal(f.state.chunkTotal.value, null)
    assert.equal(f.state.kbWarning.value, '')
    const html = await f.html()
    assert.match(html, /知识库统计加载失败|重新加载统计|—/)
    assert.doesNotMatch(html, /知识库还没有文档/)
    assert.equal(f.state.courseTotal.value, 2)
    assert.equal(f.state.userTotal.value, 3)
  }
})

test('administrator statistic retry recovers failed counts and only warns for confirmed empty knowledge base', async () => {
  let fail = true
  const f = homeFixture({ role: 'admin', overrides: {
    listCoursesApi: async () => fail ? { code: 503, message: '课程统计失败' } : { code: 200, data: [{ id: 2 }] },
    getUserPageList: async () => fail ? { code: 503, message: '用户统计失败' } : { code: 200, data: { list: [], total: 3 } },
    listDocumentsApi: async () => fail ? { code: 503, message: '文档统计失败' } : { code: 200, data: { list: [], total: 0 } }
  } })
  await f.mount()
  assert.equal(f.state.statsErrors.value.length, 3)
  assert.equal(f.state.courseTotal.value, null)
  assert.equal(f.state.userTotal.value, null)
  assert.equal(f.state.kbWarning.value, '')
  fail = false
  await f.state.loadStats()
  assert.deepEqual(f.state.statsErrors.value, [])
  assert.equal(f.state.courseTotal.value, 1)
  assert.equal(f.state.userTotal.value, 3)
  assert.equal(f.state.docCount.value, 0)
  assert.equal(f.state.chunkTotal.value, 0)
  assert.match(await f.html(), /知识库还没有文档/)
  assert.doesNotMatch(await f.html(), /统计加载失败|重新加载统计/)
})

test('administrator document statistics count every page instead of undercounting beyond the first 100 rows', async () => {
  const first = Array.from({ length: 100 }, (_, index) => ({ id: index + 1, chunk_count: 2 }))
  const f = homeFixture({ role: 'admin', overrides: { listDocumentsApi: async ({ page }) => ({ code: 200,
    data: { list: page === 1 ? first : [{ id: 101, chunk_count: 7 }], total: 101 } }) } })
  await f.mount()
  assert.equal(f.state.docCount.value, 101)
  assert.equal(f.state.chunkTotal.value, 207)
  assert.deepEqual(f.calls.filter(call => call.name === 'listDocumentsApi').map(call => call.args[0].page), [1, 2])
})

test('student course details call actual APIs by course id only and render all four sections with cited original text', async () => {
  const f = courseFixture()
  await f.mount()
  assert.deepEqual(f.calls.find(call => call.name === 'getCourseDetailApi').args, [2])
  assert.deepEqual(f.calls.find(call => call.name === 'getCourseDocsApi').args, [2])
  assert.deepEqual(f.calls.find(call => call.name === 'listAssignmentsApi').args,
    [{ course_id: 2, page: 1, page_size: 100 }])
  const html = await f.html()
  for (const text of ['课程信息', 'DS101', '学习数据结构及算法', '当前学期第 6 周', '第 3–8 周上课',
    '上课安排', '2026-10-14', '周三', '7-8节', '张老师', '实验楼301',
    '课程资料', '数据结构实验教学规定.md', '实验课前必须完成预习报告',
    '作业与考试', '未完成 1 项', '实验报告二']) assert.ok(html.includes(text), text)
  assert.equal(f.state.progress.value, 67)
  assert.equal(f.calls.some(call => call.args.some(arg => arg?.user_id)), false)
})

test('administrator course details never request student assignments and explicitly identify their own schedule perspective', async () => {
  const f = courseFixture({ role: 'admin' })
  await f.mount()
  assert.equal(f.calls.some(call => call.name === 'listAssignmentsApi'), false)
  assert.equal(f.calls.filter(call => call.name === 'getCourseDocsApi').length, 1)
  const html = await f.html()
  assert.match(html, /管理员视角不展示学生作业/)
  assert.match(html, /管理员本账号安排，未展示学生的私人课表/)
  assert.doesNotMatch(html, /实验报告二|未完成 1 项|管理这门课的截止事项/)
})

test('course retrieval empty success and explicit retrieval failure remain different visible states', async () => {
  const empty = courseFixture({ overrides: { getCourseDocsApi: async () => ({ code: 200, data: { items: [] } }) } })
  await empty.mount()
  assert.match(await empty.html(), /知识库中暂无该课程资料/)
  assert.equal(empty.state.docsError.value, '')
  const failure = courseFixture({ overrides: { getCourseDocsApi: async () => ({ code: 503,
    message: '当前无法查询资料（检索失败），请配置 EMBEDDING_API_KEY' }) } })
  await failure.mount()
  const html = await failure.html()
  assert.match(html, /当前无法查询资料（检索失败）/)
  assert.doesNotMatch(html, /知识库中暂无该课程资料/)
  assert.match(html, /课程信息|实验报告二/)
})

test('course source snippets render escaped plain text and retain filenames when text contains HTML', async () => {
  const f = courseFixture({ overrides: { getCourseDocsApi: async () => ({ code: 200,
    data: { items: [{ ...fragment(), text: '<script>alert("unsafe")</script>\n必须预习' }] } }) } })
  await f.mount()
  const html = await f.html()
  assert.match(html, /数据结构实验教学规定\.md/)
  assert.match(html, /&lt;script&gt;alert\(&quot;unsafe&quot;\)&lt;\/script&gt;/)
  assert.doesNotMatch(html, /<script>/)
})

test('course assignments load every page with course_id and show pending count independently of done rows', async () => {
  const first = Array.from({ length: 100 }, (_, index) => item({ id: index + 1, status: index === 0 ? 'done' : 'pending' }))
  const f = courseFixture({ overrides: { listAssignmentsApi: async ({ page }) => ({ code: 200,
    data: { list: page === 1 ? first : [item({ id: 101 })], total: 101 } }) } })
  await f.mount()
  assert.equal(f.state.assignments.value.length, 101)
  assert.deepEqual(f.calls.filter(call => call.name === 'listAssignmentsApi').map(call => call.args[0]), [
    { course_id: 2, page: 1, page_size: 100 }, { course_id: 2, page: 2, page_size: 100 }
  ])
  assert.match(await f.html(), /未完成 100 项/)
})

test('course and assignment business failures show their reasons without claiming data is empty', async () => {
  const missing = courseFixture({ overrides: { getCourseDetailApi: async () => ({ code: 404, message: '课程不存在' }) } })
  await missing.mount()
  assert.deepEqual(missing.calls.map(call => call.name), ['getCourseDetailApi'])
  assert.match(await missing.html(), /课程不存在/)
  assert.equal(missing.state.loading.value, false)
  const failure = courseFixture({ overrides: { listAssignmentsApi: async () => ({ code: 503, message: '作业查询暂不可用' }) } })
  await failure.mount()
  const html = await failure.html()
  assert.match(html, /作业查询暂不可用/)
  assert.doesNotMatch(html, /这门课还没有作业或考试/)
  assert.match(html, /数据结构实验教学规定/)
})

test('a delayed old course request cannot replace the new route detail or trigger obsolete assignment calls', async () => {
  const gate = deferred()
  const f = courseFixture({ overrides: { getCourseDetailApi: async id => id === 2 ? gate.promise :
    { code: 200, data: { ...detail(), course: { id: 3, name: '计算机网络', code: 'NET101' } } } } })
  const first = f.state.load()
  f.route.params.id = '3'
  await f.state.load()
  gate.resolve({ code: 200, data: detail() })
  await first
  assert.equal(f.state.detail.value.course.name, '计算机网络')
  assert.deepEqual(f.calls.filter(call => call.name === 'listAssignmentsApi').map(call => call.args[0].course_id), [3])
  assert.equal(f.state.loading.value, false)
})

test('invalid course ids are rejected locally before any detail, retrieval or assignment request', async () => {
  for (const id of ['0', '-1', 'bad', '1.5', '9007199254740992']) {
    const f = courseFixture({ id })
    await f.mount()
    assert.deepEqual(f.calls, [])
    assert.match(await f.html(), /课程不存在/)
  }
})

test('switching a loading course to an invalid id clears skeleton flags and exposes the error immediately', async () => {
  const gate = deferred()
  const f = courseFixture({ overrides: { getCourseDetailApi: () => gate.promise } })
  const first = f.state.load()
  assert.equal(f.state.loading.value, true)
  f.state.docsLoading.value = true
  f.state.assignmentsLoading.value = true
  f.route.params.id = 'invalid'
  await f.state.load()
  assert.equal(f.state.loading.value, false)
  assert.equal(f.state.docsLoading.value, false)
  assert.equal(f.state.assignmentsLoading.value, false)
  assert.equal(f.state.error.value, '课程不存在')
  const html = await f.html()
  assert.match(html, /课程不存在/)
  assert.doesNotMatch(html, /loading-placeholder/)
  gate.resolve({ code: 200, data: detail() })
  await first
  assert.equal(f.state.detail.value, null)
  assert.equal(f.state.loading.value, false)
  assert.deepEqual(f.calls.map(call => call.name), ['getCourseDetailApi'])
})
