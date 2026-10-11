import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'
import * as Vue from 'vue'
import * as Icons from '@element-plus/icons-vue'
import * as helpers from '../src/utils/documentManagement.js'

const source = readFileSync(new URL('../src/views/Document.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source, { filename: 'Document.vue' })
const script = compileScript(descriptor, { id: 'document-page-test' }).content
const template = compileTemplate({ source: descriptor.template.content,
  filename: 'Document.vue', id: 'document-page-test' }).code
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => {
  resolve = yes; reject = no
}); return { promise, resolve, reject } }
const file = (name) => new File(['测试课程资料'], name)
const row = (status = 'indexed') => ({ id: 101, filename: 'P11测试.md', status, chunk_count: 1,
  create_time: '2026-10-10T21:00:00', error_message: null })

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

function fixture(overrides = {}, { role = 'admin', confirm = async () => true } = {}) {
  const calls = [], confirmations = [], successes = []
  let mount, unmount
  const defaults = {
    listDocumentsApi: async () => ({ code: 200, data: { list: [], total: 0 } }),
    uploadDocumentsApi: async (files) => ({ code: 200, data: files.map((_, index) => ({ ...row(), id: 101 + index })) }),
    previewDocumentApi: async () => ({ code: 200, data: { ...row(), extracted_text: '抽取文本' } }),
    reindexDocumentApi: async () => ({ code: 200, data: row() }),
    deleteDocumentApi: async () => ({ code: 200, data: null })
  }
  const api = Object.fromEntries(Object.keys(defaults).map((name) => [name, async (...args) => {
    calls.push({ name, args })
    return (overrides[name] || defaults[name])(...args)
  }]))
  const component = evaluate(script, {
    vue: { ...Vue, onMounted: (handler) => { mount = handler },
      onBeforeUnmount: (handler) => { unmount = handler } },
    '@element-plus/icons-vue': Icons,
    'element-plus': {
      ElMessage: { success: (message) => successes.push(message) },
      ElMessageBox: { confirm: async (...args) => { confirmations.push(args); return confirm(...args) } }
    },
    '@/api/document': api,
    '@/utils/user': { useUser: () => ({ userInfo: Vue.ref({ id: role === 'admin' ? 1 : 2, role }) }) },
    '@/utils/documentManagement': helpers
  })
  const state = component.setup({}, { expose() {} })
  const render = evaluate(template, { vue: Vue })
  async function html() {
    const app = Vue.createSSRApp({ setup: () => ({ ...state }), render, components: Icons })
    const simple = { setup: (_, { slots }) => () => Vue.h('div', slots.default?.()) }
    for (const name of ['ElIcon', 'ElTag', 'ElTooltip']) app.component(name, simple)
    app.component('ElCard', { setup: (_, { slots }) => () => Vue.h('div', [slots.header?.(), slots.default?.()]) })
    app.component('ElButton', { props: ['disabled', 'loading'], setup: (props, { slots }) => () =>
      Vue.h('button', { disabled: props.disabled, 'data-loading': props.loading }, slots.default?.()) })
    app.component('ElUpload', { props: ['accept', 'multiple', 'drag', 'autoUpload', 'disabled'],
      setup: (props, { slots }) => () => Vue.h('div', [
        Vue.h('input', { type: 'file', accept: props.accept, multiple: props.multiple, disabled: props.disabled }),
        slots.default?.(), slots.tip?.()
      ]) })
    app.component('ElAlert', { props: ['title'], setup: (props) => () => Vue.h('div', { role: 'alert' }, props.title) })
    app.component('ElEmpty', { props: ['description'], setup: (props) => () => Vue.h('div', props.description) })
    app.component('ElResult', { props: ['title', 'subTitle'], setup: (props) => () =>
      Vue.h('div', { class: 'result' }, [props.title, props.subTitle]) })
    app.component('ElProgress', { props: ['percentage'], setup: (props) => () => Vue.h('progress', { value: props.percentage, max: 100 }) })
    app.component('ElPagination', simple)
    app.component('ElTable', { props: ['data'], setup: (props, { slots }) => {
      Vue.provide('document-rows', Vue.computed(() => props.data || []))
      return () => Vue.h('div', [slots.default?.(), ...(props.data?.length ? [] : [slots.empty?.()])])
    } })
    app.component('ElTableColumn', { props: ['prop', 'label'], setup: (props, { slots }) => {
      const rows = Vue.inject('document-rows')
      return () => Vue.h('div', [props.label, ...rows.value.map((row) =>
        Vue.h('div', slots.default?.({ row }) || String(row[props.prop] ?? '')))])
    } })
    app.component('ElDialog', { props: ['modelValue'], setup: (props, { slots }) => () =>
      props.modelValue ? Vue.h('dialog', [slots.default?.(), slots.footer?.()]) : null })
    app.directive('loading', { getSSRProps: () => ({}) })
    return renderToString(app)
  }
  return { state, calls, confirmations, successes, html, mount: () => mount(), unmount: () => unmount() }
}

