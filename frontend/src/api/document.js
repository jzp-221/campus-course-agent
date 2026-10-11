import request from '@/utils/request'

/** 知识库文档分页列表：返回 list + total */
export function listDocumentsApi(params = {}) {
  return request({
    url: '/api/documents',
    method: 'get',
    params
  })
}

/** 批量上传并入库；files 为 File 数组，任意一个失败整批回滚 */
export function uploadDocumentsApi(files, { onProgress } = {}) {
  const form = new FormData()
  for (const file of files) form.append('files', file)
  return request({
    url: '/api/documents/upload',
    method: 'post',
    data: form,
    // 不要手动设置 Content-Type：交给浏览器/axios 自动补 multipart 边界
    timeout: 120000,
    onUploadProgress: onProgress
  })
}

/** 预览抽取出的纯文本 */
export function previewDocumentApi(documentId) {
  return request({
    url: `/api/documents/${documentId}/preview`,
    method: 'get'
  })
}

/** 重新入库（从原始文件重新抽取、分块、向量化） */
export function reindexDocumentApi(documentId) {
  return request({
    url: `/api/documents/${documentId}/reindex`,
    method: 'post',
    timeout: 120000
  })
}

export function deleteDocumentApi(documentId) {
  return request({
    url: `/api/documents/${documentId}`,
    method: 'delete'
  })
}
