import request from '@/utils/request'

export function getLearningContextApi() {
  return request({ url: '/api/learning-context', method: 'get' })
}

export function getCourseDetailApi(id) {
  return request({ url: `/api/learning-context/courses/${id}`, method: 'get' })
}

export function getCourseDocsApi(id) {
  return request({ url: `/api/learning-context/courses/${id}/docs`, method: 'get', timeout: 45000 })
}
