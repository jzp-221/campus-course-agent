import test from 'node:test'
import assert from 'node:assert/strict'
import { createSSEParser } from '../src/utils/sse.js'

test('UTF-8 / CRLF frames split at every byte', () => {
  const events = []
  const parser = createSSEParser((event, data) => events.push({ event, data }))
  const bytes = new TextEncoder().encode('event: token\r\ndata: {"text":"明天有课🙂"}\r\n\r\n')
  const decoder = new TextDecoder()
  for (const byte of bytes) parser.feed(decoder.decode(new Uint8Array([byte]), { stream: true }))
  parser.feed(decoder.decode())
  parser.finish()
  assert.deepEqual(events, [{ event: 'token', data: { text: '明天有课🙂' } }])
})

test('heartbeat comments and many frames in one chunk', () => {
  const events = []
  const parser = createSSEParser((event, data) => events.push(event))
  parser.feed(': ping\n\nevent: status\ndata: {"stage":"thinking"}\n\nevent: token\ndata: {"text":"hi"}\n\n')
  parser.finish()
  assert.deepEqual(events, ['status', 'token'])
})

test('multi-line data is joined', () => {
  let result
  const parser = createSSEParser((event, data) => { result = data })
  parser.feed('event: error\ndata: {"code":500,\ndata: "message":"failed"}\n\n')
  assert.deepEqual(result, { code: 500, message: 'failed' })
})

test('incomplete frame fails at EOF', () => {
  const parser = createSSEParser(() => {})
  parser.feed('event: token\ndata: {"text":"partial"}')
  assert.throws(() => parser.finish(), /未接收完整/)
})

test('unknown names, unsafe tool args and wrong fields are rejected', () => {
  for (const frame of [
    'event: something\ndata: {}\n\n',
    'event: token\ndata: {"text":"hi","extra":1}\n\n',
    'event: tool_start\ndata: {"name":"get_my_schedules","args":{"user_id":99}}\n\n',
    'event: done\ndata: {"answer":"","tool_calls":[]}\n\n'
  ]) assert.throws(() => createSSEParser(() => {}).feed(frame))
})

test('all six declared types parse with their fixed fields', () => {
  const examples = {
    status: { stage: 'querying' }, tool_start: { name: 'get_my_schedules', args: { date: '2026-10-10' } },
    tool_end: { name: 'get_my_schedules', ok: true }, token: { text: 'answer' },
    done: { answer: 'answer', tool_calls: [{ name: 'get_my_schedules', args: {} }] },
    error: { code: 429, message: 'limit' }
  }
  const events = []
  const parser = createSSEParser((name) => events.push(name))
  for (const [name, data] of Object.entries(examples)) parser.feed(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`)
  assert.deepEqual(events, Object.keys(examples))
})

test('saving status and study plan tool use the same fixed SSE fields', () => {
  const events = []
  const parser = createSSEParser((event, data) => events.push({ event, data }))
  const calls = [{ name: 'save_study_plan', args: { content: '明天上午复习英语，下午完成数学练习。' } }]
  for (const [event, data] of [
    ['status', { stage: 'saving' }], ['tool_start', calls[0]],
    ['tool_end', { name: 'save_study_plan', ok: true }],
    ['token', { text: '已保存学习计划' }],
    ['done', { answer: '已保存学习计划', tool_calls: calls }]
  ]) parser.feed(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
  assert.equal(events[1].data.name, 'save_study_plan')
  assert.equal(events[2].data.ok, true)
  assert.deepEqual(events[4].data.tool_calls, calls)
  const failed = []
  const errorParser = createSSEParser((event, data) => failed.push({ event, data }))
  errorParser.feed('event: tool_end\ndata: {"name":"save_study_plan","ok":false}\n\n' +
    'event: error\ndata: {"code":502,"message":"学习计划保存失败"}\n\n')
  assert.equal(failed[0].data.ok, false)
  assert.equal(failed[1].data.message, '学习计划保存失败')
})

test('save arguments reject identity, plan IDs, empty content and excessive content', () => {
  for (const args of [
    {}, { content: '' }, { content: '   ' }, { content: 12 },
    { content: '学习安排', user_id: 1 }, { content: '学习安排', plan_id: 1 },
    { content: '学'.repeat(2001) }
  ]) {
    for (const payload of [
      { event: 'tool_start', data: { name: 'save_study_plan', args } },
      { event: 'done', data: { answer: '保存结果', tool_calls: [{ name: 'save_study_plan', args }] } }
    ]) assert.throws(() => createSSEParser(() => {}).feed(
      `event: ${payload.event}\ndata: ${JSON.stringify(payload.data)}\n\n`))
  }
  // 与后端 Python len 一致，按 Unicode 字符计数，不能把一个 emoji 算成两字。
  assert.doesNotThrow(() => createSSEParser(() => {}).feed(
    `event: tool_start\ndata: ${JSON.stringify({ name: 'save_study_plan', args: { content: '💡'.repeat(2000) } })}\n\n`))
  assert.throws(() => createSSEParser(() => {}).feed(
    'event: tool_start\ndata: {"name":"other_tool","args":{}}\n\n'))
})
