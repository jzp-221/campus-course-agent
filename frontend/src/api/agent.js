import { getToken, logout } from '@/utils/auth'
import router from '@/router'
import { createSSEParser } from '@/utils/sse'

async function rejectRequest(code, message) {
  if (code === 401) {
    logout()
    await router.push('/login')
  }
  const error = new Error(message || '助手请求失败')
  error.code = code
  throw error
}

export async function streamAgentChat({ message, history, signal, onEvent }) {
  const token = getToken()
  if (!token) await rejectRequest(401, '请先登录')
  const base = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
  const response = await fetch(`${base}/api/agent/chat/stream`, {
    method: 'POST', signal,
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ message, history })
  })
  if (!response.ok || !response.headers.get('content-type')?.includes('text/event-stream')) {
    const body = await response.json().catch(() => ({}))
    await rejectRequest(response.status === 401 ? 401 : body.code || response.status,
      body.message || '助手服务未返回事件流')
  }
  if (!response.body) throw new Error('当前浏览器不支持流式读取')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let terminal = false
  let failure = null
  const parser = createSSEParser((event, data) => {
    if (terminal) return
    onEvent(event, data)
    if (event === 'done' || event === 'error') terminal = true
    if (event === 'error') failure = data
  })
  try {
    while (!terminal) {
      const { value, done } = await reader.read()
      if (done) {
        parser.feed(decoder.decode())
        parser.finish()
        break
      }
      parser.feed(decoder.decode(value, { stream: true }))
    }
    if (failure) await rejectRequest(failure.code, failure.message)
    if (!terminal) throw new Error('助手连接提前结束，请重试')
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}
