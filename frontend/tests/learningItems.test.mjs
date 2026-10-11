import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import * as items from '../src/utils/learningItems.js'

const now = new Date(2026, 9, 31, 12)
const row = (id, due_at, extras = {}) => ({ id, due_at, status: 'pending', type: 'assignment', ...extras })

test('local ISO parsing preserves wall clock and rejects invalid dates and timezone conversions', () => {
  const date = items.parseLocalDateTime('2026-11-01T18:30:00')
  assert.deepEqual([date.getFullYear(), date.getMonth(), date.getDate(), date.getHours()], [2026, 10, 1, 18])
  for (const value of [null, '', '2026-13-99T10:00:00', '2026-02-30', '2026-10-31T24:00:00', '2026-10-31T12:00:00Z']) {
    assert.equal(items.parseLocalDateTime(value), null)
  }
  assert.equal(items.formatLocalDateTime('2026-11-01T18:30:00'), '2026-11-01 18:30')
  assert.equal(items.formatLocalDateTime(null), '未设截止')
})

test('remaining calendar days handle month boundaries, overdue and today', () => {
  assert.equal(items.remainingDays('2026-11-01T01:00:00', now), 1)
  assert.equal(items.remainingDays('2026-10-30T18:00:00', now), -1)
  assert.equal(items.remainingDays('2026-10-31T18:00:00', now), 0)
  assert.equal(items.remainingDays(null, now), null)
  assert.equal(items.dueLabel(row(1, '2026-10-31T11:00:00'), now), '今天已逾期')
  assert.equal(items.dueLabel(row(1, '2026-10-31T18:00:00'), now), '今天到期')
  assert.equal(items.dueLabel(row(1, '2026-10-30T18:00:00'), now), '已逾期 1 天')
})

test('deadline states separate done, overdue, 72-hour boundary and no deadline', () => {
  assert.equal(items.dueState(row(1, '2026-10-30T18:00:00', { status: 'done' }), now), 'done')
  assert.equal(items.dueState(row(1, '2026-10-31T11:59:59'), now), 'overdue')
  assert.equal(items.dueState(row(1, '2026-11-03T12:00:00'), now), 'soon')
  assert.equal(items.dueState(row(1, '2026-11-03T12:00:01'), now), 'normal')
  assert.equal(items.dueState(row(1, null), now), 'unset')
})

test('type, status and deadline filters combine without counting completed items as overdue', () => {
  const rows = [row(1, '2026-10-30T18:00:00'), row(2, '2026-11-01T18:00:00', { type: 'exam' }),
    row(3, '2026-11-05T18:00:00'), row(4, '2026-10-30T18:00:00', { status: 'done' })]
  const ids = options => items.filterAssignments(rows, options, now).map(item => item.id)
  assert.deepEqual(ids({ type: 'exam', status: 'pending', dueRange: '3' }), [2])
  assert.deepEqual(ids({ dueRange: '7' }), [2, 3])
  assert.deepEqual(ids({ dueRange: 'overdue' }), [1])
  assert.deepEqual(ids({ status: 'done' }), [4])
  assert.deepEqual(ids({ type: 'all', status: 'all', dueRange: 'all' }), [1, 2, 3, 4])
})

test('holiday deadline comes from server periods and takes precedence over weekends', () => {
  const calendar = { holidays: [{ name: '元旦', start_date: '2027-01-01', end_date: '2027-01-03' }], makeup_workdays: [] }
  assert.equal(items.deadlineNotice('2027-01-02T18:00:00', calendar), '该截止日在放假期间（元旦 1/1-1/3）')
  assert.equal(items.deadlineNotice('2027-01-03T18:00:00', calendar), '该截止日在放假期间（元旦 1/1-1/3）')
  // Different server dates are honored without a frontend calendar change.
  assert.equal(items.deadlineNotice('2026-11-02T18:00:00', { holidays: [
    { name: '测试假期', start_date: '2026-11-02', end_date: '2026-11-02' }
  ] }), '该截止日在放假期间（测试假期 11/2-11/2）')
})

test('ordinary weekend, makeup workday and unavailable calendar have distinct deadline notices', () => {
  const calendar = { holidays: [], makeup_workdays: ['2026-10-10'] }
  assert.equal(items.deadlineNotice('2026-10-11T18:00:00', calendar), '周末不放假，截止日照常')
  assert.equal(items.deadlineNotice('2026-10-10T18:00:00', calendar), '调休上班日，截止日照常')
  assert.equal(items.deadlineNotice('2026-10-12T18:00:00', calendar), '')
  assert.equal(items.deadlineNotice('2026-10-11T18:00:00', null), '')
})

test('pending badge counts all pending pages including overdue and sorts due dates without mutating records', () => {
  const rows = [row(3, null), row(1, '2026-11-03T12:00:00'), row(2, '2026-10-30T12:00:00', { status: 'done' })]
  assert.equal(items.pendingCount(rows), 2)
  assert.deepEqual(items.sortByDue(rows).map(item => item.id), [2, 1, 3])
  assert.deepEqual(rows.map(item => item.id), [3, 1, 2])
})

