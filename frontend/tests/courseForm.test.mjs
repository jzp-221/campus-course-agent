import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'
import { reactive, ref } from 'vue'
import AsyncValidator from 'async-validator'
import { buildCoursePayload } from '../src/utils/courseForm.js'

const Schema = AsyncValidator.default ?? AsyncValidator
const source = readFileSync(new URL('../src/views/Course.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source, { filename: 'Course.vue' })
const compiled = compileScript(descriptor, { id: 'course-form-test' }).content

// Execute the real SFC's save handler with an API double, without mounting a DOM.
function courseFixture() {
  const requests = []
  const api = async (method, id, payload) => {
    requests.push({ method, id, body: JSON.parse(JSON.stringify(payload)) })
    return { code: 200 }
  }
  const bindings = {
    Search: {}, Plus: {}, reactive, ref, onMounted: () => {},
    ElMessage: { success: () => {} }, ElMessageBox: {},
    createCourseApi: (payload) => api('POST', undefined, payload),
    updateCourseApi: (id, payload) => api('PUT', id, payload),
    deleteCourseApi: () => { throw new Error('Unexpected delete') },
    listCoursesApi: async () => ({ code: 200, data: [] }),
    buildCoursePayload
  }
  const script = compiled.replace(/^import .* from ['"].+['"]\n/gm, '')
    .replace(/^export default/m, 'return')
  const component = new Function(...Object.keys(bindings), script)(...Object.values(bindings))
  const state = component.setup({}, { expose: () => {} })
  state.formRef.value = { validate: async () => {
    await new Schema(state.rules).validate(state.form)
    return true
  } }
  return { state, requests }
}

test('JSON payload preserves an explicit empty description and code for backend validation', () => {
  const body = JSON.parse(JSON.stringify(buildCoursePayload({
    id: 4, name: '大学英语', code: '', description: '', status: 0
  })))
  assert.deepEqual(body, { name: '大学英语', code: '', description: '', status: 0 })
})

test('a name-only edit omits description and code; undefined is also omitted', () => {
  const body = JSON.parse(JSON.stringify(buildCoursePayload({ name: '新名称', description: undefined })))
  assert.deepEqual(body, { name: '新名称' })
  assert.equal(Object.hasOwn(body, 'description'), false)
  assert.equal(Object.hasOwn(body, 'code'), false)
})

test('explicit null retains its value instead of silently becoming an empty string', () => {
  assert.deepEqual(buildCoursePayload({ description: null }), { description: null })
})

test('the actual edit dialog submits a cleared description as an empty string', async () => {
  const { state, requests } = courseFixture()
  state.handleEdit({ id: 4, name: '大学英语', code: 'ENG101', description: '旧简介', status: 1 })
  state.form.description = ''
  await state.handleSave()
  assert.deepEqual(requests, [{
    method: 'PUT', id: 4,
    body: { name: '大学英语', code: 'ENG101', description: '', status: 1 }
  }])
  assert.equal(state.dialogVisible.value, false)
  assert.equal(state.formLoading.value, false)
})

test('the actual create dialog also preserves an empty optional description', async () => {
  const { state, requests } = courseFixture()
  state.handleCreate()
  Object.assign(state.form, { name: '大学英语', code: 'ENG101' })
  await state.handleSave()
  assert.equal(requests.length, 1)
  assert.equal(requests[0].method, 'POST')
  assert.equal(requests[0].body.description, '')
})

test('required course code rejects empty and whitespace-only edits before submitting', async () => {
  for (const code of ['', '   ', '\t\n']) {
    const { state, requests } = courseFixture()
    state.handleEdit({ id: 4, name: '大学英语', code: 'ENG101', description: '', status: 1 })
    state.form.code = code
    await state.handleSave()
    assert.deepEqual(requests, [])
    assert.equal(state.dialogVisible.value, true)
    assert.equal(state.formLoading.value, false)
  }
  assert.match(descriptor.template.content, /placeholder="必填，唯一"/)
})

test('required course name validation still prevents an invalid submission', async () => {
  const { state, requests } = courseFixture()
  state.handleCreate()
  state.form.code = 'ENG101'
  await state.handleSave()
  assert.deepEqual(requests, [])
  assert.equal(state.dialogVisible.value, true)
})
