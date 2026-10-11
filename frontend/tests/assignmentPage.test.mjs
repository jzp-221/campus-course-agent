import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'
import * as Vue from 'vue'
import * as Icons from '@element-plus/icons-vue'
import AsyncValidator from 'async-validator'
import * as helpers from '../src/utils/learningItems.js'

const Schema = AsyncValidator.default ?? AsyncValidator
const source = readFileSync(new URL('../src/views/Assignment.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source, { filename: 'Assignment.vue' })
const script = compileScript(descriptor, { id: 'assignment-page-test' }).content
const template = compileTemplate({ source: descriptor.template.content,
  filename: 'Assignment.vue', id: 'assignment-page-test' }).code
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => {
  resolve = yes; reject = no
}); return { promise, resolve, reject } }
const item = (overrides = {}) => ({ id: 17, user_id: 2, title: '第 3 章课后习题',
  type: 'assignment', status: 'pending', course_id: 2, course_name: '数据结构',
  due_at: '2099-01-04T18:00:00', note: null, ...overrides })

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

function fixture(overrides = {}, options = {}) {
  const calls = [], successes = [], confirmations = []
  let mount, unmount
  const route = Vue.reactive({ path: '/manager/assignment', query: options.query || {} })
  const assignments = Vue.ref(options.items || [])
  const inbox = { assignments, loading: Vue.ref(false), error: Vue.ref(''),
    pendingCount: Vue.computed(() => helpers.pendingCount(assignments.value)),
    refresh: async (force = false) => { calls.push({ name: 'refresh', args: [force] }); return options.refresh?.(force) } }
  const defaults = {
    createAssignmentApi: async () => ({ code: 200, data: item() }),
    updateAssignmentApi: async () => ({ code: 200, data: item() }),
    deleteAssignmentApi: async () => ({ code: 200, data: null })
  }
  const api = Object.fromEntries(Object.keys(defaults).map(name => [name, async (...args) => {
    calls.push({ name, args: structuredClone(args) })
    return (overrides[name] || defaults[name])(...args)
  }]))
  const component = evaluate(script, {
    vue: { ...Vue, watch: (source, callback, options) => Vue.watch(source, callback, { ...options, flush: 'sync' }),
      onMounted: handler => { mount = handler }, onBeforeUnmount: handler => { unmount = handler } },
    'vue-router': { useRoute: () => route, useRouter: () => ({ replace: value => { calls.push({ name: 'router.replace', args: [value] }); route.query = value.query } }) },
    '@element-plus/icons-vue': Icons,
    'element-plus': {
      ElMessage: { success: message => successes.push(message) },
      ElMessageBox: { confirm: async (...args) => { confirmations.push(args); return options.confirm?.(...args) ?? true } }
    },
    '@/api/assignment': api,
    '@/api/learningContext': { getLearningContextApi: async () => {
      calls.push({ name: 'getLearningContextApi', args: [] })
      return options.context?.() || { code: 200, data: { courses: [{ id: 2, name: '数据结构' }], holidays: [], makeup_workdays: [] } }
    } },
    '@/stores/learningInbox': { useLearningInbox: () => inbox },
    '@/utils/learningItems': helpers
  })
  const state = component.setup({}, { expose() {} })
  state.formRef.value = { clearValidate() {}, validate: async () => {
    await new Schema(state.rules).validate(state.form)
    return true
  } }
  const render = evaluate(template, { vue: Vue })
  async function html() {
    const app = Vue.createSSRApp({ setup: () => ({ ...state }), render, components: Icons })
    const simple = { setup: (_, { slots }) => () => Vue.h('div', slots.default?.()) }
    for (const name of ['ElSelect', 'ElRadioGroup', 'ElRadio', 'ElForm', 'ElFormItem']) app.component(name, simple)
    for (const name of ['ElInput', 'ElDatePicker', 'ElOption', 'ElPagination']) app.component(name, { render: () => Vue.h('div') })
    app.component('ElTag', { props: ['type'], setup: (props, { slots }) => () => Vue.h('span', { 'data-tag-type': props.type }, slots.default?.()) })
    app.component('ElButton', { props: ['disabled', 'loading'], setup: (props, { slots }) => () =>
      Vue.h('button', { disabled: props.disabled, 'data-loading': props.loading }, slots.default?.()) })
    app.component('ElCard', { setup: (_, { slots }) => () => Vue.h('div', [slots.header?.(), slots.default?.()]) })
    app.component('ElAlert', { props: ['title'], setup: (props, { slots }) => () => Vue.h('div', { role: 'alert' }, [props.title, slots.default?.()]) })
    app.component('ElEmpty', { props: ['description'], setup: props => () => Vue.h('div', props.description) })
    app.component('ElTable', { props: ['data'], setup: (props, { slots }) => {
      Vue.provide('assignment-rows', Vue.computed(() => props.data || []))
      return () => Vue.h('div', [slots.default?.(), ...(props.data?.length ? [] : [slots.empty?.()])])
    } })
    app.component('ElTableColumn', { props: ['prop', 'label'], setup: (props, { slots }) => {
      const rows = Vue.inject('assignment-rows')
      return () => Vue.h('div', [props.label, ...rows.value.map(row => Vue.h('div', slots.default?.({ row }) || String(row[props.prop] ?? '')))])
    } })
    app.component('ElDialog', { props: ['modelValue'], setup: (props, { slots }) => () =>
      props.modelValue ? Vue.h('dialog', [slots.default?.(), slots.footer?.()]) : null })
    app.directive('loading', { getSSRProps: () => ({}) })
    return renderToString(app)
  }
  return { state, calls, successes, confirmations, inbox, route, html, mount: () => mount(), unmount: () => unmount() }
}

