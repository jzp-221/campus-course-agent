import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'
import * as Vue from 'vue'
import * as helpers from '../src/utils/learningItems.js'

const source = readFileSync(new URL('../src/components/PlanTaskPanel.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source, { filename: 'PlanTaskPanel.vue' })
const script = compileScript(descriptor, { id: 'plan-task-test' }).content
const template = compileTemplate({ source: descriptor.template.content,
  filename: 'PlanTaskPanel.vue', id: 'plan-task-test' }).code

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

const row = (extras = {}) => ({ id: 901, title: '完成数据结构实验报告二', course_id: 2,
  course_name: '数据结构', due_at: '2099-10-12T18:00:00', status: 'pending', defer_count: 0, ...extras })
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function fixture(overrides = {}, { records = [row()], confirm = async () => true } = {}) {
  const calls = [], confirmations = [], successes = []
  let mount, unmount
  const defaults = {
    listPlanTasksApi: async (params) => {
      const filtered = records.filter((record) => !params.status || record.status === params.status)
      const begin = (params.page - 1) * params.page_size
      return { code: 200, data: { list: filtered.slice(begin, begin + params.page_size), total: filtered.length } }
    },
    createPlanTaskApi: async () => ({ code: 200, data: row() }),
    updatePlanTaskApi: async () => ({ code: 200, data: row() }),
    deferPlanTaskApi: async () => ({ code: 200, data: row({ defer_count: 1 }) }),
    deletePlanTaskApi: async () => ({ code: 200, data: null })
  }
  const api = Object.fromEntries(Object.keys(defaults).map((name) => [name, async (...args) => {
    calls.push({ name, args })
    return (overrides[name] || defaults[name])(...args)
  }]))
  const component = evaluate(script, {
    vue: { ...Vue, onMounted: (handler) => { mount = handler }, onBeforeUnmount: (handler) => { unmount = handler } },
    'element-plus': {
      ElMessage: { success: (message) => successes.push(message) },
      ElMessageBox: { confirm: async (...args) => { confirmations.push(args); return confirm(...args) } }
    },
    '@/api/planTask': api,
    '@/api/learningContext': { getLearningContextApi: async () => ({ code: 200,
      data: { courses: [{ id: 2, name: '数据结构' }] } }) },
    '@/utils/learningItems': helpers
  })
  const state = component.setup({}, { expose() {} })
  const render = evaluate(template, { vue: Vue })
  async function html() {
    const app = Vue.createSSRApp({ setup: () => ({ ...state }), render })
    const simple = { setup: (_, { slots }) => () => Vue.h('div', slots.default?.()) }
    for (const name of ['ElForm', 'ElFormItem', 'ElSelect', 'ElOption', 'ElRadioGroup', 'ElRadio', 'ElPagination', 'ElTag']) {
      app.component(name, simple)
    }
    app.component('ElCard', { setup: (_, { slots }) => () => Vue.h('section', [slots.header?.(), slots.default?.()]) })
    app.component('ElButton', { props: ['disabled', 'loading'], setup: (props, { slots }) => () =>
      Vue.h('button', { disabled: props.disabled, 'data-loading': props.loading }, slots.default?.()) })
    app.component('ElCheckbox', { props: ['disabled', 'modelValue'], setup: (props, { attrs }) => () =>
      Vue.h('input', { type: 'checkbox', disabled: props.disabled, checked: props.modelValue, ...attrs }) })
    app.component('ElAlert', { props: ['title'], setup: (props) => () => Vue.h('div', { role: 'alert' }, props.title) })
    app.component('ElEmpty', { props: ['description'], setup: (props) => () => Vue.h('div', props.description) })
    app.component('ElInput', { props: ['modelValue'], setup: (props) => () => Vue.h('input', { value: props.modelValue }) })
    app.component('ElInputNumber', { props: ['modelValue'], setup: (props) => () => Vue.h('input', { type: 'number', value: props.modelValue }) })
    app.component('ElDatePicker', simple)
    app.component('ElDialog', { props: ['modelValue'], setup: (props, { slots }) => () =>
      props.modelValue ? Vue.h('dialog', [slots.default?.(), slots.footer?.()]) : null })
    app.directive('loading', { getSSRProps: () => ({}) })
    return renderToString(app)
  }
  return { state, calls, confirmations, successes, html, mount: () => mount(), unmount: () => unmount() }
}

function transport(request) {
  const text = readFileSync(new URL('../src/api/planTask.js', import.meta.url), 'utf8')
    .replace(/^import request from [^\n]+\n/m, '').replace(/export function/g, 'function')
  return new Function('request', `${text}\nreturn {
    listPlanTasksApi, createPlanTaskApi, updatePlanTaskApi, deferPlanTaskApi, deletePlanTaskApi
  }`)(request)
}

test('plan-task transports preserve all five endpoints, defaults, optional nulls and requested defer days', async () => {
  const calls = []
  const api = transport(async (options) => { calls.push(options); return { code: 200 } })
  await api.listPlanTasksApi({ page: 2, page_size: 10, status: 'pending' })
  await api.createPlanTaskApi({ title: '复习', course_id: null, due_at: null })
  await api.updatePlanTaskApi(901, { due_at: null, course_id: null })
  await api.deferPlanTaskApi(901)
  await api.deferPlanTaskApi(901, 7)
  await api.deletePlanTaskApi(901)
  assert.deepEqual(calls, [
    { url: '/api/plan-tasks', method: 'get', params: { page: 2, page_size: 10, status: 'pending' } },
    { url: '/api/plan-tasks', method: 'post', data: { title: '复习', course_id: null, due_at: null } },
    { url: '/api/plan-tasks/901', method: 'put', data: { due_at: null, course_id: null } },
    { url: '/api/plan-tasks/901/defer', method: 'post', data: { days: 1 } },
    { url: '/api/plan-tasks/901/defer', method: 'post', data: { days: 7 } },
    { url: '/api/plan-tasks/901', method: 'delete' }
  ])
})

test('actual task panel loads per-page data and status filtering resets to page one', async () => {
  const f = fixture({}, { records: [row(), row({ id: 902, status: 'done' })] })
  await f.mount()
  assert.equal(f.state.tasks.value.length, 2)
  assert.deepEqual(f.state.courses.value, [{ id: 2, name: '数据结构' }])
  f.state.params.page = 3
  f.state.params.status = 'done'
  await f.state.changeStatus()
  assert.equal(f.state.params.page, 1)
  assert.deepEqual(f.calls.at(-1).args, [{ page: 1, page_size: 10, status: 'done' }])
  assert.equal(f.state.tasks.value[0].status, 'done')
  f.state.params.status = ''
  await f.state.changeStatus()
  assert.deepEqual(f.calls.at(-1).args, [{ page: 1, page_size: 10 }])
})

test('checkbox writes completion once while pending and leaves original state unchanged on a business failure', async () => {
  const gate = deferred()
  const f = fixture({ updatePlanTaskApi: () => gate.promise })
  const task = row()
  f.state.tasks.value = [task]
  const first = f.state.toggleTask(task, true)
  await f.state.toggleTask(task, true)
  assert.equal(f.calls.filter((call) => call.name === 'updatePlanTaskApi').length, 1)
  assert.deepEqual(f.calls[0].args, [901, { status: 'done' }])
  assert.equal(task.status, 'pending')
  assert.match(await f.html(), /type="checkbox" disabled/)
  gate.resolve({ code: 409, message: '状态修改失败' })
  await first
  assert.equal(task.status, 'pending')
  assert.equal(f.state.error.value, '状态修改失败')
  assert.equal(f.state.pendingIds.has(901), false)
})

test('checkbox can cancel completion using pending and refreshes the authoritative list', async () => {
  const records = [row({ status: 'done' })]
  const f = fixture({ updatePlanTaskApi: async (_, data) => {
    records[0] = { ...records[0], ...data }; return { code: 200, data: records[0] }
  } }, { records })
  await f.state.toggleTask(records[0], false)
  assert.deepEqual(f.calls[0].args, [901, { status: 'pending' }])
  assert.equal(f.state.tasks.value[0].status, 'pending')
})

test('defer defaults to one day, blocks a rapid second submit and displays the returned count/deadline', async () => {
  const gate = deferred()
  const updated = row({ due_at: '2099-10-13T18:00:00', defer_count: 3 })
  let changed = false
  const f = fixture({
    deferPlanTaskApi: async () => { await gate.promise; changed = true; return { code: 200, data: updated } },
    listPlanTasksApi: async () => ({ code: 200, data: { list: [changed ? updated : row({ defer_count: 2 })], total: 1 } })
  })
  f.state.openDefer(row({ defer_count: 2 }))
  assert.equal(f.state.deferDays.value, 1)
  const first = f.state.submitDefer()
  await f.state.submitDefer()
  assert.equal(f.calls.filter((call) => call.name === 'deferPlanTaskApi').length, 1)
  assert.deepEqual(f.calls[0].args, [901, 1])
  gate.resolve(true)
  await first
  assert.equal(f.state.deferVisible.value, false)
  assert.equal(f.state.tasks.value[0].defer_count, 3)
  const html = await f.html()
  assert.match(html, /已延期 3 次/)
  assert.match(html, /2099-10-13 18:00/)
})

test('defer accepts 1 through 30 integer days and rejects out-of-range or fractional values before any request', async () => {
  const f = fixture()
  for (const value of [0, 31, 1.5, null]) {
    f.state.openDefer(row())
    f.state.deferDays.value = value
    await f.state.submitDefer()
  }
  assert.deepEqual(f.calls, [])
  assert.match(f.state.deferError.value, /1～30/)
  f.state.deferDays.value = 30
  await f.state.submitDefer()
  assert.deepEqual(f.calls[0].args, [901, 30])
})

test('a defer failure preserves the original deadline/count and keeps the dialog available for retry', async () => {
  const f = fixture({ deferPlanTaskApi: async () => ({ code: 404, message: '计划任务不存在' }) })
  const original = row({ defer_count: 2 })
  f.state.tasks.value = [original]
  f.state.openDefer(original)
  await f.state.submitDefer()
  assert.equal(original.defer_count, 2)
  assert.equal(original.due_at, '2099-10-12T18:00:00')
  assert.equal(f.state.deferVisible.value, true)
  assert.equal(f.state.deferError.value, '计划任务不存在')
  assert.equal(f.state.pendingIds.has(901), false)
  assert.deepEqual(f.successes, [])
})

test('edit explicitly clears optional course/deadline with null and preserves local datetime without timezone conversion', async () => {
  const f = fixture()
  f.state.openEdit(row())
  f.state.form.title = '  完成实验报告  '
  f.state.form.course_id = ''
  f.state.form.due_at = ''
  await f.state.saveTask()
  assert.deepEqual(f.calls[0], { name: 'updatePlanTaskApi', args: [901,
    { title: '完成实验报告', course_id: null, due_at: null, status: 'pending' }] })
  f.state.openCreate()
  f.state.form.title = '预习'
  f.state.form.due_at = '2026-11-01T09:00:00'
  await f.state.saveTask()
  const create = f.calls.find((call) => call.name === 'createPlanTaskApi')
  assert.equal(create.args[0].due_at, '2026-11-01T09:00:00')
})

test('blank and overlength titles do not save; save failures retain editor contents', async () => {
  const f = fixture({ createPlanTaskApi: async () => ({ code: 400, message: '课程不存在' }) })
  f.state.openCreate()
  for (const title of ['  ', '字'.repeat(201)]) {
    f.state.form.title = title
    await f.state.saveTask()
  }
  assert.deepEqual(f.calls, [])
  f.state.form.title = '完成实验报告'
  await f.state.saveTask()
  assert.equal(f.state.editorVisible.value, true)
  assert.equal(f.state.form.title, '完成实验报告')
  assert.equal(f.state.formError.value, '课程不存在')
  assert.equal(f.state.saving.value, false)
})

test('delete locks before confirmation and cancellation performs no request', async () => {
  const gate = deferred()
  const f = fixture({}, { confirm: () => gate.promise })
  const first = f.state.removeTask(row())
  await f.state.removeTask(row())
  assert.equal(f.confirmations.length, 1)
  assert.match(f.confirmations[0][0], /完成数据结构实验报告二/)
  assert.deepEqual(f.calls, [])
  gate.reject('cancel')
  await first
  assert.deepEqual(f.calls, [])
  assert.equal(f.state.pendingIds.has(901), false)
  assert.equal(f.state.error.value, '')
})

test('delete success refreshes and clamps an emptied final page without touching textual plans', async () => {
  const f = fixture({}, { records: [] })
  f.state.params.page = 2
  await f.state.removeTask(row())
  assert.deepEqual(f.calls.map((call) => call.name), ['deletePlanTaskApi', 'listPlanTasksApi', 'listPlanTasksApi'])
  assert.equal(f.state.params.page, 1)
  assert.equal(f.state.tasks.value.length, 0)
  assert.match(await f.html(), /把学习安排拆成小任务/)
})

test('task cards render overdue, completed, unassigned, unset deadline and repeated deferrals separately', async () => {
  const f = fixture()
  f.state.tasks.value = [
    row({ due_at: '2010-10-01T12:00:00', defer_count: 2 }),
    row({ id: 902, title: '完成的任务', status: 'done', course_name: null, course_id: null, due_at: null })
  ]
  const html = await f.html()
  assert.match(html, /task-overdue/)
  assert.match(html, /已逾期/)
  assert.match(html, /已延期 2 次/)
  assert.match(html, /task-done/)
  assert.match(html, /未关联课程/)
  assert.match(html, /未设截止/)
  assert.match(html, /已完成/)
  assert.match(html, /disabled[^>]*>延期/)
})

test('late task list responses cannot overwrite a newer filter or unmounted panel', async () => {
  const gate = deferred()
  let count = 0
  const f = fixture({ listPlanTasksApi: async () => {
    if (++count === 1) return gate.promise
    return { code: 200, data: { list: [row({ status: 'done' })], total: 1 } }
  } })
  const first = f.state.loadTasks()
  f.state.params.status = 'done'
  await f.state.loadTasks()
  gate.resolve({ code: 200, data: { list: [row()], total: 1 } })
  await first
  assert.equal(f.state.tasks.value[0].status, 'done')
  f.unmount()
  await f.state.loadTasks()
  assert.equal(f.calls.length, 2)
})

test('StudyPlan retains the independent textual create/view/edit/delete and sanitized Markdown alongside task panel', () => {
  const text = readFileSync(new URL('../src/views/StudyPlan.vue', import.meta.url), 'utf8')
  assert.match(text, /文本学习计划/)
  assert.match(text, /可勾选任务，与文本计划并存/)
  assert.match(text, /<PlanTaskPanel\s*\/>/)
  assert.match(text, /import PlanTaskPanel from '@\/components\/PlanTaskPanel\.vue'/)
  for (const operation of ['createStudyPlan(text)', 'updateStudyPlan(editingId.value, text)',
    'deleteStudyPlan(row.id)', 'listStudyPlans({ page: params.page, page_size: params.pageSize })']) {
    assert.ok(text.includes(operation), operation)
  }
  assert.match(text, /MAX_CONTENT_LENGTH = 2000/)
  assert.match(text, /DOMPurify\.sanitize\(rawHtml/)
  assert.doesNotMatch(source, /study-plans|createStudyPlan|deleteStudyPlan|updateStudyPlan/)
})
