import request from '@/utils/request'

export function listPlanTasksApi(params = {}) {
  return request({ url: '/api/plan-tasks', method: 'get', params })
}

export function createPlanTaskApi(data) {
  return request({ url: '/api/plan-tasks', method: 'post', data })
}

export function updatePlanTaskApi(id, data) {
  return request({ url: `/api/plan-tasks/${id}`, method: 'put', data })
}

export function deferPlanTaskApi(id, days = 1) {
  return request({ url: `/api/plan-tasks/${id}/defer`, method: 'post', data: { days } })
}

export function deletePlanTaskApi(id) {
  return request({ url: `/api/plan-tasks/${id}`, method: 'delete' })
}
