import test from 'node:test'
import assert from 'node:assert/strict'
import { reactive, watch } from 'vue'
import { createBufferedAnswer } from '../src/utils/bufferedAnswer.js'

function fixture() {
  const answer = reactive({ state: 'pending', content: '', html: '' })
  const timers = new Map()
  const rendered = []
  let clock = 0
  let nextTimer = 0
  let disposed = false
  const renderer = createBufferedAnswer(answer, {
    renderMarkdown: (text) => { rendered.push(text); return `safe:${text}` },
    schedule(callback, delay) { const id = ++nextTimer; timers.set(id, { callback, at: clock + delay }); return id },
    cancel(id) { timers.delete(id) },
    isDisposed: () => disposed
  })
  return {
    answer, timers, rendered, renderer,
    disposePage() { disposed = true },
    advance(ms) {
      clock += ms
      for (const [id, timer] of [...timers]) {
        if (timer.at <= clock) { timers.delete(id); timer.callback() }
      }
    }
  }
}

test('first token enters streaming and a partial answer renders before done', () => {
  const f = fixture()
  const states = [f.answer.state]
  const stopWatch = watch(() => f.answer.state, (state) => states.push(state), { flush: 'sync' })
  f.renderer.push('明天')
  assert.equal(f.answer.state, 'streaming')
  assert.equal(f.answer.content, '')
  f.advance(50)
  assert.equal(f.answer.content, '明天')
  assert.equal(f.answer.html, 'safe:明天')
  assert.equal(f.answer.state, 'streaming')
  f.renderer.push('有英语课。')
  f.renderer.complete('明天有英语课。')
  assert.equal(f.answer.content, '明天有英语课。')
  assert.deepEqual(states, ['pending', 'streaming', 'done'])
  assert.equal(f.timers.size, 0)
  stopWatch()
})

test('tokens share a 50ms batch instead of rendering one Markdown block per token', () => {
  const f = fixture()
  f.renderer.push('上午')
  f.advance(10)
  f.renderer.push('复习')
  f.advance(10)
  f.renderer.push('英语')
  assert.equal(f.timers.size, 1)
  assert.deepEqual(f.rendered, [])
  f.advance(30)
  assert.deepEqual(f.rendered, ['上午复习英语'])
  f.renderer.push('。')
  f.advance(50)
  assert.deepEqual(f.rendered, ['上午复习英语', '上午复习英语。'])
  assert.equal(f.answer.state, 'streaming')
})

test('stop flushes partial text and disposal cancels every scheduled refresh', () => {
  const f = fixture()
  f.renderer.push('已收到的文字')
  f.renderer.flush()
  f.answer.state = 'stopped'
  f.renderer.dispose()
  f.renderer.push('迟到的 token')
  f.advance(100)
  assert.equal(f.timers.size, 0)
  assert.equal(f.answer.content, '已收到的文字')
  assert.equal(f.answer.state, 'stopped')
})

test('page disposal prevents late tokens, complete and timed callbacks from updating bubbles', () => {
  const f = fixture()
  f.renderer.push('尚未刷新')
  f.disposePage()
  f.advance(50)
  f.renderer.push('迟到')
  f.renderer.complete('迟到的最终答案')
  f.renderer.dispose()
  assert.equal(f.answer.content, '')
  assert.deepEqual(f.rendered, [])
  assert.equal(f.timers.size, 0)
})