test('the actual page submits one complete batch, displays progress and blocks a duplicate upload click', async () => {
  const gate = deferred()
  let progress
  const f = fixture({ uploadDocumentsApi: async (files, options) => { progress = options.onProgress; return gate.promise } })
  f.state.selectedFiles.value = [file('第一份.md'), file('第二份.txt')]
  const first = f.state.uploadBatch()
  await f.state.uploadBatch()
  assert.equal(f.calls.filter((call) => call.name === 'uploadDocumentsApi').length, 1)
  assert.deepEqual(f.calls[0].args[0].map((file) => file.name), ['第一份.md', '第二份.txt'])
  assert.equal(f.state.uploading.value, true)
  progress({ loaded: 5, total: 10 })
  assert.equal(f.state.uploadProgress.value, 50)
  assert.match(await f.html(), /正在上传整批文件/)
  gate.resolve({ code: 200, data: [row(), { ...row(), id: 102 }] })
  await first
  assert.equal(f.state.uploading.value, false)
  assert.match(f.state.uploadResult.value, /整批 2 份资料已入库/)
  assert.deepEqual(f.state.selectedFiles.value, [])
  assert.equal(f.calls.filter((call) => call.name === 'listDocumentsApi').length, 1)
})

test('the actual page rejects a mixed md/png selection with the concrete filename and zero upload requests', async () => {
  const f = fixture()
  f.state.selectedFiles.value = [file('正常资料.md'), file('坏文件.png')]
  await f.state.uploadBatch()
  assert.match(f.state.uploadError.value, /坏文件\.png/)
  assert.match(f.state.uploadError.value, /整批未上传/)
  assert.deepEqual(f.calls, [])
  assert.equal(f.state.selectedFiles.value.length, 2)
  assert.match(await f.html(), /<button[^>]*disabled[^>]*>整批上传并入库/)
})

test('HTTP-200 business failure is shown verbatim, keeps selected files and never reports indexing success', async () => {
  const f = fixture({ uploadDocumentsApi: async () => ({ code: 400,
    message: '扫描版.pdf：未能提取文本，请使用包含文字层的 PDF' }) })
  f.state.selectedFiles.value = [file('正常资料.md'), file('扫描版.pdf')]
  await f.state.uploadBatch()
  assert.equal(f.state.uploadError.value, '扫描版.pdf：未能提取文本，请使用包含文字层的 PDF')
  assert.equal(f.state.uploadResult.value, '')
  assert.equal(f.state.selectedFiles.value.length, 2)
  assert.equal(f.state.uploading.value, false)
  assert.equal(f.calls.length, 1)
})

test('an empty or incomplete success payload cannot claim a whole batch was indexed', async () => {
  for (const data of [undefined, null, [], [row()], { list: [row(), row()] }]) {
    const f = fixture({ uploadDocumentsApi: async () => ({ code: 200, data }) })
    f.state.selectedFiles.value = [file('第一份.md'), file('第二份.txt')]
    await f.state.uploadBatch()
    assert.match(f.state.uploadError.value, /上传结果不完整/)
    assert.equal(f.state.uploadResult.value, '')
    assert.equal(f.state.selectedFiles.value.length, 2)
    assert.equal(f.calls.length, 1)
    assert.equal(f.state.uploading.value, false)
  }
})

