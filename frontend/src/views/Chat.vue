<template>
  <div class="assistant-page">
    <div class="page-heading">
      <div><h2>课程助手</h2><p>查课表、检索课程资料、规划学习安排；明确说“保存”后才会记录学习计划</p></div>
      <el-button :disabled="sending || !messages.length" @click="clearChat">清空对话</el-button>
    </div>
    <div class="chat-layout">
      <el-card class="session-card" shadow="never">
        <template #header>
          <div class="session-head">
            <strong>对话记录</strong>
            <el-button type="primary" size="small" :disabled="sending" @click="newSession">新建</el-button>
          </div>
        </template>
        <el-empty v-if="!sessions.length" description="还没有对话" :image-size="60" />
        <ul v-else class="session-list">
          <li
            v-for="item in sessions"
            :key="item.id"
            class="session-item"
            :class="{ active: item.id === activeSessionId }"
            @click="switchSession(item.id)"
          >
            <div class="session-title">{{ item.title }}</div>
            <div class="session-meta">
              <span>{{ formatSessionTime(item.updatedAt) }}</span>
              <el-icon class="session-remove" @click.stop="removeSession(item.id)"><Close /></el-icon>
            </div>
          </li>
        </ul>
      </el-card>
      <el-card class="conversation-card" shadow="never">
        <div ref="conversation" class="conversation" aria-live="polite">
          <el-empty v-if="!messages.length" description="试试“我明天有什么课？”或“帮我规划一下明天的学习安排”" />
          <div v-for="item in messages" :key="item.id" class="message-row" :class="item.role">
            <div class="bubble">
              <div class="role-label">{{ item.role === 'user' ? '你' : '课程助手' }}</div>
              <div v-if="item.role === 'user'" class="user-text">{{ item.content }}</div>
              <div v-else class="answer-md" v-html="item.html"></div>
              <div v-if="item.role === 'assistant'" class="message-state">
                {{ stateLabels[item.state] }}
                <span v-if="item.error">：{{ item.error }}</span>
              </div>
            </div>
          </div>
        </div>
        <form class="composer" @submit.prevent="sendMessage">
          <el-input v-model="input" type="textarea" :rows="3" :maxlength="4000" :disabled="sending"
            placeholder="输入你的问题，Enter 发送，Shift+Enter 换行" @keydown="handleKey" />
          <div class="composer-actions">
            <span>对话记录保存在本机浏览器，刷新后仍可继续；换设备看不到</span>
            <el-button v-if="sending" @click="stop">停止回答</el-button>
            <el-button type="primary" native-type="submit" :loading="sending" :disabled="!input.trim()">发送</el-button>
          </div>
        </form>
      </el-card>
      <el-card class="process-card" shadow="never">
        <template #header><strong>处理过程</strong></template>
        <el-empty v-if="!processes.length" description="查询、检索与保存进度会显示在这里" :image-size="65" />
        <div v-for="process in processes" :key="process.id" class="process-turn">
          <div class="process-question">{{ process.question }}</div>
          <el-tag size="small" :type="process.state === 'error' ? 'danger' : process.state === 'done' ? 'success' : 'info'">
            {{ process.label }}
          </el-tag>
          <ul class="process-list">
            <li v-for="entry in process.entries" :key="entry.id">
              <span>{{ entry.label }}</span>
              <small v-if="entry.detail">{{ entry.detail }}</small>
            </li>
          </ul>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Close } from '@element-plus/icons-vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { streamAgentChat } from '@/api/agent'
import { createBufferedAnswer } from '@/utils/bufferedAnswer'
import { useUser } from '@/utils/user'
import {
  buildTitle,
  createSessionId,
  historyFromMessages,
  loadSessions,
  saveSessions,
  toStoredMessage
} from '@/utils/chatSessions'

