<template>
  <div>
    <el-container style="min-height: 100vh">
      <el-header
        style="
          display: flex;
          align-items: center;
          border-bottom: 1px solid #ddd;
          background-color: #fff;
        "
      >
        <div
          style="flex: 1; display: flex; align-items: center; font-size: 24px; font-weight: bold"
        >
          <img style="width: 40px" src="@/assets/imgs/logo.png" alt="" />
          <div style="margin-left: 5px">智能系统</div>
        </div>
        <div>
          <el-dropdown @command="handleCommand">
            <div style="display: flex; align-items: center; cursor: pointer">
              <img :src="userInfo?.avatar" alt="" style="width: 30px; border-radius: 50%" />
              <div style="margin-left: 3px">{{ userInfo?.name }}</div>
            </div>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">个人信息</el-dropdown-item>
                <el-dropdown-item command="password">修改密码</el-dropdown-item>
                <el-dropdown-item divided command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <el-container>
        <el-aside width="220px"
          :class="{ 'week-view-aside': router.currentRoute.value.path === '/manager/schedule-week' }">
          <el-menu
            router
            :default-active="router.currentRoute.value.path"
            style="height: 100%"
          >
            <el-menu-item index="/manager/home" title="系统首页" aria-label="系统首页">
              <el-icon><icon-menu /></el-icon>
              <span>系统首页</span>
            </el-menu-item>
            <el-menu-item index="/manager/assistant" title="课程助手" aria-label="课程助手">
              <el-icon><ChatDotRound /></el-icon>
              <span>课程助手</span>
            </el-menu-item>
            <el-menu-item index="/manager/schedule" title="我的课表" aria-label="我的课表" v-if="userInfo?.role === 'student'">
              <el-icon><Calendar /></el-icon>
              <span>我的课表</span>
            </el-menu-item>
            <el-menu-item index="/manager/schedule-week" title="周课表" aria-label="周课表" v-if="userInfo?.role === 'student' || userInfo?.role === 'admin'">
              <el-icon><Calendar /></el-icon>
              <span>周课表</span>
            </el-menu-item>
            <el-menu-item index="/manager/study-plan" title="学习计划" aria-label="学习计划" v-if="userInfo?.role !== 'admin'">
              <el-icon><Notebook /></el-icon>
              <span>学习计划</span>
            </el-menu-item>
            <el-menu-item index="/manager/assignment" title="作业与考试" aria-label="作业与考试">
              <el-icon><Tickets /></el-icon>
              <el-badge :value="inbox.pendingCount.value" :hidden="userInfo?.role !== 'student' || !inbox.loaded.value || inbox.pendingCount.value === 0" :max="99" class="assignment-badge"><span>作业与考试</span></el-badge>
            </el-menu-item>
            <el-menu-item index="/manager/course" title="课程管理" aria-label="课程管理" v-if="userInfo?.role === 'admin'">
              <el-icon><Reading /></el-icon>
              <span>课程管理</span>
            </el-menu-item>
            <el-menu-item index="/manager/document" title="知识库文档" aria-label="知识库文档" v-if="userInfo?.role === 'admin'">
              <el-icon><DocumentIcon /></el-icon>
              <span>知识库文档</span>
            </el-menu-item>
            <el-menu-item index="/manager/schedule-manage" title="课表管理" aria-label="课表管理" v-if="userInfo?.role === 'admin'">
              <el-icon><Calendar /></el-icon>
              <span>课表管理</span>
            </el-menu-item>
            <el-menu-item index="/manager/user" title="用户管理" aria-label="用户管理" v-if="userInfo?.role === 'admin'">
              <el-icon><User /></el-icon>
              <span>用户管理</span>
            </el-menu-item>
            <el-menu-item index="/manager/help" title="使用指南" aria-label="使用指南">
              <el-icon><QuestionFilled /></el-icon>
              <span>使用指南</span>
            </el-menu-item>
          </el-menu>
        </el-aside>
        <el-main style="min-width: 0">
          <router-view />
        </el-main>
      </el-container>
    </el-container>
  </div>
</template>

<script setup>
import router from '@/router'
import { logout } from '@/utils/auth'
import { useUser } from '@/utils/user'
import { onMounted } from 'vue'
import { useLearningInbox } from '@/stores/learningInbox'
import { Menu as IconMenu, Reading, Calendar, User, Notebook, ChatDotRound, Document as DocumentIcon, QuestionFilled, Tickets } from '@element-plus/icons-vue'

const { userInfo } = useUser()
const inbox = useLearningInbox()
onMounted(() => { if (userInfo.value?.role === 'student') inbox.refresh(true).catch(() => {}) })

const handleCommand = (command) => {
  if (command === 'profile') {
    router.push('/manager/profile')
  } else if (command === 'password') {
    router.push('/manager/password')
  } else if (command === 'logout') {
    logout()
    router.push('/login')
  }
}
</script>

<style scoped>
.assignment-badge :deep(.el-badge__content) { top: 7px; right: -10px; }
/* The wide timetable keeps usable space on phones; other routes retain their sidebar. */
@media (max-width: 720px) {
  .week-view-aside { width: 64px !important; flex-shrink: 0; overflow-x: hidden; }
  .week-view-aside :deep(.el-menu-item) { justify-content: center; padding: 0 !important; }
  .week-view-aside :deep(.el-menu-item > span) { display: none; }
  .week-view-aside :deep(.el-menu-item .el-icon) { margin-right: 0; width: 24px; }
}
</style>
