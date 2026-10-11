import request from '@/utils/request'

export function listAssignmentsApi(params = {}) {
  return request({ url: '/api/assignments', method: 'get', params })
}

export function createAssignmentApi(data) {
  return request({ url: '/api/assignments', method: 'post', data })
}

export function updateAssignmentApi(id, data) {
  return request({ url: `/api/assignments/${id}`, method: 'put', data })
}

export function deleteAssignmentApi(id) {
  return request({ url: `/api/assignments/${id}`, method: 'delete' })
}
