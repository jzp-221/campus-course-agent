import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildTitle,
  createSessionId,
  historyFromMessages,
  loadSessions,
  MAX_SESSIONS,
  MAX_STORED_MESSAGES,
  MAX_STORED_CONTENT,
  removeSessions,
  saveSessions,
  storageKey,
  toStoredMessage,
  trimSessions
} from '../src/utils/chatSessions.js'

/** 最小 localStorage 替身，便于断言写入内容与配额异常 */
function memoryStorage(initial = {}) {
  const map = new Map(Object.entries(initial))
  return {
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => map.set(key, String(value)),
    removeItem: (key) => map.delete(key),
    _dump: () => Object.fromEntries(map)
  }
}

test('会话按用户隔离，不同账号互不可见', () => {
  assert.equal(storageKey(2), 'campus_assistant_sessions_2')
  assert.equal(storageKey(3), 'campus_assistant_sessions_3')
  assert.notEqual(storageKey(2), storageKey(3))
  assert.equal(storageKey(undefined), 'campus_assistant_sessions_anonymous')

  const storage = memoryStorage()
  saveSessions(storage, 2, [{ id: 'a', title: 'aaa 的对话', messages: [], updatedAt: 1 }])
  assert.equal(loadSessions(storage, 2).length, 1)
  assert.equal(loadSessions(storage, 3).length, 0, 'bbb 不应看到 aaa 的会话')
})

test('标题取首条提问并截断到限定长度', () => {
  assert.equal(buildTitle('我明天有什么课？'), '我明天有什么课？')
  assert.equal(buildTitle('  多个   空格\u3000折叠  '), '多个 空格 折叠')
  assert.equal(buildTitle(''), '新对话')
  assert.equal(buildTitle(null), '新对话')
  const long = '这是一句非常长的提问'.repeat(6)
  const title = buildTitle(long, 10)
  assert.equal(title.length, 11, '截断后应带一个省略号')
  assert.ok(title.endsWith('…'))
})

test('落盘消息只保留角色与正文，并截断超长正文', () => {
  assert.deepEqual(toStoredMessage({ role: 'user', content: '你好', html: '<b>x</b>', state: 'done' }),
    { role: 'user', content: '你好' })
  assert.deepEqual(toStoredMessage({ role: 'system', content: '越权' }), { role: 'assistant', content: '越权' })
  const huge = 'a'.repeat(MAX_STORED_CONTENT + 500)
  assert.equal(toStoredMessage({ role: 'assistant', content: huge }).content.length, MAX_STORED_CONTENT)
})

test('裁剪会限制会话总数与单会话消息条数，并按更新时间排序', () => {
  const sessions = Array.from({ length: MAX_SESSIONS + 5 }, (_, index) => ({
    id: `s${index}`,
    title: `标题${index}`,
    updatedAt: index,
    messages: Array.from({ length: MAX_STORED_MESSAGES + 10 }, (_, i) => ({
      role: 'user',
      content: `第${i}条`
    }))
  }))
  const trimmed = trimSessions(sessions)
  assert.equal(trimmed.length, MAX_SESSIONS)
  assert.equal(trimmed[0].id, `s${MAX_SESSIONS + 4}`, '最新的会话应排在最前')
  assert.equal(trimmed[0].messages.length, MAX_STORED_MESSAGES)
  assert.equal(trimmed[0].messages.at(-1).content, `第${MAX_STORED_MESSAGES + 9}条`, '应保留最近的消息')
})

test('裁剪会丢弃结构异常的数据而不是抛错', () => {
  const result = trimSessions([
    null,
    'not-an-object',
    { title: '缺少 id' },
    { id: 'ok', title: 123, updatedAt: 'bad', messages: 'not-array' }
  ])
  assert.equal(result.length, 1)
  assert.equal(result[0].id, 'ok')
  assert.equal(result[0].title, '新对话')
  assert.deepEqual(result[0].messages, [])
})

test('损坏的 JSON 或不可用存储返回空会话，不影响页面', () => {
  assert.deepEqual(loadSessions(memoryStorage({ campus_assistant_sessions_2: '{坏数据' }), 2), [])
  assert.deepEqual(loadSessions(null, 2), [])
  assert.deepEqual(loadSessions(memoryStorage(), 2), [])
  assert.equal(saveSessions(null, 2, []), false)
  assert.equal(removeSessions(null, 2), false)
})

test('写入失败（如超出配额）不抛异常，只返回 false', () => {
  const storage = memoryStorage()
  storage.setItem = () => {
    throw new Error('QuotaExceededError')
  }
  assert.equal(saveSessions(storage, 2, [{ id: 'a', title: 't', messages: [] }]), false)
})

test('写盘内容可完整读回，且不携带渲染用字段', () => {
  const storage = memoryStorage()
  saveSessions(storage, 2, [
    {
      id: 's1',
      title: '明天的课',
      updatedAt: 1700000000000,
      messages: [{ role: 'user', content: '我明天有什么课？', html: '<p>x</p>' }]
    }
  ])
  const [session] = loadSessions(storage, 2)
  assert.equal(session.id, 's1')
  assert.equal(session.title, '明天的课')
  assert.deepEqual(session.messages, [{ role: 'user', content: '我明天有什么课？' }])
  assert.ok(!('html' in session.messages[0]))
})

test('回传后端的历史只含问答文字，且截取最近若干条', () => {
  const messages = [
    { role: 'user', content: '问一' },
    { role: 'assistant', content: '答一' },
    { role: 'user', content: '' },
    { role: 'user', content: '问二', html: '<p>忽略</p>' }
  ]
  assert.deepEqual(historyFromMessages(messages), [
    { role: 'user', content: '问一' },
    { role: 'assistant', content: '答一' },
    { role: 'user', content: '问二' }
  ])
  const many = Array.from({ length: 80 }, (_, i) => ({ role: 'user', content: `第${i}条` }))
  const limited = historyFromMessages(many, 50)
  assert.equal(limited.length, 50)
  assert.equal(limited.at(-1).content, '第79条')
  assert.deepEqual(historyFromMessages(null), [])
})

test('会话 id 不依赖后端且重复调用不冲突', () => {
  const ids = new Set()
  for (let i = 0; i < 200; i += 1) ids.add(createSessionId(1700000000000 + i, Math.random))
  assert.equal(ids.size, 200)
  assert.ok([...ids].every((id) => id.startsWith('s-')))
})

test('删除会话键后读回为空', () => {
  const storage = memoryStorage()
  saveSessions(storage, 2, [{ id: 'a', title: 't', messages: [] }])
  assert.equal(loadSessions(storage, 2).length, 1)
  assert.equal(removeSessions(storage, 2), true)
  assert.deepEqual(loadSessions(storage, 2), [])
})
