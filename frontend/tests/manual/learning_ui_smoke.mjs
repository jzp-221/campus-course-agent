/**
 * P13-Step2 真实 HTTP 验收。运行：node tests/manual/learning_ui_smoke.mjs
 * API_BASE_URL 可覆盖 http://127.0.0.1:8000；复用已运行 FastAPI/MCP，不启停服务。
 * 只新建 UUID 标识的临时事项/任务并回收；不修改原有 5 条任务、文本计划或课表。
 */
import assert from 'node:assert/strict'
import { createHash, randomUUID } from 'node:crypto'
import { deadlineNotice, dueState, parseLocalDateTime } from '../../src/utils/learningItems.js'

const base = (process.env.API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
const marker = `P13-S2-${randomUUID()}`
const sessions = {}
const temporary = { assignments: new Set(), 'plan-tasks': new Set() }
const snapshots = {}
const evidence = { marker, api: base, operations: [] }
let interrupted = false
let failed = false

process.on('SIGINT', () => { interrupted = true })
process.on('SIGTERM', () => { interrupted = true })

function check(condition, message) {
  if (!condition) throw new Error(message)
}

function log(message) { console.log(`[PASS] ${message}`) }
function digest(rows) { return createHash('sha256').update(JSON.stringify(rows)).digest('hex') }
function sortedRows(rows) { return [...rows].sort((left, right) => left.id - right.id) }

async function request(username, method, path, body, { cleanup = false } = {}) {
  if (interrupted && !cleanup) throw new Error('收到中断信号，停止验证并清理临时数据')
  let response
  try {
    response = await fetch(base + path, {
      method,
      headers: {
        ...(sessions[username] ? { Authorization: `Bearer ${sessions[username].token}` } : {}),
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {})
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      signal: AbortSignal.timeout(path.endsWith('/docs') ? 70000 : 30000)
    })
  } catch {
    throw new Error(`${method} ${path} 请求失败或超时；请确认 FastAPI 已运行，课程检索还需要 MCP`)
  }
  let result
  try { result = await response.json() } catch {
    throw new Error(`${method} ${path} 返回非 JSON，HTTP ${response.status}`)
  }
  check(result && typeof result === 'object', `${method} ${path} 响应结构错误`)
  return { http: response.status, ...result }
}

async function success(username, method, path, body, options) {
  const response = await request(username, method, path, body, options)
  check(response.http === 200 && response.code === 200,
    `${method} ${path} 失败：HTTP ${response.http}，业务码 ${response.code}，${response.message || '请求失败'}`)
  return response.data
}

async function login(username, password) {
  const data = await success(username, 'POST', '/api/auth/login', { username, password })
  check(data?.user?.username === username && typeof data.token === 'string', `${username} 登录身份或令牌格式错误`)
  sessions[username] = { token: data.token, id: data.user.id, role: data.user.role }
  // Do not log the response, request body, JWT or credentials.
}

async function listAll(username, resource, extra = {}, { cleanup = false } = {}) {
  const rows = []
  let total = null
  for (let page = 1; ; page++) {
    const query = new URLSearchParams({ ...extra, page: String(page), page_size: '100' })
    const data = await success(username, 'GET', `/api/${resource}?${query}`, undefined, { cleanup })
    check(Array.isArray(data?.list) && Number.isInteger(data.total) && data.total >= 0,
      `${username} ${resource} 分页结构错误`)
    if (total === null) total = data.total
    check(data.total === total, `${username} ${resource} 验证期间总数变化，请停止并发写入后重跑`)
    rows.push(...data.list)
    if (rows.length >= total) {
      check(rows.length === total && new Set(rows.map(row => row.id)).size === total,
        `${username} ${resource} 分页记录不完整或重复`)
      return sortedRows(rows)
    }
    check(data.list.length > 0, `${username} ${resource} 分页提前结束`)
  }
}

function shiftLocal(value, days) {
  const date = parseLocalDateTime(value)
  check(date, '验收日期格式不合法')
  date.setDate(date.getDate() + days)
  const pad = number => String(number).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
    + `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

async function captureBaseline() {
  for (const username of ['aaa', 'bbb', 'admin']) {
    snapshots[username] = {}
    for (const resource of ['assignments', 'plan-tasks', 'study-plans']) {
      snapshots[username][resource] = await listAll(username, resource)
      if (resource !== 'study-plans') {
        check(snapshots[username][resource].every(row => row.user_id === sessions[username].id),
          `${username} ${resource} 包含其他用户记录`)
      }
    }
  }
  evidence.before = Object.fromEntries(Object.entries(snapshots).map(([username, resources]) =>
    [username, Object.fromEntries(Object.entries(resources).map(([name, rows]) => [name, { count: rows.length, sha256: digest(rows) }]))]))
  log('已读取 aaa/bbb/admin 的全部原有事项、任务和文本计划；仅保存数量与指纹')
}

async function verifyContext() {
  const context = await success('aaa', 'GET', '/api/learning-context')
  check(context.courses?.length === 22, '学习上下文课程数不是基线 22')
  check(Array.isArray(context.holidays) && Array.isArray(context.makeup_workdays), '后端假期与调休日数据缺失')
  const newYear = context.holidays.find(holiday => holiday.name === '元旦')
  check(newYear?.start_date && newYear?.end_date, '后端日历未提供元旦区间')
  const today = process.env.SMOKE_TODAY || context.semester?.today
  check(parseLocalDateTime(today), '上下文 today 非合法本地日期')
  evidence.today = today
  evidence.holidays = context.holidays
  const reference = parseLocalDateTime(`${today}T12:00:00`)
  const aaa = snapshots.aaa.assignments
  const states = {
    done: aaa.filter(item => item.status === 'done').length,
    overdue: aaa.filter(item => dueState(item, reference) === 'overdue').length,
    soon: aaa.filter(item => dueState(item, reference) === 'soon').length,
    holiday: aaa.filter(item => deadlineNotice(item.due_at, context).startsWith('该截止日在放假期间')).length
  }
  check(Object.values(states).every(value => value > 0), `演示四种状态不完整：${JSON.stringify(states)}`)
  evidence.demo_states = states
  evidence.holiday_notice = deadlineNotice(`${newYear.start_date}T18:00:00`, context)
  let weekend = ''
  for (let offset = 0; offset < 21; offset++) {
    const value = shiftLocal(`${today}T18:00:00`, offset)
    if (deadlineNotice(value, context) === '周末不放假，截止日照常') { weekend = value; break }
  }
  check(weekend, '未找到可验证的普通周末截止日')
  evidence.weekend_notice = deadlineNotice(weekend, context)
  log(`演示状态：已完成 ${states.done} / 逾期 ${states.overdue} / 3 天内 ${states.soon} / 假期 ${states.holiday}`)
  log(`假期提示：${evidence.holiday_notice}`)
  log(`周末提示：${evidence.weekend_notice}`)

  for (const username of ['aaa', 'bbb']) {
    const other = username === 'aaa' ? 'bbb' : 'aaa'
    const detail = await success(username, 'GET', `/api/learning-context/courses/2?user_id=${sessions[other].id}`)
    const own = (await listAll(username, 'schedules')).filter(row => row.course_id === 2)
    check(detail.course?.id === 2 && Array.isArray(detail.schedules), `${username} 课程详情格式错误`)
    assert.deepEqual(detail.schedules.map(row => row.id).sort((a, b) => a - b), own.map(row => row.id).sort((a, b) => a - b),
      `${username} 课程详情应只含本人安排，忽略别人的 user_id 参数`)
    evidence.operations.push({ action: 'course_detail', username, course_id: 2, own_schedule_count: detail.schedules.length })
  }
  const adminDetail = await success('admin', 'GET', `/api/learning-context/courses/2?user_id=${sessions.aaa.id}`)
  check(adminDetail.schedules?.length === 0, '管理员课程详情出现学生课表')
  const docs = await success('aaa', 'GET', '/api/learning-context/courses/2/docs')
  check(Array.isArray(docs?.items) && docs.items.length > 0 && docs.count === docs.items.length,
    '真实 MCP 课程检索没有返回资料片段')
  check(docs.items.every(item => typeof item.source === 'string' && item.source.trim()
    && typeof item.text === 'string' && item.text.trim()), '课程资料存在无来源或无正文片段')
  evidence.operations.push({ action: 'course_docs_via_mcp', course_id: 2, count: docs.count,
    sources: [...new Set(docs.items.map(item => item.source))] })
  log(`课程详情按本人过滤；管理员不返回学生安排；真实 MCP 检索返回 ${docs.count} 个有来源片段`)
  return context
}

async function verifyCrossUser(resource, id) {
  for (const [method, suffix, body] of [
    ['PUT', '', { title: `${marker}-越权测试不得生效` }],
    ['DELETE', '', undefined],
    ...(resource === 'plan-tasks' ? [['POST', '/defer', { days: 1 }]] : [])
  ]) {
    const response = await request('bbb', method, `/api/${resource}/${id}${suffix}`, body)
    check(response.code === 404, `bbb ${method} aaa 的 ${resource} 应返回业务 404，实际 ${response.code}`)
    evidence.operations.push({ action: 'cross_user_denied', resource, method, code: response.code })
  }
}

async function verifyAssignment(context) {
  const due = `${context.semester.today}T18:00:00`
  const row = await success('aaa', 'POST', '/api/assignments', {
    course_id: 2, title: `${marker}-实验报告验收`, type: 'assignment',
    due_at: due, note: 'P13-Step2 临时验收记录，运行后自动清理'
  })
  check(Number.isInteger(row?.id) && row.user_id === sessions.aaa.id, '新增作业未绑定当前学生')
  temporary.assignments.add(row.id)
  check(!snapshots.aaa.assignments.some(item => item.id === row.id), '新增作业错误地复用了原有记录 ID')
  await verifyCrossUser('assignments', row.id)
  const title = `${marker}-实验报告验收已修改`
  const completed = await success('aaa', 'PUT', `/api/assignments/${row.id}`, { title, status: 'done' })
  check(completed.title === title && completed.status === 'done' && completed.due_at === due,
    '作业编辑、完成状态或本地截止时间不匹配')
  const pending = await success('aaa', 'PUT', `/api/assignments/${row.id}`, { status: 'pending' })
  check(pending.status === 'pending' && pending.title === title, '取消完成状态错误')
  const filtered = await listAll('aaa', 'assignments', { status: 'pending', type: 'assignment', course_id: '2' })
  check(filtered.some(item => item.id === row.id) && filtered.every(item => item.status === 'pending'
    && item.type === 'assignment' && item.course_id === 2), '作业组合筛选错误')
  await success('aaa', 'DELETE', `/api/assignments/${row.id}`)
  temporary.assignments.delete(row.id)
  evidence.operations.push({ action: 'assignment_crud', id: row.id, create: true, edit: true,
    done_then_pending: true, combined_filter: true, delete: true })
  log('临时作业新增、改标题、完成/取消、组合筛选与删除均成功；bbb 改删被 404 拒绝')
}

async function verifyTask(context) {
  const due = `${context.semester.today}T19:00:00`
  const row = await success('aaa', 'POST', '/api/plan-tasks', {
    title: `${marker}-复习任务验收`, course_id: 2, due_at: due
  })
  check(Number.isInteger(row?.id) && row.user_id === sessions.aaa.id && row.defer_count === 0,
    '新增任务归属或初始延期次数错误')
  temporary['plan-tasks'].add(row.id)
  check(!snapshots.aaa['plan-tasks'].some(item => item.id === row.id), '新增任务错误地复用了原有任务 ID')
  await verifyCrossUser('plan-tasks', row.id)
  const first = await success('aaa', 'POST', `/api/plan-tasks/${row.id}/defer`)
  check(first.due_at === shiftLocal(due, 1) && first.defer_count === 1, '默认延期一天或次数递增错误')
  const second = await success('aaa', 'POST', `/api/plan-tasks/${row.id}/defer`, { days: 2 })
  check(second.due_at === shiftLocal(due, 3) && second.defer_count === 2, '再次延期两天或次数递增错误')
  const cleared = await success('aaa', 'PUT', `/api/plan-tasks/${row.id}`, {
    title: `${marker}-复习任务验收已修改`, due_at: null, course_id: null, status: 'done'
  })
  check(cleared.due_at === null && cleared.course_id === null && cleared.course_name === null
    && cleared.status === 'done' && cleared.defer_count === 2, '任务编辑、清空字段或完成状态错误')
  await success('aaa', 'DELETE', `/api/plan-tasks/${row.id}`)
  temporary['plan-tasks'].delete(row.id)
  evidence.operations.push({ action: 'task_crud', id: row.id, create: true,
    default_defer: { due_at: first.due_at, defer_count: first.defer_count },
    defer_two_days: { due_at: second.due_at, defer_count: second.defer_count },
    clear_optional_fields: true, done: true, delete: true })
  log('临时任务默认延期 +1 天、再延期 +2 天（次数 2）、清空课程/截止、完成及删除成功；bbb 越权均 404')
}

async function cleanupAndCompare() {
  const errors = []
  if (sessions.aaa && snapshots.aaa) {
    for (const resource of ['assignments', 'plan-tasks']) {
      if (!snapshots.aaa[resource]) continue
      try {
        // A timed-out POST may have committed before its response was lost. Discover
        // only this UUID-owned new row; never touch an ID present in the baseline.
        const current = await listAll('aaa', resource, {}, { cleanup: true })
        for (const row of current) {
          if (row.user_id === sessions.aaa.id && row.title.startsWith(marker)
              && !snapshots.aaa[resource].some(original => original.id === row.id)) temporary[resource].add(row.id)
        }
        for (const id of [...temporary[resource]]) {
          check(!snapshots.aaa[resource].some(row => row.id === id), '禁止清理原有演示记录')
          const response = await request('aaa', 'DELETE', `/api/${resource}/${id}`, undefined, { cleanup: true })
          check(response.code === 200 || response.code === 404, `临时 ${resource}/${id} 清理失败，业务码 ${response.code}`)
          temporary[resource].delete(id)
        }
      } catch (error) { errors.push(error.message) }
    }
  }
  evidence.after = {}
  for (const [username, resources] of Object.entries(snapshots)) {
    evidence.after[username] = {}
    for (const [resource, before] of Object.entries(resources)) {
      try {
        const after = await listAll(username, resource, {}, { cleanup: true })
        check(JSON.stringify(after) === JSON.stringify(before), `${username} 原有 ${resource} 全量 JSON 发生变化`)
        evidence.after[username][resource] = { count: after.length, sha256: digest(after), unchanged: true }
      } catch (error) { errors.push(error.message) }
    }
  }
  if (errors.length) throw new Error(errors.join('；'))
  check(temporary.assignments.size === 0 && temporary['plan-tasks'].size === 0, '临时记录未清理完毕')
  if (Object.keys(snapshots).length === 3) log('临时数据全部回收；三账号原有事项/任务/文本计划的全量 JSON 完全不变')
}

try {
  await login('aaa', process.env.E2E_AAA_PASSWORD || '123')
  await login('bbb', process.env.E2E_BBB_PASSWORD || '123')
  await login('admin', process.env.E2E_ADMIN_PASSWORD || 'admin')
  check(sessions.aaa.role === 'student' && sessions.bbb.role === 'student' && sessions.admin.role === 'admin', '登录角色不符合验收基线')
  await captureBaseline()
  const context = await verifyContext()
  await verifyAssignment(context)
  await verifyTask(context)
} catch (error) {
  failed = true
  console.error(`[FAIL] ${error.message}`)
} finally {
  try { await cleanupAndCompare() } catch (error) {
    failed = true
    console.error(`[FAIL] 清理/原数据核对失败：${error.message}`)
  }
  if (interrupted) failed = true
  evidence.result = failed ? 'FAIL' : 'PASS'
  console.log(JSON.stringify(evidence, null, 2))
  process.exitCode = failed ? 1 : 0
}
