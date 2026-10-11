import request from '@/utils/request'

export function listCoursesApi(params) {
  return request({
    url: '/api/course/list',
    method: 'get',
    params
  })
}


export function createCourseApi(data) {
  return request({
    url: '/api/course',
    method: 'post',
    data
  })
}

export function updateCourseApi(id, data) {
  return request({
    url: `/api/course/${id}`,
    method: 'put',
    data
  })
}

export function deleteCourseApi(id) {
  return request({
    url: `/api/course/${id}`,
    method: 'delete'
  })
}