test('assignment API uses the existing request convention for all four endpoints', async () => {
  const apiSource = readFileSync(new URL('../src/api/assignment.js', import.meta.url), 'utf8')
  const requests = []
  const executable = apiSource.replace(/^import .*\n/, '').replace(/^export /gm, '')
    + '\nreturn {listAssignmentsApi, createAssignmentApi, updateAssignmentApi, deleteAssignmentApi}'
  const api = new Function('request', executable)(async request => { requests.push(request); return { code: 200 } })
  await api.listAssignmentsApi({ type: 'exam', status: 'pending', days: 3 })
  await api.createAssignmentApi({ title: '期中考试' })
  await api.updateAssignmentApi(17, { status: 'done' })
  await api.deleteAssignmentApi(17)
  assert.deepEqual(requests, [
    { url: '/api/assignments', method: 'get', params: { type: 'exam', status: 'pending', days: 3 } },
    { url: '/api/assignments', method: 'post', data: { title: '期中考试' } },
    { url: '/api/assignments/17', method: 'put', data: { status: 'done' } },
    { url: '/api/assignments/17', method: 'delete' }
  ])
})

test('the actual create dialog trims the title and preserves local ISO time without user identity fields', async () => {
  const f = fixture()
  f.state.openCreate()
  Object.assign(f.state.form, { course_id: 2, title: '  实验报告二  ', due_at: '2026-10-12T18:00:00', note: '' })
  await f.state.saveItem()
  assert.deepEqual(f.calls, [
    { name: 'createAssignmentApi', args: [{ course_id: 2, type: 'assignment', title: '实验报告二',
      due_at: '2026-10-12T18:00:00', status: 'pending', note: '' }] },
    { name: 'refresh', args: [true] }
  ])
  assert.equal(f.state.dialogVisible.value, false)
  assert.deepEqual(f.successes, ['事项已保存'])
})

test('the real form rejects blank or oversized titles, invalid whitelist values, missing course, bad time and excessive notes', async () => {
  const invalid = [
    { title: ' \t ' }, { title: '题'.repeat(201) }, { type: 'quiz' }, { status: 'completed' },
    { course_id: null }, { due_at: '' }, { due_at: '2026-13-99T18:00:00' },
    { due_at: '2026-02-30T18:00:00' }, { due_at: '2026-10-12T18:00:00Z' }, { note: '备注'.repeat(251) }
  ]
  for (const fields of invalid) {
    const f = fixture()
    f.state.openCreate()
    Object.assign(f.state.form, { course_id: 2, title: '实验报告二', due_at: '2026-10-12T18:00:00', ...fields })
    await f.state.saveItem()
    assert.deepEqual(f.calls, [], JSON.stringify(fields))
    assert.equal(f.state.dialogVisible.value, true)
    assert.equal(f.state.formLoading.value, false)
  }
})

test('save locks before asynchronous validation and never submits twice on rapid clicks', async () => {
  const validation = deferred(), request = deferred()
  const f = fixture({ createAssignmentApi: () => request.promise })
  f.state.openCreate()
  Object.assign(f.state.form, { course_id: 2, title: '实验报告二', due_at: '2026-10-12T18:00:00' })
  f.state.formRef.value.validate = () => validation.promise
  const first = f.state.saveItem()
  await f.state.saveItem()
  assert.equal(f.state.formLoading.value, true)
  assert.deepEqual(f.calls, [])
  validation.resolve(true)
  await Promise.resolve()
  await Promise.resolve()
  await f.state.saveItem()
  assert.equal(f.calls.filter(call => call.name === 'createAssignmentApi').length, 1)
  request.resolve({ code: 200, data: item() })
  await first
  assert.equal(f.state.formLoading.value, false)
})

