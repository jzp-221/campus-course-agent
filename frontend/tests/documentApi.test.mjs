import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

// Execute the existing transport with an injected request function. Native
// FormData retains multipart field names and filenames without a browser DOM.
function fixture(request) {
  const source = readFileSync(new URL('../src/api/document.js', import.meta.url), 'utf8')
    .replace(/^import request from [^\n]+\n/m, '')
    .replace(/export function/g, 'function')
  return new Function('request', `${source}\nreturn {
    listDocumentsApi, uploadDocumentsApi, previewDocumentApi,
    reindexDocumentApi, deleteDocumentApi
  }`)(request)
}

test('document upload sends several files in one request using repeated files fields', async () => {
  const calls = []
  const response = { code: 200, data: [{ id: 101 }, { id: 102 }] }
  const api = fixture(async (options) => { calls.push(options); return response })
  const files = [new File(['第一份'], '课程规定.md'), new File(['第二份'], '教学安排.txt')]
  assert.equal(await api.uploadDocumentsApi(files), response)
  assert.equal(calls.length, 1)
  const [request] = calls
  assert.equal(request.url, '/api/documents/upload')
  assert.equal(request.method, 'post')
  assert.ok(request.data instanceof FormData)
  assert.deepEqual([...request.data.keys()], ['files', 'files'])
  assert.deepEqual(request.data.getAll('files').map((file) => file.name), files.map((file) => file.name))
  assert.deepEqual(await Promise.all(request.data.getAll('files').map((file) => file.text())), ['第一份', '第二份'])
})

test('multipart boundary is left to Axios/browser and the upload progress callback is forwarded', async () => {
  let options
  const api = fixture(async (request) => { options = request; return { code: 200, data: [] } })
  const updates = []
  const progress = (event) => updates.push(event.loaded)
  await api.uploadDocumentsApi([new File(['x'], 'a.md')], { onProgress: progress })
  assert.equal(Object.hasOwn(options, 'headers'), false)
  assert.equal(options.timeout, 120000)
  assert.equal(options.onUploadProgress, progress)
  options.onUploadProgress({ loaded: 1, total: 2 })
  assert.deepEqual(updates, [1])
})

test('all five document transports retain their endpoint, method, pagination and response contracts', async () => {
  const calls = []
  const responses = [
    { code: 200, data: { list: [{ id: 17, status: 'indexed' }], total: 4 } },
    { code: 200, data: { id: 101, extracted_text: '抽取原文' } },
    { code: 200, data: { id: 101, status: 'indexed', chunk_count: 2 } },
    { code: 200, data: null }
  ]
  const api = fixture(async (options) => { calls.push(options); return responses[calls.length - 1] })
  assert.equal(await api.listDocumentsApi({ page: 2, page_size: 10 }), responses[0])
  assert.equal(await api.previewDocumentApi(101), responses[1])
  assert.equal(await api.reindexDocumentApi(101), responses[2])
  assert.equal(await api.deleteDocumentApi(101), responses[3])
  assert.deepEqual(calls, [
    { url: '/api/documents', method: 'get', params: { page: 2, page_size: 10 } },
    { url: '/api/documents/101/preview', method: 'get' },
    { url: '/api/documents/101/reindex', method: 'post', timeout: 120000 },
    { url: '/api/documents/101', method: 'delete' }
  ])
})

test('the original upload transport preserves a rejected business/network error', async () => {
  const failure = new Error('坏图片.png：仅支持 .txt、.md、.pdf、.docx 文件')
  const api = fixture(async () => { throw failure })
  await assert.rejects(api.uploadDocumentsApi([new File(['x'], '坏图片.png')]), (error) => error === failure)
})

function routerFixture() {
  let routes, guard
  const auth = { token: null, user: null }
  const router = { beforeEach(handler) { guard = handler } }
  const source = readFileSync(new URL('../src/router/index.js', import.meta.url), 'utf8')
    .replace(/^import .*\r?\n/gm, '')
    .replace(/import\.meta\.env\.BASE_URL/g, "'/'")
    .replace(/^export default router\r?$/m, '')
  new Function('getToken', 'getUserInfo', 'createRouter', 'createWebHistory', source)(
    () => auth.token, () => auth.user,
    (options) => { routes = options.routes; return router }, () => ({})
  )
  return { auth, routes, visit: (to) => guard({ meta: {}, ...to }, {}) }
}

test('the document route requires an administrator; direct student visits redirect before loading the page', () => {
  const { auth, routes, visit } = routerFixture()
  const document = routes.find((route) => route.path === '/manager').children
    .find((route) => route.path === 'document')
  assert.ok(document)
  assert.equal(document.meta.requiresAdmin, true)
  const target = { path: '/manager/document', meta: document.meta }
  assert.equal(visit(target), '/login')
  auth.token = 'local-test-token'
  auth.user = { id: 2, role: 'student' }
  assert.equal(visit(target), '/manager/home')
  auth.user = null
  assert.equal(visit(target), '/manager/home')
  auth.user = { id: 1, role: 'admin' }
  assert.equal(visit(target), true)
  assert.equal(visit({ path: '/manager/schedule-week' }), true)
  auth.token = null
  assert.equal(visit({ path: '/login' }), true)
  assert.equal(visit({ path: '/register' }), true)
})
