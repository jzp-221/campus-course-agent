/**
 * 助手对话的本地会话存储。
 *
 * 设计说明：教程明确要求「服务端不保存会话，每次由前端把历史一起发来」，
 * 因此会话记录只存在浏览器本地，不新增后端表、不引入跨设备同步。
 * 本模块保持纯函数，便于单测。
 */

export const SESSIONS_KEY_PREFIX = 'campus_assistant_sessions_'
export const MAX_SESSIONS = 20
export const MAX_STORED_MESSAGES = 40
export const MAX_TITLE_LENGTH = 24
export const MAX_STORED_CONTENT = 4000

/** 按用户隔离，避免同一台机器上不同账号看到彼此的对话 */
export function storageKey(userId) {
  return `${SESSIONS_KEY_PREFIX}${userId ?? 'anonymous'}`
}

/** 用首条提问生成会话标题 */
export function buildTitle(text, limit = MAX_TITLE_LENGTH) {
  const plain = String(text || '').replace(/\s+/g, ' ').trim()
  if (!plain) return '新对话'
  return plain.length > limit ? `${plain.slice(0, limit)}…` : plain
}

/** 只保留必要的字段，并截断超长正文，避免写爆 localStorage */
export function toStoredMessage(message) {
  const role = message?.role === 'user' ? 'user' : 'assistant'
  const content = String(message?.content ?? '')
  return {
    role,
    content: content.length > MAX_STORED_CONTENT ? content.slice(0, MAX_STORED_CONTENT) : content
  }
}

/** 会话落盘前的裁剪：保留最近若干条问答，并限制会话总数 */
export function trimSessions(sessions, maxSessions = MAX_SESSIONS) {
  const list = Array.isArray(sessions) ? sessions : []
  return list
    .filter((item) => item && typeof item.id === 'string')
    .map((item) => ({
      id: item.id,
      title: typeof item.title === 'string' && item.title ? item.title : '新对话',
      updatedAt: Number(item.updatedAt) || 0,
      createdAt: Number(item.createdAt) || Number(item.updatedAt) || 0,
      messages: (Array.isArray(item.messages) ? item.messages : [])
        .slice(-MAX_STORED_MESSAGES)
        .map(toStoredMessage)
    }))
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, maxSessions)
}

/** 从 assistant 消息里还原用于回传后端的纯文字历史（不含过程区内容） */
export function historyFromMessages(messages, limit = 50) {
  const list = Array.isArray(messages) ? messages : []
  return list
    .filter((item) => item && (item.role === 'user' || item.role === 'assistant') && item.content)
    .slice(-limit)
    .map((item) => ({ role: item.role, content: String(item.content) }))
}

/** 生成一个足够唯一的会话 id，不依赖后端 */
export function createSessionId(now = Date.now(), random = Math.random) {
  return `s-${now.toString(36)}-${Math.floor(random() * 1e6).toString(36)}`
}

export function loadSessions(storage, userId) {
  if (!storage) return []
  try {
    const raw = storage.getItem(storageKey(userId))
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return trimSessions(Array.isArray(parsed) ? parsed : [])
  } catch {
    // 数据损坏或浏览器禁用存储时按空会话处理，不影响正常提问
    return []
  }
}

export function saveSessions(storage, userId, sessions) {
  if (!storage) return false
  try {
    storage.setItem(storageKey(userId), JSON.stringify(trimSessions(sessions)))
    return true
  } catch {
    // 超出配额等情况静默失败：对话仍可继续，只是这次没存上
    return false
  }
}

export function removeSessions(storage, userId) {
  if (!storage) return false
  try {
    storage.removeItem(storageKey(userId))
    return true
  } catch {
    return false
  }
}
