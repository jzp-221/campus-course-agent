import test from 'node:test'
import assert from 'node:assert/strict'
import { createSSEParser } from '../src/utils/sse.js'

test('retrieval status and query-only tool retain fixed SSE fields', () => {
  const seen = []
  const parser = createSSEParser((name, data) => seen.push({ name, data }))
  const calls = [{ name: 'search_course_docs', args: { query: '实验课提前多久到达？' } }]
  for (const [name, data] of [
    ['status', { stage: 'retrieving' }], ['tool_start', calls[0]],
    ['tool_end', { name: 'search_course_docs', ok: true }],
    ['token', { text: '提前10分钟。来源：实验课规定.txt' }],
    ['done', { answer: '提前10分钟。来源：实验课规定.txt', tool_calls: calls }]
  ]) parser.feed(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`)
  assert.equal(seen[0].data.stage, 'retrieving')
  assert.deepEqual(seen.at(-1).data.tool_calls, calls)
})

test('retrieval rejects identity, unknown, empty and excessive query parameters', () => {
  for (const args of [{}, { query: '' }, { query: '  ' }, { query: 1 },
    { query: '问'.repeat(2001) }, { query: '资料', user_id: 7 },
    { query: '资料', document_id: 4 }, { query: '资料', plan_id: 2 }]) {
    for (const [name, data] of [
      ['tool_start', { name: 'search_course_docs', args }],
      ['done', { answer: '资料回答', tool_calls: [{ name: 'search_course_docs', args }] }]
    ]) assert.throws(() => createSSEParser(() => {}).feed(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`))
  }
  assert.doesNotThrow(() => createSSEParser(() => {}).feed(
    `event: tool_start\ndata: ${JSON.stringify({ name: 'search_course_docs', args: { query: '💡'.repeat(2000) } })}\n\n`))
})

test('retrieval failure is displayed as failure rather than empty success', () => {
  const seen = []
  const parser = createSSEParser((name, data) => seen.push({ name, data }))
  parser.feed('event: tool_end\ndata: {"name":"search_course_docs","ok":false}\n\n' +
    'event: error\ndata: {"code":503,"message":"当前无法查询资料（检索失败：未配置 EMBEDDING_API_KEY）"}\n\n')
  assert.equal(seen[0].data.ok, false)
  assert.equal(seen[1].data.code, 503)
  assert.match(seen[1].data.message, /当前无法查询资料/)
  assert.doesNotMatch(seen[1].data.message, /没有相关内容/)
})
