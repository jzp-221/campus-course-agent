export const SUPPORTED_DOCUMENT_SUFFIXES = Object.freeze(['.txt', '.md', '.pdf', '.docx'])

/** Validate the entire selected batch, including files added by drag and drop. */
export function validateDocumentFiles(files) {
  if (!Array.isArray(files) || files.length === 0) {
    return { ok: false, message: '请先选择要上传的课程资料', invalidNames: [] }
  }
  const invalidNames = files
    .filter((file) => {
      const name = file?.raw?.name || file?.name || ''
      const suffix = name.slice(name.lastIndexOf('.')).toLowerCase()
      return !SUPPORTED_DOCUMENT_SUFFIXES.includes(suffix)
    })
    .map((file) => file?.raw?.name || file?.name || '未命名文件')
  if (invalidNames.length) {
    return {
      ok: false,
      message: `整批未上传：${invalidNames.join('、')}：仅支持 .txt、.md、.pdf、.docx 文件。请移除不支持的文件后重试。`,
      invalidNames
    }
  }
  return { ok: true, message: '', invalidNames: [] }
}

export function documentStatus(status) {
  return {
    indexed: { label: '已入库可检索', type: 'success' },
    processing: { label: '重建中', type: 'primary' },
    failed: { label: '失败', type: 'danger' }
  }[status] || { label: status || '未知状态', type: 'info' }
}

export function isReindexDisabled(document, pendingIds = false) {
  return document?.status === 'processing' ||
    (pendingIds instanceof Set ? pendingIds.has(document?.id) : Boolean(pendingIds))
}

/** One upload invocation for the whole batch; the existing API creates FormData. */
export async function submitDocumentBatch(files, upload, options = {}) {
  const validation = validateDocumentFiles(files)
  if (!validation.ok) throw new Error(validation.message)
  return upload(files.map((file) => file.raw || file), options)
}

/** Acquire the lock synchronously, before confirmation, to prevent double clicks. */
export function createDocumentActionRunner(pendingIds = new Set()) {
  return async (id, operation) => {
    if (pendingIds.has(id)) return { skipped: true }
    pendingIds.add(id)
    try {
      return { skipped: false, response: await operation() }
    } finally {
      pendingIds.delete(id)
    }
  }
}

export function documentErrorMessage(error, fallback = '操作失败，请重试') {
  return error?.response?.data?.message || error?.message || fallback
}