defineOptions({ name: 'AssistantChat' })
const route = useRoute()
const router = useRouter()
const { userInfo } = useUser()
const input = ref('')
const sending = ref(false)
const messages = ref([])
const processes = ref([])
const history = ref([])
const conversation = ref(null)
const sessions = ref([])
const activeSessionId = ref('')
const stateLabels = { pending: '正在处理', streaming: '正在回答', done: '回答完成', error: '回答失败', stopped: '已停止' }
const stageLabels = { thinking: '正在思考', querying: '正在查询课表', saving: '正在保存学习计划', retrieving: '正在检索课程资料' }
const toolLabels = { get_my_schedules: '查询我的课表', save_study_plan: '保存学习计划', search_course_docs: '检索课程资料' }
let sequence = 0
let controller = null
let activeAnswerRenderer = null
let disposed = false

function safeMarkdown(text) {
  return DOMPurify.sanitize(marked.parse(text, { gfm: true, breaks: true, async: false }),
    { USE_PROFILES: { html: true } })
}

async function scrollDown() {
  await nextTick()
  if (conversation.value) conversation.value.scrollTop = conversation.value.scrollHeight
}

/* ---------- 本地会话记录：仅存浏览器，不新增后端表 ---------- */

function storage() {
  try {
    return window.localStorage
  } catch {
    // 隐私模式等场景下不可用，退化为"本次打开有效"
    return null
  }
}

function userId() {
  return userInfo.value?.id ?? null
}

function persist() {
  const active = sessions.value.find((item) => item.id === activeSessionId.value)
  if (active) {
    active.messages = messages.value.map((item) => toStoredMessage(item))
    active.updatedAt = Date.now()
  }
  saveSessions(storage(), userId(), sessions.value)
}

function applyActiveSession() {
  const active = sessions.value.find((item) => item.id === activeSessionId.value)
  if (!active) {
    messages.value = []
    history.value = []
  } else {
    messages.value = active.messages.map((item) => ({
      id: ++sequence,
      role: item.role,
      content: item.content,
      html: item.role === 'assistant' ? safeMarkdown(item.content) : '',
      state: 'done',
      error: ''
    }))
    history.value = historyFromMessages(active.messages)
  }
  // 过程区不落盘：历史回合只还原问答文字
  processes.value = []
}

function ensureSession() {
  if (sessions.value.some((item) => item.id === activeSessionId.value)) return
  const id = createSessionId()
  sessions.value.unshift({ id, title: '新对话', messages: [], createdAt: Date.now(), updatedAt: Date.now() })
  activeSessionId.value = id
}

function newSession() {
  if (sending.value) return
  const previous = sessions.value.find((item) => item.id === activeSessionId.value)
  // 空会话不重复创建，避免连点产生一堆空记录
  if (previous && !previous.messages.length) {
    applyActiveSession()
    return
  }
  const id = createSessionId()
  sessions.value.unshift({ id, title: '新对话', messages: [], createdAt: Date.now(), updatedAt: Date.now() })
  activeSessionId.value = id
  applyActiveSession()
  persist()
  scrollDown()
}

function switchSession(id) {
  if (sending.value || id === activeSessionId.value) return
  persist()
  activeSessionId.value = id
  applyActiveSession()
  scrollDown()
}

async function removeSession(id) {
  if (sending.value) return
  sessions.value = sessions.value.filter((item) => item.id !== id)
  if (activeSessionId.value === id) {
    if (sessions.value.length) {
      activeSessionId.value = sessions.value[0].id
    } else {
      const next = createSessionId()
      sessions.value = [{ id: next, title: '新对话', messages: [], createdAt: Date.now(), updatedAt: Date.now() }]
      activeSessionId.value = next
    }
    applyActiveSession()
  }
  persist()
}

function formatSessionTime(value) {
  if (!value) return ''
  const date = new Date(value)
  const pad = (n) => String(n).padStart(2, '0')
  const today = new Date()
  const sameDay = date.toDateString() === today.toDateString()
  return sameDay
    ? `${pad(date.getHours())}:${pad(date.getMinutes())}`
    : `${date.getMonth() + 1}/${date.getDate()}`
}

