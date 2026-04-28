<template>
  <SetupPage v-if="needSetup" @configured="onSetupDone" />

  <div v-else-if="!authenticated" class="min-h-screen flex items-center justify-center p-4">
    <div class="w-full max-w-sm rounded-3xl border border-slate-800 bg-slate-950 p-8 shadow-2xl shadow-slate-950/60">
      <div class="mb-6 text-center">
        <div class="mb-3 text-xs uppercase tracking-[0.35em] text-cyan-400">AutoTeam</div>
        <h1 class="text-2xl font-semibold text-white">控制台登录</h1>
        <p class="mt-2 text-sm text-slate-400">请输入 API Key 进入多母号管理面板。</p>
      </div>

      <div v-if="authError" class="mb-4 rounded-2xl border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-300">
        {{ authError }}
      </div>

      <input
        v-model.trim="inputKey"
        type="password"
        placeholder="API Key"
        class="w-full rounded-2xl border border-slate-800 bg-slate-900 px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-500"
        @keyup.enter="doLogin"
      />

      <button
        class="mt-4 w-full rounded-2xl bg-cyan-500 px-4 py-3 text-sm font-medium text-slate-950 transition hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-50"
        :disabled="!inputKey || authLoading"
        @click="doLogin"
      >
        {{ authLoading ? '验证中...' : '登录' }}
      </button>
    </div>
  </div>

  <div v-else class="min-h-screen bg-[radial-gradient(circle_at_top,#0f172a,#020617_60%)] text-white">
    <div class="mx-auto max-w-7xl px-4 py-6 md:px-8">
      <header class="mb-6 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div class="text-xs uppercase tracking-[0.35em] text-cyan-400">AutoTeam</div>
          <h1 class="mt-2 text-3xl font-semibold">多母号批量邀请控制台</h1>
          <p class="mt-2 text-sm text-slate-400">Freemail 自动收码，母号独立工作空间，子号成功后自动同步状态。</p>
        </div>
        <div class="flex flex-wrap gap-3">
          <button class="rounded-2xl border border-slate-700 px-4 py-2 text-sm text-slate-200 transition hover:border-cyan-500 hover:text-white" @click="refresh">
            刷新
          </button>
          <button class="rounded-2xl border border-slate-700 px-4 py-2 text-sm text-slate-200 transition hover:border-rose-500 hover:text-white" @click="doLogout">
            退出
          </button>
        </div>
      </header>

      <div class="mb-6 flex flex-wrap gap-3">
        <button
          v-for="item in tabs"
          :key="item.key"
          class="rounded-full px-4 py-2 text-sm transition"
          :class="currentPage === item.key ? 'bg-cyan-500 text-slate-950' : 'bg-slate-900/70 text-slate-300 hover:text-white'"
          @click="currentPage = item.key"
        >
          {{ item.label }}
        </button>
      </div>

      <div
        v-if="loginStatus.in_progress"
        class="mb-4 rounded-2xl border border-amber-500/20 bg-amber-500/10 px-4 py-3 text-sm text-amber-100"
      >
        <div>母号登录进行中：{{ loginStatus.email || '-' }}</div>
        <div class="mt-1">{{ loginStatus.message || loginStatus.step || '处理中' }}</div>
        <div v-if="loginStatus.detail" class="mt-1 text-xs text-amber-200/80">{{ loginStatus.detail }}</div>
      </div>

      <div
        v-if="runningTask"
        class="mb-4 rounded-2xl border border-cyan-500/20 bg-cyan-500/10 px-4 py-3 text-sm text-cyan-100"
      >
        当前任务：{{ runningTask.command }} / {{ runningTask.status }}
      </div>

      <div
        v-if="actionError"
        class="mb-4 rounded-2xl border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-200"
      >
        {{ actionError }}
      </div>

      <DashboardPage
        v-if="currentPage === 'dashboard'"
        :status="status"
        :tasks="tasks"
        :running-task="runningTask"
        @batch-run="startBatchRun"
        @fill-all="startFillAll"
        @check-health="startCheckChildHealth"
        @delete-401="startDelete401Children"
        @repair="startRepairStuckAccounts"
        @repair-links="startRepairParentChildLinks"
        @clear-pending="startClearPendingInvites"
        @resync="resyncCpa"
      />

      <ParentsPage
        v-else-if="currentPage === 'parents'"
        :parents="status.parents || []"
        :login-status="loginStatus"
        :main-codex-status="mainCodexStatus"
        :invite-state="inviteState"
        :blocked-state="blockedState"
        :bulk-import-result="bulkImportResult"
        @refresh="refresh"
        @create="createParent"
        @bulk-import="bulkImportParents"
        @update="updateParent"
        @remove="removeParent"
        @start-login="startParentLogin"
        @login-all="startLoginAllParents"
        @import-session="importParentSession"
        @submit-password="submitParentPassword"
        @submit-code="submitParentCode"
        @submit-workspace="submitParentWorkspace"
        @cancel-login="cancelParentLogin"
        @start-main-codex="startParentMainCodex"
        @submit-main-codex-password="submitParentMainCodexPassword"
        @submit-main-codex-code="submitParentMainCodexCode"
        @cancel-main-codex="cancelParentMainCodex"
        @clear-pending-all="startClearPendingInvites"
        @load-invites="loadParentInvites"
        @cancel-invite="cancelParentInvite"
        @load-blocked="loadParentBlockedMembers"
        @remove-blocked="removeParentBlockedMember"
        @remove-all-blocked="removeAllParentBlockedMembers"
      />

      <ChildrenPage v-else-if="currentPage === 'children'" :children="status.children || []" />
      <SettingsPage v-else-if="currentPage === 'settings'" @saved="refresh" />
      <LogViewer v-else />
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, clearApiKey, setApiKey } from './api.js'
import SetupPage from './components/SetupPage.vue'
import DashboardPage from './components/DashboardPage.vue'
import ParentsPage from './components/ParentsPage.vue'
import ChildrenPage from './components/ChildrenPage.vue'
import SettingsPage from './components/SettingsPage.vue'
import LogViewer from './components/LogViewer.vue'

