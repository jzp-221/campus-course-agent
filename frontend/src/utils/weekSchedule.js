export const WEEKDAY_LABELS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

// Only use Element Plus theme colors; an ID always selects the same palette entry.
const COURSE_PALETTE = ['primary', 'success', 'warning', 'danger', 'info']

export function courseColor(courseId) {
  let hash = 2166136261
  for (const char of String(courseId)) {
    hash = Math.imul(hash ^ char.charCodeAt(0), 16777619) >>> 0
  }
  const color = COURSE_PALETTE[hash % COURSE_PALETTE.length]
  return {
    '--course-bg': `var(--el-color-${color}-light-9)`,
    '--course-border': `var(--el-color-${color}-light-7)`,
    '--course-accent': `var(--el-color-${color})`,
    '--course-text': `var(--el-color-${color}-dark-2)`
  }
}

export function formatMonthDay(date) {  const [, month, day] = date.split('-')
  return `${Number(month)}/${Number(day)}`
}

export function formatChineseDate(date) {
  const [, month, day] = date.split('-')
  return `${Number(month)}月${Number(day)}日`
}

export function datesForWeek(semesterStartDate, week) {
  const start = new Date(`${semesterStartDate}T00:00:00Z`)
  start.setUTCDate(start.getUTCDate() + (week - 1) * 7)
  return Array.from({ length: 7 }, (_, index) => {
    const date = new Date(start)
    date.setUTCDate(date.getUTCDate() + index)
    return date.toISOString().slice(0, 10)
  })
}

export function adjacentWeek(week, direction, totalWeeks) {
  return Math.max(1, Math.min(totalWeeks, week + direction))
}

export function buildWeekRows(data) {
  if (!Array.isArray(data.days) || data.days.length !== 7 ||
      !Array.isArray(data.periods) || data.periods.length !== 4) {
    throw new Error('课表数据格式异常，请重试')
  }
  const days = Array.from({ length: 7 }, (_, index) =>
    data.days.find((day) => day.weekday === index + 1))
  if (days.some((day) => !day || !Array.isArray(day.courses))) {
    throw new Error('课表日期数据异常，请重试')
  }
  for (const day of days) {
    const used = new Set()
    for (const course of day.courses) {
      if (!data.periods.includes(course.period) || used.has(course.period)) {
        throw new Error('课表单元格数据冲突，请重试')
      }
      used.add(course.period)
    }
  }
  return data.periods.map((period) => ({
    period,
    cells: days.map((day) => ({
      day,
      course: day.courses.find((course) => course.period === period) ?? null
    }))
  }))
}

export function courseTitle(course) {
  return [course.course_name, `教师：${course.teacher || '未填写'}`,
    `节次：${course.period}`, `地点：${course.location || '未填写'}`,
    `周次：${course.start_week}-${course.end_week}周`].join('\n')
}

export function holidayNotice(data) {
  return data?.has_holidays ? data.holiday_notice || '本周含节假日，放假日期不上课' : ''
}

/**
 * 课程时间轴：把 start_week/end_week 变成可直接渲染的 0-100% 横条。
 * 只统计传入这一周实际出现的课程；同一课程只保留一条。
 */
export function buildCourseTimeline(data, week) {
  const totalWeeks = Number(data?.total_weeks)
  const currentWeek = Number(week ?? data?.week)
  if (!Number.isInteger(totalWeeks) || totalWeeks < 1 || !Number.isInteger(currentWeek)) return []

  const seen = new Map()
  for (const day of data?.days || []) {
    for (const course of day.courses || []) {
      if (!course || course.course_id == null || seen.has(course.course_id)) continue
      const start = Number(course.start_week)
      const end = Number(course.end_week)
      if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start) continue
      const from = Math.min(start, totalWeeks)
      const to = Math.min(end, totalWeeks)
      // 百分比做定点舍入，避免 5.5/20*100 = 27.500000000000004 这类浮点噪声
      const percent = (value) => Math.round(value * 1e4) / 1e4
      seen.set(course.course_id, {
        courseId: course.course_id,
        name: course.course_name,
        teacher: course.teacher,
        location: course.location,
        period: course.period,
        startWeek: start,
        endWeek: end,
        remaining: Math.max(0, end - currentWeek),
        ended: end < currentWeek,
        // 以百分比定位，前端无需再关心总周数
        leftPercent: percent(((from - 1) / totalWeeks) * 100),
        widthPercent: percent(((to - from + 1) / totalWeeks) * 100),
        currentPercent: percent(((currentWeek - 0.5) / totalWeeks) * 100)
      })
    }
  }
  return [...seen.values()].sort((a, b) =>
    a.startWeek - b.startWeek || a.endWeek - b.endWeek || a.name.localeCompare(b.name))
}

export function keyboardWeekDirection(event) {
  if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey ||
      event.shiftKey || event.isComposing ||
      event.target?.isContentEditable ||
      event.target?.closest?.('input, textarea, select, [contenteditable], [role="dialog"], .el-dialog')) {
    return 0
  }
  return event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0
}

// Component-scoped cache, partitioned by queried user as well as week.
export function createWeekLoader(fetchWeek) {
  const cache = new Map()
  const pending = new Map()
  let disposed = false

  const cacheKey = (week, userId) => `${userId ?? 'self'}:${week ?? 'current'}`

  function load(week, { fresh = false, userId } = {}) {
    if (disposed) return Promise.reject(new Error('课表页面已关闭'))
    const key = cacheKey(week, userId)
    if (!fresh && cache.has(key)) return Promise.resolve(cache.get(key))
    if (pending.has(key)) return pending.get(key).promise
    if (fresh) cache.delete(key)
    const controller = new AbortController()
    const promise = Promise.resolve().then(() => fetchWeek(week, { signal: controller.signal, userId }))
      .then((response) => {
        if (disposed) throw new Error('课表页面已关闭')
        if (response?.code !== 200 || !response.data) {
          throw new Error(response?.message || '课表请求失败，请重试')
        }
        buildWeekRows(response.data)
        cache.set(cacheKey(response.data.week, userId), response.data)
        if (week === undefined || week === null) cache.set(key, response.data)
        return response.data
      }).finally(() => pending.delete(key))
    pending.set(key, { controller, promise })
    return promise
  }

  function dispose() {
    disposed = true
    for (const request of pending.values()) request.controller.abort()
    pending.clear()
    cache.clear()
  }

  return { load, dispose }
}