async function sendMessage() {
  const question = input.value.trim()
  if (!question || sending.value) return
  ensureSession()
  const active = sessions.value.find((item) => item.id === activeSessionId.value)
  if (active && active.title === '新对话') active.title = buildTitle(question)
  const previous = history.value.slice(-50).map((item) => ({ role: item.role, content: item.content.slice(0, 10000) }))
  messages.value.push({ id: ++sequence, role: 'user', content: question })
  const answer = reactive({ id: ++sequence, role: 'assistant', content: '', html: '', state: 'pending', error: '' })
  messages.value.push(answer)
  const process = reactive({ id: ++sequence, question, state: 'pending', label: '正在思考', entries: [] })
  processes.value.push(process)
  input.value = ''
  persist()
  sending.value = true
  controller = new AbortController()
  let activeTool = null
  const renderer = createBufferedAnswer(answer, {
    renderMarkdown: safeMarkdown, onFlush: scrollDown, isDisposed: () => disposed
  })
  activeAnswerRenderer = renderer
  await scrollDown()
  try {
    await streamAgentChat({ message: question, history: previous, signal: controller.signal,
      onEvent(event, data) {
        if (disposed) return
        if (event === 'status') {
          process.label = stageLabels[data.stage]
          process.entries.push({ id: ++sequence, label: process.label })
        } else if (event === 'tool_start') {
          activeTool = reactive({ id: ++sequence, name: data.name, label: toolLabels[data.name],
            detail: data.name === 'get_my_schedules' ? data.args.date || '全部日期' :
              data.name === 'search_course_docs' ? data.args.query : '仅保存计划正文' })
          process.entries.push(activeTool)
        } else if (event === 'tool_end') {
          if (activeTool) activeTool.label = `${toolLabels[data.name]}：${data.ok ? '完成' : '失败'}`
          activeTool = null
        } else if (event === 'token') {
          process.label = '正在生成回答'
          renderer.push(data.text)
        } else if (event === 'done') {
          renderer.complete(data.answer)
          process.state = 'done'
          process.label = '处理完成'
          history.value.push({ role: 'user', content: question }, { role: 'assistant', content: data.answer })
          persist()
          scrollDown()
        } else if (event === 'error') {
          renderer.flush()
          answer.error = data.message
          answer.state = 'error'
          process.state = 'error'
          process.label = '处理失败'
        }
      }
    })
  } catch (error) {
    if (!disposed) {
      renderer.flush()
      const stopped = error.name === 'AbortError'
      answer.state = stopped ? 'stopped' : 'error'
      answer.error = stopped ? '' : error.message || '助手请求失败'
      process.state = stopped ? 'stopped' : 'error'
      process.label = stopped ? '已停止' : '处理失败'
      if (activeTool) activeTool.label = `${toolLabels[activeTool.name]}：已中断`
      if (!stopped) ElMessage.error(answer.error)
    }
  } finally {
    renderer.dispose()
    activeAnswerRenderer = null
    controller = null
    if (!disposed) sending.value = false
    persist()
  }
}

function handleKey(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    sendMessage()
  }
}
function stop() { controller?.abort() }
function clearChat() {
  if (sending.value) return
  messages.value = []
  processes.value = []
  history.value = []
  const active = sessions.value.find((item) => item.id === activeSessionId.value)
  if (active) {
    active.messages = []
    active.title = '新对话'
    active.updatedAt = Date.now()
  }
  persist()
}

