import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'
import * as Vue from 'vue'
import * as Icons from '@element-plus/icons-vue'
import AsyncValidator from 'async-validator'
import { ensureSuccess } from '../src/utils/learningItems.js'

const Schema = AsyncValidator.default ?? AsyncValidator
const source = readFileSync(new URL('../src/views/ScheduleManage.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source, { filename: 'ScheduleManage.vue' })
const script = compileScript(descriptor, { id: 'schedule-manage' }).content
const template = compileTemplate({ source: descriptor.template.content, filename: 'ScheduleManage.vue', id: 'schedule-manage' }).code
const periods = ['1-2节', '3-4节', '5-6节', '7-8节']
const row = () => ({ id: 901, user_id: 2, course_id: 2, username: 'aaa', user_name: '同学甲',
  course_name: '数据结构', class_date: '2026-10-14', period: '7-8节', location: '实验楼301',
  teacher: '张老师', start_week: 3, end_week: 8 })
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => {
  resolve = yes; reject = no
}); return { promise, resolve, reject } }

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

function fixture(overrides = {}) {
  const calls = [], successes = []
  let mount, unmount
  const defaults = {
    getWeekScheduleApi: async () => ({ code: 200, data: { periods, total_weeks: 20 } }),
    listSchedulesApi: async () => ({ code: 200, data: { list: [row()], total: 1 } }),
    createScheduleApi: async () => ({ code: 200, data: row() }),
    updateScheduleApi: async () => ({ code: 200, data: row() }),
    deleteScheduleApi: async () => ({ code: 200, data: null }),
    listCoursesApi: async () => ({ code: 200, data: [{ id: 2, name: '数据结构', code: 'DS101', status: 1 }] }),
    getUserPageList: async () => ({ code: 200, data: { list: [{ id: 2, name: '同学甲', username: 'aaa', role: 'student' }], total: 1 } })
  }
  const api = Object.fromEntries(Object.keys(defaults).map(name => [name, async (...args) => {
    calls.push({ name, args: structuredClone(args) })
    return (overrides[name] || defaults[name])(...args)
  }]))
  const component = evaluate(script, {
    vue: { ...Vue, onMounted: handler => { mount = handler }, onBeforeUnmount: handler => { unmount = handler } },
    '@element-plus/icons-vue': Icons,
    'element-plus': { ElMessage: { success: message => successes.push(message) },
      ElMessageBox: { confirm: async () => overrides.confirm?.() ?? true } },
    '@/api/schedule': api, '@/api/course': api, '@/api/user': api,
    '@/utils/learningItems': { ensureSuccess }
  })
  const state = component.setup({}, { expose() {} })
  state.formRef.value = { clearValidate() {}, validate: async () => {
    await new Schema(state.rules).validate(state.form)
    return true
  } }
  const render = evaluate(template, { vue: Vue })
  async function html() {
    const app = Vue.createSSRApp({ setup: () => ({ ...state }), render })
    const simple = { setup: (_, { slots }) => () => Vue.h('div', slots.default?.()) }
    for (const name of ['ElForm', 'ElFormItem', 'ElSelect', 'ElInput', 'ElIcon', 'ElTag']) app.component(name, simple)
    for (const name of ['ElDatePicker', 'ElInputNumber', 'ElPagination']) app.component(name, { render: () => Vue.h('div') })
    app.component('ElOption', { props: ['label', 'value'], setup: props => () => Vue.h('option', { value: props.value }, props.label) })
    app.component('ElCard', { setup: (_, { slots }) => () => Vue.h('div', [slots.header?.(), slots.default?.()]) })
    app.component('ElAlert', { props: ['title'], setup: (props, { slots }) => () => Vue.h('div', { role: 'alert' }, [props.title, slots.default?.()]) })
    app.component('ElButton', { props: ['disabled', 'loading'], setup: (props, { slots }) => () =>
      Vue.h('button', { disabled: props.disabled, 'data-loading': props.loading }, slots.default?.()) })
    app.component('ElDialog', { props: ['modelValue'], setup: (props, { slots }) => () => props.modelValue
      ? Vue.h('dialog', [slots.default?.(), slots.footer?.()]) : null })
    app.component('ElTable', { props: ['data'], setup: (props, { slots }) => {
      Vue.provide('schedule-rows', Vue.computed(() => props.data || []))
      return () => Vue.h('div', slots.default?.())
    } })
    app.component('ElTableColumn', { props: ['prop', 'label'], setup: (props, { slots }) => {
      const rows = Vue.inject('schedule-rows')
      return () => Vue.h('div', [props.label, ...rows.value.map(row => Vue.h('span', slots.default?.({ row }) || String(row[props.prop] ?? '')))])
    } })
    app.directive('loading', { getSSRProps: () => ({}) })
    return renderToString(app)
  }
  return { state, calls, successes, mount: () => mount(), unmount: () => unmount(), html }
}