const tabs = [
  { key: 'dashboard', label: '总览' },
  { key: 'parents', label: '母号管理' },
  { key: 'children', label: '子号记录' },
  { key: 'settings', label: '设置' },
  { key: 'logs', label: '日志' },
]

const needSetup = ref(false)
const authenticated = ref(false)
const authLoading = ref(false)
const authError = ref('')
const inputKey = ref('')
const currentPage = ref('dashboard')

const status = ref({ parents: [], children: [], summary: {} })
const tasks = ref([])
const loginStatus = ref({ in_progress: false, workspace_options: [], detail: '', message: '', auto_mode: false })
const mainCodexStatus = ref({ in_progress: false, detail: '', message: '', auto_mode: false })
const inviteState = ref({})
const blockedState = ref({})
const bulkImportResult = ref(null)
const actionError = ref('')
let pollTimer = null

const runningTask = computed(() => tasks.value.find(task => task.status === 'pending' || task.status === 'running') || null)

async function checkAuth() {
  try {
    const result = await api.checkAuth()
    authenticated.value = result.authenticated
    return result.authenticated
  } catch (error) {
    if (error.status === 401) {
      authenticated.value = false
      return false
    }
    authenticated.value = true
    return true
  }
}

async function checkSetup() {
  try {
    const result = await api.getSetupStatus()
    return result.configured
  } catch {
    return true
  }
}

async function refresh() {
  const [statusResult, tasksResult, loginResult, mainCodexResult] = await Promise.all([
    api.getStatus(),
    api.getTasks(),
    api.getParentLoginStatus(),
    api.getParentMainCodexStatus(),
  ])
  status.value = statusResult
  tasks.value = tasksResult
  loginStatus.value = loginResult
  mainCodexStatus.value = mainCodexResult
}

async function runAction(action) {
  actionError.value = ''
  try {
    await action()
  } catch (error) {
    actionError.value = error?.message || '操作失败'
  }
}

async function doLogin() {
  authLoading.value = true
  authError.value = ''
  try {
    setApiKey(inputKey.value)
    const ok = await checkAuth()
    if (!ok) {
      clearApiKey()
      authError.value = 'API Key 无效'
      return
    }
    inputKey.value = ''
    await refresh()
    startPolling()
  } catch (error) {
    clearApiKey()
    authError.value = error.message
  } finally {
    authLoading.value = false
  }
}

function doLogout() {
  clearApiKey()
  authenticated.value = false
  stopPolling()
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    try {
      await refresh()
    } catch (error) {
      if (error.status === 401) {
        doLogout()
      }
    }
  }, 5000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function createParent(payload) {
  await runAction(async () => {
    await api.createParent(payload)
    await refresh()
  })
}

async function bulkImportParents(payload) {
  await runAction(async () => {
    const parents = Array.isArray(payload) ? payload : payload.parents
    const defaultBatchSize = Array.isArray(payload) ? 1 : payload.defaultBatchSize
    bulkImportResult.value = await api.bulkImportParents(parents, defaultBatchSize)
    await refresh()
  })
}

async function updateParent({ id, payload }) {
  await runAction(async () => {
    await api.updateParent(id, payload)
    await refresh()
  })
}

async function removeParent(id) {
  await runAction(async () => {
    await api.deleteParent(id)
    delete inviteState.value[id]
    delete blockedState.value[id]
    await refresh()
  })
}

async function startParentLogin({ id, email }) {
  await runAction(async () => {
    await api.startParentLogin(id, email)
    await refresh()
  })
}

async function startLoginAllParents(payload = {}) {
  await runAction(async () => {
    const concurrency = typeof payload === 'object' ? payload.concurrency : payload
    await api.startLoginAllParents(concurrency)
    await refresh()
  })
}

async function importParentSession({ id, email, sessionToken }) {
  await runAction(async () => {
    await api.importParentSession(id, email, sessionToken)
    await refresh()
  })
}

async function submitParentPassword({ id, password }) {
  await runAction(async () => {
    await api.submitParentPassword(id, password)
    await refresh()
  })
}

