<template>
  <div class="help-page">
    <el-card shadow="never" class="hero">
      <h2>使用指南</h2>
      <p class="hero-sub">快速了解这个系统能做什么，以及怎么问才能得到最准的回答。</p>
    </el-card>

    <!-- 能力清单 -->
    <el-card shadow="never" class="panel">
      <template #header><span class="panel-title">你能做什么</span></template>
      <div class="capability-grid">
        <div v-for="item in capabilities" :key="item.title" class="capability">
          <el-icon class="cap-icon"><component :is="item.icon" /></el-icon>
          <div>
            <p class="cap-title">{{ item.title }}</p>
            <p class="cap-desc">{{ item.desc }}</p>
            <el-button
              v-if="item.route"
              type="primary"
              link
              @click="go(item.route)"
            >去使用 →</el-button>
          </div>
        </div>
      </div>
    </el-card>

    <!-- 示例问题 -->
    <el-card shadow="never" class="panel">
      <template #header>
        <div class="panel-head">
          <span class="panel-title">这样问，回答最准</span>
          <el-tag size="small" type="info" effect="plain">点击直接去提问</el-tag>
        </div>
      </template>
      <div v-for="group in sampleGroups" :key="group.label" class="sample-group">
        <p class="sample-label">{{ group.label }}</p>
        <div class="sample-tags">
          <el-tag
            v-for="question in group.questions"
            :key="question"
            class="sample-tag"
            effect="plain"
            @click="askSample(question)"
          >{{ question }}</el-tag>
        </div>
      </div>
      <el-alert
        class="tip"
        type="info"
        :closable="false"
        show-icon
        title="小提示"
        description="助手只会依据已入库的课程资料和你的真实课表回答。资料里没有的内容，它会明确说“没有检索到”，不会编造。"
      />
    </el-card>

    <!-- 管理员 -->
    <el-card v-if="isAdmin" shadow="never" class="panel">
      <template #header>
        <div class="panel-head">
          <span class="panel-title">管理员：让助手“有资料可查”</span>
          <el-tag size="small" type="warning" effect="plain">仅管理员</el-tag>
        </div>
      </template>
      <ol class="steps">
        <li>进入「知识库文档」，上传课程资料。支持 <code>.txt / .md / .pdf / .docx</code>，可一次多选批量上传。</li>
        <li>上传后系统自动完成：抽取文本 → 分块 → 向量化 → 可被助手检索。列表里的「入库状态」显示为「已入库可检索」即完成。</li>
        <li>扫描版 PDF 没有文字层，抽不出文本，无法入库（本系统不包含 OCR）。</li>
        <li>资料更新后可点「重新入库」重建索引；删除文档会同时清理它的向量。</li>
      </ol>
      <el-alert
        class="tip"
        type="warning"
        :closable="false"
        show-icon
        title="注意"
        description="助手只能回答已入库资料里的内容。上传后请到「课程助手」用资料里的一个具体问题验证一次，确认检索正常。"
      />
      <el-button type="primary" plain @click="go('Document')">前往知识库文档</el-button>
    </el-card>

    <!-- 说明 -->
    <el-card shadow="never" class="panel">
      <template #header><span class="panel-title">需要知道的几件事</span></template>
      <el-descriptions :column="1" border>
        <el-descriptions-item
          v-for="note in notes"
          :key="note.label"
          :label="note.label"
        >{{ note.text }}</el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<script setup>
import { computed, markRaw } from 'vue'
import { useRouter } from 'vue-router'
import {
  ChatDotRound,
  Calendar,
  Notebook,
  Document as DocumentIcon,
  TrendCharts
} from '@element-plus/icons-vue'
import { useUser } from '@/utils/user'

defineOptions({ name: 'Help' })

const router = useRouter()
const { userInfo } = useUser()
const isAdmin = computed(() => userInfo.value?.role === 'admin')

