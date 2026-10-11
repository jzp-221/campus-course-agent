import { computed, ref, watch } from 'vue'
import { listAssignmentsApi } from '@/api/assignment'
import { useUser } from '@/utils/user'
import { getUserInfo } from '@/utils/auth'
import { fetchAllPages, pendingCount as countPending } from '@/utils/learningItems'

const assignments = ref([])
const loading = ref(false)
const error = ref('')
const loaded = ref(false)
let owner = null
let version = 0
let inFlight = null

function reset(nextOwner) {
  owner = nextOwner
  version++
  assignments.value = []
  error.value = ''
  loaded.value = false
  loading.value = false
  inFlight = null
}

export function useLearningInbox() {
  const { userInfo } = useUser()
  watch(() => userInfo.value?.id ?? null, id => {
    if (owner !== id) reset(id)
  }, { immediate: true, flush: 'sync' })

  async function refresh(force = false) {
    const id = userInfo.value?.id ?? null
    if (owner !== id) reset(id)
    if (!id) return
    if (inFlight) {
      if (!force) return inFlight
      // A write can finish while an older GET is still running. Fetch again after it
      // settles so completion badges and lists reflect the committed write.
      const previous = inFlight
      await previous.catch(() => {})
      if (owner !== id || userInfo.value?.id !== id) return
      if (inFlight) return inFlight
    }
    if (loaded.value && !force) return assignments.value
    const revision = ++version
    loading.value = true
    error.value = ''
    const task = (async () => {
      try {
        const rows = await fetchAllPages(listAssignmentsApi)
        if (owner !== id || version !== revision || getUserInfo()?.id !== id) return
        assignments.value = rows
        loaded.value = true
        return rows
      } catch (failure) {
        if (owner === id && version === revision) error.value = failure.message || '截止事项加载失败，请重试'
        throw failure
      } finally {
        if (version === revision) {
          loading.value = false
          inFlight = null
        }
      }
    })()
    inFlight = task
    return task
  }

  return { assignments, loading, error, loaded, pendingCount: computed(() => countPending(assignments.value)), refresh }
}
