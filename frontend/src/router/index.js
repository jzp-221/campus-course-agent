import { getToken, getUserInfo } from '@/utils/auth'
import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', redirect: '/manager/home' },
    {
      path: '/manager',
      name: 'Layout',
      component: () => import('@/layouts/Layout.vue'),
      children: [
        { path: 'home', name: 'Home', component: () => import('@/views/Home.vue') },
        { path: 'profile', name: 'Profile', component: () => import('@/views/Profile.vue') },
        { path: 'password', name: 'Password', component: () => import('@/views/Password.vue') },
        { path: 'user', name: 'User', component: () => import('@/views/User.vue') },
        { path: 'course', name: 'Course', component: () => import('@/views/Course.vue') },
        { path: 'course/:id', name: 'CourseDetail', component: () => import('@/views/CourseDetail.vue') },
        { path: 'document', name: 'Document', component: () => import('@/views/Document.vue'), meta: { requiresAdmin: true } },
        { path: 'schedule', name: 'Schedule', component: () => import('@/views/Schedule.vue') },
        { path: 'schedule-week', name: 'ScheduleWeek', component: () => import('@/views/ScheduleWeek.vue') },
        { path: 'study-plan', name: 'StudyPlan', component: () => import('@/views/StudyPlan.vue') },
        { path: 'assignment', name: 'Assignment', component: () => import('@/views/Assignment.vue') },
        { path: 'assistant', name: 'Assistant', component: () => import('@/views/Chat.vue') },
        { path: 'schedule-manage', name: 'ScheduleManage', component: () => import('@/views/ScheduleManage.vue') },
        { path: 'help', name: 'Help', component: () => import('@/views/Help.vue') },
      ]
    },
    { path: '/login', name: 'Login', component: () => import('@/views/Login.vue') },
    { path: '/register', name: 'Register', component: () => import('@/views/Register.vue') }
  ]
})

// 路由守卫：验证token
router.beforeEach((to, from) => {
  const token = getToken()

  if (to.path === '/login' || to.path === '/register') {
    return true
  } else {
    if (!token) return '/login'
    if (to.meta.requiresAdmin && getUserInfo()?.role !== 'admin') return '/manager/home'
    return true
  }
})

export default router