test('all-page loading includes pending records after page one and preserves course filter', async () => {
  const calls = []
  const records = Array.from({ length: 205 }, (_, index) => row(index + 1, '2026-11-01T18:00:00'))
  const rows = await items.fetchAllPages(async params => {
    calls.push(params)
    return { code: 200, data: { list: records.slice((params.page - 1) * 100, params.page * 100), total: 205 } }
  }, { course_id: 2 })
  assert.equal(items.pendingCount(rows), 205)
  assert.deepEqual(calls, [1, 2, 3].map(page => ({ course_id: 2, page, page_size: 100 })))
})

test('incomplete, changing or failed lists reject rather than showing a misleading zero reminder', async () => {
  await assert.rejects(items.fetchAllPages(async () => ({ code: 503, message: '服务暂不可用' })), /服务暂不可用/)
  await assert.rejects(items.fetchAllPages(async params => ({ code: 200, data: {
    list: params.page === 1 ? Array.from({ length: 100 }, (_, i) => row(i + 1, null)) : [], total: 101
  } })), /未完整/)
  await assert.rejects(items.fetchAllPages(async params => ({ code: 200, data: {
    list: [row(params.page, null)], total: params.page === 1 ? 101 : 102
  } })), /发生变化/)
  assert.deepEqual(await items.fetchAllPages(async () => ({ code: 200, data: { list: [], total: 0 } })), [])
})

function inboxFixture(load) {
  const userInfo = Vue.ref({ id: 2, role: 'student' })
  let persisted = userInfo.value
  const source = readFileSync(new URL('../src/stores/learningInbox.js', import.meta.url), 'utf8')
  const bindings = { computed: Vue.computed, ref: Vue.ref, watch: Vue.watch, listAssignmentsApi: load,
    useUser: () => ({ userInfo }), getUserInfo: () => persisted, fetchAllPages: items.fetchAllPages, countPending: items.pendingCount }
  const executable = source.replace(/^import .*\n/gm, '').replace('export function useLearningInbox', 'function useLearningInbox')
  const use = new Function(...Object.keys(bindings), `${executable}\nreturn useLearningInbox`)(...Object.values(bindings))
  const scope = Vue.effectScope()
  const inbox = scope.run(use)
  return { inbox, switchUser(user) { persisted = user; userInfo.value = user }, logout() { persisted = null }, stop: () => scope.stop() }
}
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes }); return { promise, resolve } }
const response = list => ({ code: 200, data: { list, total: list.length } })

test('shared inbox caches one own list and computes a reactive pending badge', async () => {
  let calls = 0
  const f = inboxFixture(async () => { calls++; return response([row(1, null), row(2, null, { status: 'done' })]) })
  await f.inbox.refresh()
  await f.inbox.refresh()
  assert.equal(calls, 1)
  assert.equal(f.inbox.pendingCount.value, 1)
  assert.equal(f.inbox.loaded.value, true)
  f.stop()
})

test('account switching clears badge immediately and discards the previous user response', async () => {
  const old = deferred(), next = deferred()
  let calls = 0
  const f = inboxFixture(() => (++calls === 1 ? old.promise : next.promise))
  const requestA = f.inbox.refresh()
  f.switchUser({ id: 3, role: 'student' })
  assert.equal(f.inbox.pendingCount.value, 0)
  const requestB = f.inbox.refresh()
  next.resolve(response([row(3, null)])); await requestB
  old.resolve(response([row(1, null), row(2, null)])); await requestA
  assert.deepEqual(f.inbox.assignments.value.map(item => item.id), [3])
  assert.equal(f.inbox.pendingCount.value, 1)
  f.stop()
})

test('post-write forced refresh waits for the old GET then obtains committed completion state', async () => {
  const old = deferred()
  let calls = 0
  const f = inboxFixture(() => (++calls === 1 ? old.promise : Promise.resolve(response([row(1, null, { status: 'done' })]))))
  const initial = f.inbox.refresh()
  const afterWrite = f.inbox.refresh(true)
  old.resolve(response([row(1, null)])); await initial; await afterWrite
  assert.equal(calls, 2)
  assert.equal(f.inbox.pendingCount.value, 0)
  f.stop()
})

test('logout during a GET discards data and list errors remain visible for retry', async () => {
  const gate = deferred()
  const f = inboxFixture(() => gate.promise)
  const request = f.inbox.refresh()
  f.logout()
  gate.resolve(response([row(1, null)])); await request
  assert.equal(f.inbox.loaded.value, false)
  assert.deepEqual(f.inbox.assignments.value, [])
  f.stop()
  const failed = inboxFixture(async () => ({ code: 400, message: '列表错误' }))
  await assert.rejects(failed.inbox.refresh(), /列表错误/)
  assert.equal(failed.inbox.error.value, '列表错误')
  assert.equal(failed.inbox.loading.value, false)
  failed.stop()
})