test('the edit dialog can clear a note and does not convert date values or overwrite ownership', async () => {
  const f = fixture()
  f.state.openEdit(item({ note: '旧备注' }))
  f.state.form.note = ''
  f.state.form.type = 'exam'
  await f.state.saveItem()
  assert.equal(f.calls[0].name, 'updateAssignmentApi')
  assert.equal(f.calls[0].args[0], 17)
  assert.equal(f.calls[0].args[1].note, '')
  assert.equal(f.calls[0].args[1].type, 'exam')
  assert.equal(f.calls[0].args[1].due_at, '2099-01-04T18:00:00')
  assert.equal(Object.hasOwn(f.calls[0].args[1], 'user_id'), false)
})

test('HTTP-200 business failure keeps the dialog editable with the actual reason, and permits retry', async () => {
  let fail = true
  const f = fixture({ createAssignmentApi: async () => fail ? { code: 404, message: '关联课程不存在' } : { code: 200, data: item() } })
  f.state.openCreate()
  Object.assign(f.state.form, { course_id: 2, title: '实验报告二', due_at: '2026-10-12T18:00:00' })
  await f.state.saveItem()
  assert.equal(f.state.formError.value, '关联课程不存在')
  assert.equal(f.state.dialogVisible.value, true)
  assert.equal(f.state.formLoading.value, false)
  assert.deepEqual(f.successes, [])
  fail = false
  await f.state.saveItem()
  assert.equal(f.state.formError.value, '')
  assert.equal(f.state.dialogVisible.value, false)
})

test('completion sends only status, locks the row, preserves state on business failure and refreshes badges on success', async () => {
  const gate = deferred()
  const f = fixture({ updateAssignmentApi: () => gate.promise })
  const row = item()
  const first = f.state.toggleDone(row)
  await f.state.toggleDone(row)
  assert.equal(f.state.pendingIds.has(17), true)
  assert.deepEqual(f.calls, [{ name: 'updateAssignmentApi', args: [17, { status: 'done' }] }])
  gate.resolve({ code: 404, message: '事项不存在' })
  await first
  assert.equal(f.state.pendingIds.has(17), false)
  assert.equal(row.status, 'pending')
  assert.equal(f.state.actionError.value, '事项不存在')
  assert.deepEqual(f.successes, [])
  const success = fixture()
  await success.state.toggleDone(item({ status: 'done' }))
  assert.deepEqual(success.calls, [{ name: 'updateAssignmentApi', args: [17, { status: 'pending' }] }, { name: 'refresh', args: [true] }])
})

test('delete locks before confirmation, cancellation sends zero requests and subsequent success reloads the inbox', async () => {
  const gate = deferred()
  let confirm = () => gate.promise
  const f = fixture({}, { confirm: () => confirm() })
  const first = f.state.removeItem(item())
  await f.state.removeItem(item())
  assert.equal(f.confirmations.length, 1)
  assert.match(f.confirmations[0][0], /第 3 章课后习题.*无法恢复/)
  assert.deepEqual(f.calls, [])
  gate.reject('cancel')
  await first
  assert.equal(f.state.pendingIds.has(17), false)
  assert.equal(f.state.actionError.value, '')
  confirm = async () => true
  await f.state.removeItem(item())
  assert.deepEqual(f.calls, [{ name: 'deleteAssignmentApi', args: [17] }, { name: 'refresh', args: [true] }])
})

test('overdue filtering uses all inbox pages before local pagination and combined type/status filters remain correct', () => {
  const rows = Array.from({ length: 34 }, (_, index) => item({ id: index + 1, due_at: '2099-01-04T18:00:00' }))
  rows.push(item({ id: 100, title: '逾期考试', type: 'exam', due_at: '2000-01-01T10:00:00' }))
  rows.push(item({ id: 101, title: '已完成考试', type: 'exam', status: 'done', due_at: '2000-01-01T10:00:00' }))
  const f = fixture({}, { items: rows })
  assert.equal(f.state.filteredItems.value.length, 36)
  f.state.filters.dueRange = 'overdue'
  f.state.filters.type = 'exam'
  f.state.filters.status = 'pending'
  f.state.pagination.page = 1
  assert.deepEqual(f.state.pageItems.value.map(row => row.id), [100])
  assert.equal(f.state.overdueCount.value, 1)
  assert.equal(f.inbox.pendingCount.value, 35)
  f.state.filters.dueRange = 'all'
  f.state.filters.type = ''
  f.state.filters.status = ''
  f.state.pagination.page = 4
  assert.equal(f.state.pageItems.value.length, 6)
  f.state.resetPage()
  assert.equal(f.state.pagination.page, 1)
})

