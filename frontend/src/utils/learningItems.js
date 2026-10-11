// API datetimes are local wall-clock values. Never append Z or convert timezones.
export function parseLocalDateTime(value) {
  if (!value) return null
  const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/)
  if (!match) return null
  const [, year, month, day, hour = '0', minute = '0', second = '0'] = match
  const date = new Date(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute), Number(second))
  if (date.getFullYear() !== Number(year) || date.getMonth() !== Number(month) - 1 || date.getDate() !== Number(day)
      || date.getHours() !== Number(hour) || date.getMinutes() !== Number(minute) || date.getSeconds() !== Number(second)) return null
  return date
}

export function formatLocalDateTime(value) {
  return parseLocalDateTime(value) ? String(value).replace('T', ' ').slice(0, 16) : '未设截止'
}

export function remainingDays(value, now = new Date()) {
  const due = parseLocalDateTime(value)
  if (!due) return null
  // Calendar day difference: remains correct across months and DST boundaries.
  return Math.round((Date.UTC(due.getFullYear(), due.getMonth(), due.getDate())
    - Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())) / 86400000)
}

export function isOverdue(item, now = new Date()) {
  const due = parseLocalDateTime(item?.due_at)
  return item?.status === 'pending' && due !== null && due.getTime() < now.getTime()
}

export function dueState(item, now = new Date()) {
  if (item?.status === 'done') return 'done'
  const due = parseLocalDateTime(item?.due_at)
  if (!due) return 'unset'
  if (isOverdue(item, now)) return 'overdue'
  return due.getTime() - now.getTime() <= 3 * 86400000 ? 'soon' : 'normal'
}

export function dueLabel(item, now = new Date()) {
  const days = remainingDays(item?.due_at, now)
  if (days === null) return '未设截止'
  if (isOverdue(item, now)) return days === 0 ? '今天已逾期' : `已逾期 ${Math.abs(days)} 天`
  if (days === 0) return '今天到期'
  if (days === 1) return '明天到期'
  return `剩余 ${days} 天`
}

export function sortByDue(rows) {
  return [...rows].sort((a, b) => (parseLocalDateTime(a.due_at)?.getTime() ?? Infinity)
    - (parseLocalDateTime(b.due_at)?.getTime() ?? Infinity) || Number(a.id) - Number(b.id))
}

export function filterAssignments(rows, { type = '', status = '', dueRange = 'all' } = {}, now = new Date()) {
  return rows.filter(item => {
    if (type && type !== 'all' && item.type !== type) return false
    if (status && status !== 'all' && item.status !== status) return false
    if (dueRange === 'overdue') return isOverdue(item, now)
    if (dueRange === '3' || dueRange === '7') {
      const due = parseLocalDateTime(item.due_at)
      return due !== null && due.getTime() >= now.getTime() && due.getTime() - now.getTime() <= Number(dueRange) * 86400000
    }
    return true
  })
}

export function pendingCount(rows) {
  return rows.filter(item => item.status === 'pending').length
}

export function deadlineNotice(value, calendar) {
  const due = parseLocalDateTime(value)
  if (!due || !calendar || !Array.isArray(calendar.holidays)) return ''
  const day = String(value).slice(0, 10)
  const holiday = calendar.holidays.find(item => item.start_date <= day && day <= item.end_date)
  if (holiday) {
    const dateLabel = iso => `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`
    return `该截止日在放假期间（${holiday.name} ${dateLabel(holiday.start_date)}-${dateLabel(holiday.end_date)}）`
  }
  if ((due.getDay() === 0 || due.getDay() === 6) && !calendar.makeup_workdays?.includes(day)) {
    return '周末不放假，截止日照常'
  }
  if (calendar.makeup_workdays?.includes(day)) return '调休上班日，截止日照常'
  return ''
}

export function ensureSuccess(response) {
  if (response?.code !== 200) throw new Error(response?.message || '请求失败，请重试')
  return response.data
}

export async function fetchAllPages(loadPage, params = {}) {
  const all = []
  let pages = 1
  let total = 0
  for (let page = 1; page <= pages; page++) {
    const data = ensureSuccess(await loadPage({ ...params, page, page_size: 100 }))
    if (!Array.isArray(data?.list) || !Number.isInteger(data.total) || data.total < 0) throw new Error('列表数据格式不正确')
    if (page === 1) {
      total = data.total
      pages = Math.max(1, Math.ceil(total / 100))
    } else if (data.total !== total) throw new Error('列表已发生变化，请重新加载')
    if (page < pages && data.list.length === 0) throw new Error('列表未完整加载，请重试')
    all.push(...data.list)
  }
  const rows = [...new Map(all.map(row => [row.id, row])).values()]
  if (rows.length !== total) throw new Error('列表未完整加载，请重试')
  return rows
}