test('processing disables the actual row reindex button and its handler sends no confirmation or request', async () => {
  const f = fixture()
  const processing = row('processing')
  f.state.documents.value = [processing]
  await f.state.reindexDocument(processing)
  assert.deepEqual(f.confirmations, [])
  assert.deepEqual(f.calls, [])
  assert.match(await f.html(), /<button[^>]*disabled[^>]*>重新入库/)
})

test('the actual reindex handler locks before confirmation and a rapid second click does not reindex twice', async () => {
  const gate = deferred()
  const f = fixture({}, { confirm: () => gate.promise })
  const document = row()
  f.state.documents.value = [document]
  const first = f.state.reindexDocument(document)
  assert.equal(f.state.pendingIds.has(document.id), true)
  assert.match(await f.html(), /<button[^>]*disabled[^>]*>重新入库/)
  await f.state.reindexDocument(document)
  assert.equal(f.confirmations.length, 1)
  assert.deepEqual(f.calls, [])
  gate.resolve(true)
  await first
  assert.equal(f.calls.filter((call) => call.name === 'reindexDocumentApi').length, 1)
  assert.equal(f.state.pendingIds.has(document.id), false)
  assert.equal(f.state.reindexingId.value, null)
})

test('a reindex business error is displayed without a success claim and releases the row for a retry', async () => {
  const f = fixture({ reindexDocumentApi: async () => ({ code: 503, message: 'P11测试.md：向量化服务暂不可用' }) })
  const document = row()
  await f.state.reindexDocument(document)
  assert.equal(f.state.actionError.value, 'P11测试.md：向量化服务暂不可用')
  assert.deepEqual(f.successes, [])
  assert.equal(f.state.pendingIds.has(101), false)
  assert.equal(f.state.reindexingId.value, null)
  assert.equal(document.status, 'indexed')
  assert.deepEqual(f.calls.map((call) => call.name), ['reindexDocumentApi', 'listDocumentsApi'])
})

test('delete requires a warning about vector cleanup; cancellation sends no delete, then success refreshes', async () => {
  let cancel = true
  const f = fixture({}, { confirm: async () => { if (cancel) throw 'cancel'; return true } })
  await f.state.removeDocument(row())
  assert.match(f.confirmations[0][0], /同时清理.*全部向量/)
  assert.deepEqual(f.calls, [])
  assert.equal(f.state.pendingIds.has(101), false)
  cancel = false
  await f.state.removeDocument(row())
  assert.deepEqual(f.calls.map((call) => call.name), ['deleteDocumentApi', 'listDocumentsApi'])
  assert.match(f.successes[0], /关联向量已清理/)
})

test('preview shows long extracted text as escaped plain text; upload guidance and accepted formats remain visible', async () => {
  const text = '<script>alert("unsafe")</script>\n' + '教学规定 '.repeat(2500)
  const f = fixture({ previewDocumentApi: async () => ({ code: 200, data: { ...row(), extracted_text: text } }) })
  await f.state.openPreview(row())
  assert.equal(f.state.preview.value.extracted_text, text)
  const html = await f.html()
  assert.match(html, /&lt;script&gt;alert\(&quot;unsafe&quot;\)&lt;\/script&gt;/)
  assert.doesNotMatch(html, /<script>/)
  assert.match(html, /accept="\.txt,\.md,\.pdf,\.docx"/)
  for (const step of ['上传', '抽取文本', '分块', '向量化', '可被助手检索']) assert.ok(html.includes(step))
  assert.match(html, /先上传课程资料，学生才能在助手里提问/)
  assert.match(descriptor.styles[0].content, /white-space:\s*pre-wrap/)
  assert.match(descriptor.styles[0].content, /max-height:\s*58vh/)
})

test('a student mounting or directly invoking page handlers sees only the role message and never touches document APIs', async () => {
  const f = fixture({}, { role: 'student' })
  f.state.selectedFiles.value = [file('测试.md')]
  await f.mount()
  await f.state.loadDocuments()
  await f.state.uploadBatch()
  await f.state.openPreview(row())
  await f.state.reindexDocument(row())
  await f.state.removeDocument(row())
  assert.deepEqual(f.calls, [])
  assert.deepEqual(f.confirmations, [])
  const html = await f.html()
  assert.match(html, /无权限访问/)
  assert.doesNotMatch(html, /class="document-page|type="file"|已上传资料|P11测试/)
})