function populate(state) {
  Object.assign(state.form, { user_id: 2, course_id: 2, class_date: '2026-10-14', period: '7-8节', location: '实验楼301' })
}

test('schedule form reads four standard periods and total weeks from the existing week endpoint', async () => {
  const f = fixture({ getWeekScheduleApi: async () => ({ code: 200, data: { periods, total_weeks: 16 } }) })
  await f.mount()
  f.state.handleCreate()
  assert.deepEqual(f.state.periods.value, periods)
  assert.equal(f.state.form.start_week, 1)
  assert.equal(f.state.form.end_week, 16)
  const html = await f.html()
  for (const period of periods) assert.match(html, new RegExp(`<option value="${period}">${period}</option>`))
  assert.doesNotMatch(descriptor.template.content, /<el-input v-model="form\.period"/)
  assert.match(html, /教师|起始周|结束周/)
  assert.deepEqual(f.calls.find(call => call.name === 'getWeekScheduleApi').args, [])
})

test('actual schedule create submits standard period, configured range and nullable teacher', async () => {
  const f = fixture()
  await f.mount()
  f.state.params.userId = 2
  f.state.handleCreate()
  populate(f.state)
  await f.state.handleSave()
  assert.deepEqual(f.calls.find(call => call.name === 'createScheduleApi').args, [{
    user_id: 2, course_id: 2, class_date: '2026-10-14', period: '7-8节', location: '实验楼301',
    teacher: null, start_week: 1, end_week: 20
  }])
  assert.equal(f.state.dialogVisible.value, false)
  assert.deepEqual(f.successes, ['操作成功'])
})

test('actual schedule edit fills teacher and range, and sends explicit null when the teacher is cleared', async () => {
  const f = fixture()
  await f.mount()
  f.state.handleEdit(row())
  assert.equal(f.state.form.teacher, '张老师')
  assert.equal(f.state.form.start_week, 3)
  assert.equal(f.state.form.end_week, 8)
  f.state.form.teacher = '  '
  await f.state.handleSave()
  assert.deepEqual(f.calls.find(call => call.name === 'updateScheduleApi').args, [901, {
    user_id: 2, course_id: 2, class_date: '2026-10-14', period: '7-8节', location: '实验楼301',
    teacher: null, start_week: 3, end_week: 8
  }])
})

test('the form rejects unsupported periods, invalid or reversed weeks and oversized teachers before HTTP', async () => {
  for (const invalid of [{ period: '9-10节' }, { period: '' }, { start_week: 0 }, { end_week: 21 },
    { start_week: 1.5 }, { end_week: null }, { start_week: 9, end_week: 8 }, { teacher: '师'.repeat(101) }]) {
    const f = fixture()
    await f.mount()
    f.state.handleCreate()
    populate(f.state)
    Object.assign(f.state.form, invalid)
    await f.state.handleSave()
    assert.equal(f.calls.some(call => ['createScheduleApi', 'updateScheduleApi'].includes(call.name)), false, JSON.stringify(invalid))
    assert.equal(f.state.dialogVisible.value, true)
    assert.equal(f.state.formLoading.value, false)
  }
})

test('schedule business 409 retains edited form and displays the conflict without claiming success', async () => {
  const f = fixture({ updateScheduleApi: async () => ({ code: 409, message: '该学生同一天同节次已有课表安排' }) })
  await f.mount()
  f.state.handleEdit(row())
  f.state.form.teacher = '李老师'
  await f.state.handleSave()
  assert.equal(f.state.dialogVisible.value, true)
  assert.equal(f.state.form.teacher, '李老师')
  assert.equal(f.state.formError.value, '该学生同一天同节次已有课表安排')
  assert.deepEqual(f.successes, [])
  assert.match(await f.html(), /该学生同一天同节次已有课表安排/)
})