onMounted(async () => {
  sessions.value = loadSessions(storage(), userId())
  if (!sessions.value.length) {
    const id = createSessionId()
    sessions.value = [{ id, title: '新对话', messages: [], createdAt: Date.now(), updatedAt: Date.now() }]
  }
  activeSessionId.value = sessions.value[0].id
  applyActiveSession()
  await scrollDown()
  // 使用指南里的示例问题通过 ?q= 跳转过来，自动填入并提问
  const preset = typeof route.query.q === 'string' ? route.query.q.trim() : ''
  if (preset) {
    input.value = preset
    router.replace({ name: 'Assistant', query: {} })
    await sendMessage()
  }
})

onBeforeUnmount(() => {
  disposed = true
  stop()
  activeAnswerRenderer?.dispose()
  persist()
})
</script>

<style scoped>
.assistant-page { max-width: 1280px; margin: 0 auto; }
.page-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
h2 { margin: 0 0 8px; font-size: 23px; color: #253047; }
.page-heading p { margin: 0; color: #7a8598; }
.chat-layout { display: grid; grid-template-columns: 220px minmax(0, 1fr) 290px; gap: 20px; align-items: start; }

/* 对话记录侧栏 */
.session-card { align-self: start; }
.session-head { display: flex; align-items: center; justify-content: space-between; }
.session-list { margin: 0; padding: 0; list-style: none; max-height: min(56vh, 600px); overflow-y: auto; }
.session-item { padding: 10px 12px; border-radius: 10px; cursor: pointer; transition: background-color .15s; }
.session-item:hover { background: #f4f7fb; }
.session-item.active { background: var(--el-color-primary-light-9); }
.session-title { font-size: 13px; color: #29354a; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session-item.active .session-title { color: var(--el-color-primary); font-weight: 600; }
.session-meta { display: flex; align-items: center; justify-content: space-between; margin-top: 4px; font-size: 12px; color: #9aa4b2; }
.session-remove { cursor: pointer; }
.session-remove:hover { color: var(--el-color-danger); }
.conversation { height: min(56vh, 600px); min-height: 320px; overflow-y: auto; padding: 4px 10px 16px; }
.message-row { display: flex; margin: 18px 0; }
.message-row.user { justify-content: flex-end; }
.bubble { max-width: 88%; min-width: 100px; padding: 14px 18px; border-radius: 14px; background: #f3f5f9; overflow-wrap: anywhere; }
.user .bubble { background: #eaf2ff; }
.role-label { font-size: 12px; color: #788599; margin-bottom: 8px; }
.user-text { white-space: pre-wrap; line-height: 1.6; }
.answer-md { line-height: 1.7; color: #29354a; }
.answer-md :deep(p:first-child) { margin-top: 0; }
.answer-md :deep(p:last-child) { margin-bottom: 0; }
.answer-md :deep(pre) { overflow-x: auto; background: #e8edf4; padding: 12px; border-radius: 8px; }
.answer-md :deep(table) { border-collapse: collapse; max-width: 100%; }
.answer-md :deep(td), .answer-md :deep(th) { border: 1px solid #dce2eb; padding: 6px 9px; }
.message-state { font-size: 12px; color: #8893a4; margin-top: 10px; }
.composer { border-top: 1px solid #edf0f5; padding-top: 18px; }
.composer-actions { display: flex; align-items: center; gap: 10px; margin-top: 12px; }
.composer-actions > span { flex: 1; font-size: 12px; color: #8b95a5; }
.process-card { align-self: start; }
.process-turn + .process-turn { border-top: 1px solid #edf0f5; padding-top: 16px; margin-top: 16px; }
.process-question { margin-bottom: 10px; font-size: 13px; color: #536078; overflow-wrap: anywhere; }
.process-list { padding-left: 18px; font-size: 13px; color: #64728a; line-height: 1.8; }
.process-list small { display: block; color: #8b95a5; }
@media (max-width: 1180px) { .chat-layout { grid-template-columns: 180px minmax(0, 1fr); } .process-card { grid-column: 1 / -1; } }
@media (max-width: 900px) { .chat-layout { grid-template-columns: 1fr; } .conversation { height: 50vh; } .session-list { max-height: 30vh; } }
</style>