test('real page rendering shows overdue warning, exam danger tag, completed styling and server-provided holiday notices', async () => {
  const f = fixture({}, { items: [
    item({ title: '逾期考试', type: 'exam', due_at: '2000-01-01T10:00:00' }),
    item({ id: 18, title: '已完成的作业', status: 'done' }),
    item({ id: 19, title: '假期截止示例', due_at: '2099-01-01T18:00:00' })
  ] })
  f.state.calendar.value = { holidays: [{ name: '测试假期', start_date: '2099-01-01', end_date: '2099-01-03' }], makeup_workdays: [] }
  const html = await f.html()
  assert.match(html, /已逾期，请尽快处理/)
  assert.match(html, /data-tag-type="danger"[^>]*>考试/)
  assert.match(html, /取消完成/)
  assert.match(html, /该截止日在放假期间（测试假期 1\/1-1\/3）/)
  assert.equal(f.state.rowClass({ row: item({ due_at: '2000-01-01T10:00:00' }) }), 'assignment-row assignment-overdue')
  assert.equal(f.state.rowClass({ row: item({ status: 'done' }) }), 'assignment-row assignment-done')
  assert.match(descriptor.styles[0].content, /assignment-done.*td\.el-table__cell/s)
  assert.doesNotMatch(source, /2026-09-25|2026-10-01|2027-01-01|user_id|listUsersApi/)
})

test('context failure stays visible and can be retried; refresh failure never reports a completed write as unsaved', async () => {
  let fail = true
  const f = fixture({}, { context: () => fail ? { code: 503, message: '假期信息暂不可用' } : { code: 200, data: { courses: [{ id: 2, name: '数据结构' }], holidays: [], makeup_workdays: [] } },
    refresh: async () => { throw new Error('网络中断') } })
  await f.state.loadContext()
  assert.equal(f.state.contextError.value, '假期信息暂不可用')
  fail = false
  await f.state.loadContext()
  assert.equal(f.state.contextError.value, '')
  assert.deepEqual(f.state.courses.value, [{ id: 2, name: '数据结构' }])
  await f.state.toggleDone(item())
  assert.match(f.state.actionError.value, /操作已保存，列表刷新失败：网络中断/)
  assert.equal(f.successes.length, 1)
})

test('a course detail link filters the full own inbox by course_id and its visible tag can clear only that route filter', async () => {
  const f = fixture({}, { query: { course_id: '2', from: 'detail' }, items: [item(), item({ id: 18, course_id: 4, course_name: '大学英语' })] })
  assert.deepEqual(f.state.pageItems.value.map(row => row.course_id), [2])
  assert.match(await f.html(), /当前课程：.*数据结构/s)
  f.state.clearCourseFilter()
  assert.deepEqual(f.route.query, { from: 'detail' })
  assert.equal(f.state.pageItems.value.length, 2)
  f.route.query = { course_id: 'not-an-id' }
  assert.equal(f.state.pageItems.value.length, 2)
})

test('home reminder due links initialize valid ranges, reject unknown values and react to route changes with course filtering intact', () => {
  for (const due of ['3', '7', 'overdue']) {
    const f = fixture({}, { query: { due } })
    assert.equal(f.state.filters.dueRange, due)
  }
  for (const due of [undefined, 'all', '30', 'invalid', ['overdue']]) {
    const f = fixture({}, { query: { due } })
    assert.equal(f.state.filters.dueRange, 'all')
  }
  const f = fixture({}, { query: { due: 'overdue', course_id: '2' }, items: [
    item({ due_at: '2000-01-01T18:00:00' }),
    item({ id: 18, course_id: 4, due_at: '2000-01-01T18:00:00' }),
    item({ id: 19 })
  ] })
  assert.deepEqual(f.state.pageItems.value.map(row => row.id), [17])
  f.route.query = { due: 'invalid', course_id: '2' }
  assert.equal(f.state.filters.dueRange, 'all')
  assert.deepEqual(f.state.pageItems.value.map(row => row.id), [17, 19])
  f.route.query = { due: 'overdue', course_id: '4' }
  assert.equal(f.state.filters.dueRange, 'overdue')
  assert.deepEqual(f.state.pageItems.value.map(row => row.id), [18])
  f.state.clearCourseFilter()
  assert.equal(f.route.query.due, 'overdue')
  assert.deepEqual(f.state.pageItems.value.map(row => row.id), [17, 18])
})
