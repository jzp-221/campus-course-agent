// 首个 token 立即切换状态，文字与 Markdown 最多每 50ms 刷新一次。
export function createBufferedAnswer(answer, {
  renderMarkdown,
  onFlush = () => {},
  delayMs = 50,
  schedule = (callback, delay) => setTimeout(callback, delay),
  cancel = (timer) => clearTimeout(timer),
  isDisposed = () => false
}) {
  let pendingText = ''
  let timer = null
  let ended = false

  const clearTimer = () => {
    if (timer !== null) cancel(timer)
    timer = null
  }
  const flush = () => {
    clearTimer()
    if (ended || isDisposed() || !pendingText) return
    answer.content += pendingText
    pendingText = ''
    answer.html = renderMarkdown(answer.content)
    onFlush()
  }

  return {
    push(text) {
      if (ended || isDisposed() || !text) return
      answer.state = 'streaming'
      pendingText += text
      if (timer === null) timer = schedule(flush, delayMs)
    },
    flush,
    complete(text) {
      if (ended || isDisposed()) return
      clearTimer()
      pendingText = ''
      answer.content = text
      answer.html = renderMarkdown(text)
      answer.state = 'done'
      ended = true
      onFlush()
    },
    dispose() {
      ended = true
      clearTimer()
      pendingText = ''
    }
  }
}
