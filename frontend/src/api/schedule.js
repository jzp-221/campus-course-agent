import request from '@/utils/request'

/** 课表分页列表：学生查自己；管理员可传 user_id / date；返回 list + total */
export function listSchedulesApi(params = {}) {
  return request({
    url: '/api/schedules',
    method: 'get',
    params
  })
}

/** 单周课表；管理员可选择学生，首次省略 week 由后端选择今天所在周。 */
export function getWeekScheduleApi(week, { signal, userId } = {}) {
  const params = {}
  if (week !== undefined && week !== null) params.week = week
  if (userId !== undefined && userId !== null) params.user_id = userId
  return request({
    url: '/api/schedules/week',
    method: 'get',
    params,
    signal
  })
}

export function createScheduleApi(data) {
  return request({
    url: '/api/schedules',
    method: 'post',
    data
  })
}

export function updateScheduleApi(id, data) {
  return request({
    url: `/api/schedules/${id}`,
    method: 'put',
    data
  })
}

export function deleteScheduleApi(id) {
  return request({
    url: `/api/schedules/${id}`,
    method: 'delete'
  })
}