async function submitParentCode({ id, code }) {
  await runAction(async () => {
    await api.submitParentCode(id, code)
    await refresh()
  })
}

async function submitParentWorkspace({ id, optionId }) {
  await runAction(async () => {
    await api.submitParentWorkspace(id, optionId)
    await refresh()
  })
}

async function cancelParentLogin(id) {
  await runAction(async () => {
    await api.cancelParentLogin(id)
    await refresh()
  })
}

async function startParentMainCodex(id) {
  await runAction(async () => {
    await api.startParentMainCodex(id)
    await refresh()
  })
}

async function submitParentMainCodexPassword({ id, password }) {
  await runAction(async () => {
    await api.submitParentMainCodexPassword(id, password)
    await refresh()
  })
}

async function submitParentMainCodexCode({ id, code }) {
  await runAction(async () => {
    await api.submitParentMainCodexCode(id, code)
    await refresh()
  })
}

async function cancelParentMainCodex(id) {
  await runAction(async () => {
    await api.cancelParentMainCodex(id)
    await refresh()
  })
}

function updateInviteState(parentId, patch) {
  inviteState.value[parentId] = {
    invites: [],
    loading: false,
    error: '',
    ...(inviteState.value[parentId] || {}),
    ...patch,
  }
}

async function loadParentInvites(parentId) {
  updateInviteState(parentId, { loading: true, error: '' })
  try {
    const result = await api.getParentInvites(parentId)
    updateInviteState(parentId, { invites: result.invites || [], loading: false, error: '' })
  } catch (error) {
    updateInviteState(parentId, { loading: false, error: error?.message || '加载待邀请失败' })
  }
}

async function cancelParentInvite({ parentId, inviteId }) {
  updateInviteState(parentId, { loading: true, error: '' })
  try {
    await api.cancelParentInvite(parentId, inviteId)
    await refresh()
    await loadParentInvites(parentId)
  } catch (error) {
    updateInviteState(parentId, { loading: false, error: error?.message || '取消邀请失败' })
  }
}

function updateBlockedState(parentId, patch) {
  blockedState.value[parentId] = {
    members: [],
    loading: false,
    error: '',
    ...(blockedState.value[parentId] || {}),
    ...patch,
  }
}

async function loadParentBlockedMembers(parentId) {
  updateBlockedState(parentId, { loading: true, error: '' })
  try {
    const result = await api.getParentBlockedMembers(parentId)
    updateBlockedState(parentId, { members: result.members || [], loading: false, error: '' })
  } catch (error) {
    updateBlockedState(parentId, { loading: false, error: error?.message || '加载疑似封禁子号失败' })
  }
}

async function removeParentBlockedMember({ parentId, childId }) {
  updateBlockedState(parentId, { loading: true, error: '' })
  try {
    await api.removeBlockedMember(parentId, childId)
    await refresh()
    await loadParentBlockedMembers(parentId)
  } catch (error) {
    updateBlockedState(parentId, { loading: false, error: error?.message || '移出 Team 成员失败' })
  }
}

async function removeAllParentBlockedMembers(parentId) {
  updateBlockedState(parentId, { loading: true, error: '' })
  try {
    await api.removeAllBlockedMembers(parentId)
    await refresh()
    await loadParentBlockedMembers(parentId)
  } catch (error) {
    updateBlockedState(parentId, { loading: false, error: error?.message || '批量移出 Team 成员失败' })
  }
}

async function startBatchRun() {
  await runAction(async () => {
    await api.startBatchRun()
    await refresh()
  })
}

async function startFillAll() {
  await runAction(async () => {
    await api.startFillAll()
    await refresh()
  })
}

async function startCheckChildHealth(payload = {}) {
  await runAction(async () => {
    const concurrency = typeof payload === 'object' ? payload.concurrency : payload
    await api.startCheckChildHealth(concurrency)
    await refresh()
  })
}

async function startDelete401Children(payload = {}) {
  await runAction(async () => {
    const concurrency = typeof payload === 'object' ? payload.concurrency : payload
    await api.startDelete401Children(concurrency)
    await refresh()
  })
}

async function startRepairStuckAccounts() {
  await runAction(async () => {
    await api.startRepairStuckAccounts()
    await refresh()
  })
}

async function startRepairParentChildLinks(payload = {}) {
  await runAction(async () => {
    const concurrency = typeof payload === 'object' ? payload.concurrency : payload
    await api.startRepairParentChildLinks(concurrency)
    await refresh()
  })
}

async function startClearPendingInvites() {
  await runAction(async () => {
    await api.startClearPendingInvites()
    await refresh()
  })
}

async function resyncCpa() {
  await runAction(async () => {
    await api.resyncCpa()
    await refresh()
  })
}

function onSetupDone() {
  needSetup.value = false
  checkAuth().then(async ok => {
    if (ok) {
      await refresh()
      startPolling()
    }
  })
}

onMounted(async () => {
  const setupOk = await checkSetup()
  if (!setupOk) {
    needSetup.value = true
    return
  }

  const ok = await checkAuth()
  if (ok) {
    await refresh()
    startPolling()
  }
})

onUnmounted(() => {
  stopPolling()
})
</script>
