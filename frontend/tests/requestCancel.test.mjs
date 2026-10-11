import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'

test('the real Axios response interceptor rejects cancellations silently and still reports network errors', async () => {
  const messages = []
  let errorHandler
  const service = {
    interceptors: {
      request: { use() {} },
      response: { use(_, reject) { errorHandler = reject } }
    }
  }
  const axios = { create: () => service, isCancel: (error) => error.__CANCEL__ === true }
  const source = readFileSync(new URL('../src/utils/request.js', import.meta.url), 'utf8')
    .replace(/^import .*\r?\n/gm, '')
    .replace(/import\.meta\.env\.VITE_API_BASE_URL/g, 'undefined')
    .replace(/^export default service\r?$/m, '')
  runInNewContext(source, {
    axios, getToken: () => null,
    logout: () => { throw new Error('Cancellation must not log the user out') },
    router: { push: () => { throw new Error('Cancellation must not redirect') } },
    ElMessage: { error: (message) => messages.push(message) }
  })
  const cancellation = { __CANCEL__: true, message: 'canceled' }
  await assert.rejects(errorHandler(cancellation), (error) => error === cancellation)
  assert.deepEqual(messages, [])
  const outage = new Error('Network Error')
  await assert.rejects(errorHandler(outage), (error) => error === outage)
  assert.deepEqual(messages, ['网络异常，请检查后端服务'])
})
