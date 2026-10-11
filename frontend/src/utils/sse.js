// 流数据可能在任意字节、行或帧边界被切开；只在完整 SSE 帧到齐后解析 JSON。
const fields = {
  status: ['stage'], tool_start: ['name', 'args'], tool_end: ['name', 'ok'],
  token: ['text'], done: ['answer', 'tool_calls'], error: ['code', 'message']
}

const toolNames = new Set(['get_my_schedules', 'save_study_plan', 'search_course_docs'])
const validToolArgs = (name, args) => {
  if (!args || typeof args !== 'object' || Array.isArray(args)) return false
  if (name === 'get_my_schedules') {
    return Object.keys(args).every((key) => key === 'date') &&
      (args.date == null || typeof args.date === 'string')
  }
  if (name === 'save_study_plan') {
    return Object.keys(args).length === 1 && typeof args.content === 'string' &&
      Boolean(args.content.trim()) && Array.from(args.content).length <= 2000
  }
  if (name === 'search_course_docs') {
    return Object.keys(args).length === 1 && typeof args.query === 'string' &&
      Boolean(args.query.trim()) && Array.from(args.query).length <= 2000
  }
  return false
}

export function validateEvent(event, data) {
  const expected = fields[event]
  if (!expected || !data || typeof data !== 'object' || Array.isArray(data) ||
      Object.keys(data).sort().join() !== [...expected].sort().join()) {
    throw new Error('助手返回了不符合约定的事件')
  }
  if (event === 'status' && !['thinking', 'querying', 'saving', 'retrieving'].includes(data.stage)) throw new Error('助手状态不正确')
  if (event === 'tool_start' && !validToolArgs(data.name, data.args)) throw new Error('工具参数不正确')
  if (event === 'tool_end' && (!toolNames.has(data.name) || typeof data.ok !== 'boolean')) throw new Error('工具结果不正确')
  if (event === 'token' && typeof data.text !== 'string') throw new Error('回答片段不正确')
  if (event === 'done' && (typeof data.answer !== 'string' || !data.answer.trim() ||
      !Array.isArray(data.tool_calls) || !data.tool_calls.every((call) =>
        call && validToolArgs(call.name, call.args)))) throw new Error('最终回答不正确')
  if (event === 'error' && (!Number.isInteger(data.code) || typeof data.message !== 'string')) throw new Error('错误事件不正确')
}

export function createSSEParser(onEvent) {
  let buffer = ''
  return {
    feed(text) {
      buffer += text
      if (buffer.length > 1024 * 1024) throw new Error('助手事件过大')
      let match
      while ((match = /\r?\n\r?\n|\r\r/.exec(buffer))) {
        const frame = buffer.slice(0, match.index)
        buffer = buffer.slice(match.index + match[0].length)
        let event = ''
        const data = []
        for (const line of frame.split(/\r\n|\n|\r/)) {
          if (line.startsWith(':')) continue // 心跳注释不属于业务事件。
          const colon = line.indexOf(':')
          const key = colon < 0 ? line : line.slice(0, colon)
          const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '')
          if (key === 'event') event = value
          if (key === 'data') data.push(value)
        }
        if (!data.length) continue
        const payload = JSON.parse(data.join('\n'))
        validateEvent(event, payload)
        onEvent(event, payload)
      }
    },
    finish() {
      if (buffer.trim() && !buffer.trim().startsWith(':')) throw new Error('助手连接中断，事件未接收完整')
    }
  }
}