const capabilities = computed(() => {
  const list = [
    {
      title: '问课表',
      desc: '问「我明天有什么课」，助手会查你的真实课表并按节次、地点列出来。',
      icon: markRaw(ChatDotRound),
      route: 'Assistant'
    },
    {
      title: '问资料',
      desc: '关于课程规定、实验要求、考核方式的问题，助手会依据已上传的课程资料回答，并注明来源文件。',
      icon: markRaw(ChatDotRound),
      route: 'Assistant'
    },
    {
      title: '看周课表',
      desc: '按周查看整学期 20 周的课程安排，节假日会自动空出，调休日会照常排课。',
      icon: markRaw(Calendar),
      route: 'ScheduleWeek'
    },
    {
      title: '写学习计划',
      desc: '可以让助手根据你的课表生成计划，也可以自己填写；保存后随时查看和修改。',
      icon: markRaw(Notebook),
      route: 'StudyPlan'
    }
  ]
  if (isAdmin.value) {
    list.push({
      title: '维护知识库',
      desc: '上传并入库课程资料，助手才有内容可查；支持批量上传、预览、重新入库与删除。',
      icon: markRaw(DocumentIcon),
      route: 'Document'
    })
  }
  return list
})

// 全部取自已完成真实联调的提问，保证点开就有效果
const sampleGroups = [
  {
    label: '课表类',
    questions: ['我明天有什么课？', '我今天有什么课？', '我这周三有几门课？']
  },
  {
    label: '课程资料类',
    questions: [
      '实验课前需要预习吗？',
      '考试可以带计算器吗？',
      '作业晚交了会怎么样？',
      '请假超过三天由谁审批？'
    ]
  },
  {
    label: '学习计划类',
    questions: ['帮我规划一下明天的学习安排', '请根据我的课表生成一份学习计划并保存']
  }
]

const notes = computed(() => {
  const list = [
    {
      label: '数据归属',
      text: '课表、学习计划只属于你本人，助手查询时用的是你的登录身份，不会查到别人的数据。'
    },
    {
      label: '会话记录',
      text: '对话记录保存在本机浏览器里，刷新或重新打开还能接着看；换一台设备看不到历史。'
    },
    {
      label: '资料范围',
      text: '知识库是全班共用的，由管理员维护。你能检索到的内容对所有学生一致。'
    },
    {
      label: '保存计划',
      text: '只有你明确说「保存」「记下来」时，助手才会写入学习计划；只说「帮我规划一下」时它只给建议。'
    }
  ]
  if (isAdmin.value) {
    list.push({ label: '学生视角', text: '周课表支持切换学生查看，便于核对课表是否排错。' })
  }
  return list
})

function go(routeName) {
  router.push({ name: routeName })
}

function askSample(question) {
  router.push({ name: 'Assistant', query: { q: question } })
}
</script>

<style scoped>
.help-page { max-width: 1080px; margin: 0 auto; }
.hero { border-radius: 14px; margin-bottom: 18px; }
.hero h2 { margin: 0 0 8px; font-size: 22px; color: #253047; }
.hero-sub { margin: 0; color: #7a8598; font-size: 14px; }

.panel { border-radius: 14px; margin-bottom: 18px; }
.panel-head { display: flex; align-items: center; justify-content: space-between; }
.panel-title { font-weight: 600; color: #253047; }

.capability-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 18px; }
.capability { display: flex; gap: 12px; padding: 16px; border-radius: 12px; background: #f7f9fc; }
.cap-icon { flex: none; margin-top: 2px; font-size: 20px; color: var(--el-color-primary); }
.cap-title { margin: 0 0 6px; font-weight: 600; color: #29354a; }
.cap-desc { margin: 0 0 4px; font-size: 13px; line-height: 1.65; color: #64728a; }

.sample-group + .sample-group { margin-top: 14px; }
.sample-label { margin: 0 0 8px; font-size: 13px; color: #8b95a5; }
.sample-tags { display: flex; flex-wrap: wrap; gap: 8px; }
.sample-tag { cursor: pointer; }
.sample-tag:hover { border-color: var(--el-color-primary); color: var(--el-color-primary); }

.tip { margin-top: 16px; }
.steps { margin: 0 0 14px; padding-left: 20px; line-height: 1.9; color: #4a586d; font-size: 14px; }
.steps code { padding: 1px 6px; border-radius: 4px; background: #f0f2f7; font-size: 13px; }
</style>