test('schedule save locks before async validation so rapid clicks send one request', async () => {
  const validation = deferred(), response = deferred()
  const f = fixture({ createScheduleApi: () => response.promise })
  await f.mount()
  f.state.handleCreate()
  populate(f.state)
  f.state.formRef.value.validate = () => validation.promise
  const first = f.state.handleSave()
  await f.state.handleSave()
  assert.equal(f.state.formLoading.value, true)
  assert.equal(f.calls.some(call => call.name === 'createScheduleApi'), false)
  validation.resolve(true)
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(f.calls.filter(call => call.name === 'createScheduleApi').length, 1)
  await f.state.handleSave()
  assert.equal(f.calls.filter(call => call.name === 'createScheduleApi').length, 1)
  response.resolve({ code: 200, data: row() })
  await first
  assert.equal(f.state.formLoading.value, false)
})

test('failed semester configuration leaves the list usable, prevents saving and supports retry', async () => {
  let fail = true
  const f = fixture({ getWeekScheduleApi: async () => fail ? { code: 503, message: '配置服务暂不可用' }
    : { code: 200, data: { periods, total_weeks: 20 } } })
  await f.mount()
  assert.equal(f.state.tableData.value.length, 1)
  f.state.handleCreate()
  populate(f.state)
  await f.state.handleSave()
  assert.equal(f.calls.some(call => call.name === 'createScheduleApi'), false)
  assert.equal(f.state.configReady.value, false)
  assert.match(await f.html(), /学期配置加载失败：配置服务暂不可用|重新加载学期配置|数据结构/)
  fail = false
  await f.state.loadConfig()
  assert.equal(f.state.configReady.value, true)
  assert.equal(f.state.configError.value, '')
  assert.equal(f.state.form.end_week, 20)
  await f.state.handleSave()
  assert.equal(f.calls.filter(call => call.name === 'createScheduleApi').length, 1)
})

test('malformed period configuration is rejected instead of exposing arbitrary input options', async () => {
  for (const invalid of [{ periods: ['1-2节'], total_weeks: 20 }, { periods, total_weeks: 0 },
    { periods: ['1-2节', '1-2节', '5-6节', '7-8节'], total_weeks: 20 }]) {
    const f = fixture({ getWeekScheduleApi: async () => ({ code: 200, data: invalid }) })
    await f.mount()
    assert.equal(f.state.configReady.value, false)
    assert.deepEqual(f.state.periods.value, [])
    assert.match(f.state.configError.value, /配置格式不正确/)
  }
})

test('schedule pagination and administrator student/date filters keep their original request contract', async () => {
  const f = fixture()
  await f.mount()
  Object.assign(f.state.params, { page: 3, pageSize: 20, userId: 2, date: '2026-10-14' })
  await f.state.load()
  assert.deepEqual(f.calls.filter(call => call.name === 'listSchedulesApi').at(-1).args, [{
    page: 3, page_size: 20, user_id: 2, date: '2026-10-14'
  }])
  f.state.handleSearch()
  assert.equal(f.state.params.page, 1)
})

test('network save failure preserves the dialog and allows a successful retry', async () => {
  let fail = true
  const f = fixture({ createScheduleApi: async () => {
    if (fail) throw new Error('网络不可用')
    return { code: 200, data: row() }
  } })
  await f.mount()
  f.state.handleCreate()
  populate(f.state)
  f.state.form.teacher = '  王老师  '
  await f.state.handleSave()
  assert.equal(f.state.dialogVisible.value, true)
  assert.equal(f.state.formError.value, '网络不可用')
  assert.deepEqual(f.successes, [])
  fail = false
  await f.state.handleSave()
  assert.equal(f.state.dialogVisible.value, false)
  assert.equal(f.calls.filter(call => call.name === 'createScheduleApi').at(-1).args[0].teacher, '王老师')
})

test('schedule deletion cancellation and failed business response never report successful deletion', async () => {
  const cancel = fixture({ confirm: async () => { throw 'cancel' } })
  await cancel.mount()
  await cancel.state.handleDelete(row())
  assert.equal(cancel.calls.some(call => call.name === 'deleteScheduleApi'), false)
  assert.equal(cancel.state.actionError.value, '')
  const failure = fixture({ deleteScheduleApi: async () => ({ code: 404, message: '课表不存在' }) })
  await failure.mount()
  await failure.state.handleDelete(row())
  assert.equal(failure.state.actionError.value, '课表不存在')
  assert.deepEqual(failure.successes, [])
  assert.equal(failure.state.pendingIds.size, 0)
})
