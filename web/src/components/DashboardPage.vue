<template>
  <div class="space-y-6">
    <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-6 2xl:grid-cols-10">
      <div
        v-for="card in cards"
        :key="card.label"
        class="rounded-3xl border border-slate-800 bg-slate-950/70 p-5 shadow-[0_20px_60px_rgba(2,6,23,0.35)]"
      >
        <div class="text-xs uppercase tracking-[0.3em] text-slate-500">{{ card.label }}</div>
        <div class="mt-3 text-3xl font-semibold text-white">{{ card.value }}</div>
        <div class="mt-2 text-xs text-slate-400">{{ card.hint }}</div>
      </div>
    </div>

    <div class="grid gap-6 xl:grid-cols-[1.2fr,0.8fr]">
      <section class="rounded-3xl border border-slate-800 bg-slate-950/70 p-6">
        <div class="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 class="text-xl font-semibold text-white">任务面板</h2>
            <p class="mt-1 text-sm text-slate-400">
              一键补满现在会先对账远端占位，再优先恢复卡住账号，最后才补新号。
            </p>
          </div>
          <div class="flex flex-wrap gap-3">
            <label class="flex items-center gap-2 rounded-2xl border border-slate-800 bg-slate-900/70 px-3 py-2 text-xs text-slate-400">
              检测并发
              <input v-model.number="healthCheckConcurrency" type="number" min="1" max="5" class="w-14 bg-transparent text-sm text-white outline-none" />
            </label>
            <button class="action-btn action-btn-cyan" @click="$emit('batch-run')">批量创建</button>
            <button class="action-btn action-btn-emerald" @click="$emit('fill-all')">一键补满</button>
            <span class="rounded-2xl border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-100">
              目标：每母号 {{ fillAllTarget }} 个
            </span>
            <button class="action-btn action-btn-rose" @click="emitCheckHealth">检测封禁子号</button>
            <button class="action-btn action-btn-danger" @click="emitDelete401">一键删除401账号</button>
            <label class="flex items-center gap-2 rounded-2xl border border-slate-800 bg-slate-900/70 px-3 py-2 text-xs text-slate-400">
              关联并发
              <input v-model.number="repairLinksConcurrency" type="number" min="1" max="5" class="w-14 bg-transparent text-sm text-white outline-none" />
            </label>
            <button class="action-btn action-btn-cyan" @click="emitRepairLinks">修复母子关联</button>
            <button class="action-btn action-btn-amber" @click="$emit('repair')">修复卡住账号</button>
            <button class="action-btn action-btn-slate" @click="$emit('clear-pending')">清空待邀请</button>
            <button class="action-btn action-btn-slate" @click="$emit('resync')">补传子号 CPA</button>
          </div>
        </div>

        <div class="mt-6 overflow-hidden rounded-2xl border border-slate-800">
          <table class="min-w-full divide-y divide-slate-800 text-sm">
            <thead class="bg-slate-900/80 text-left text-slate-400">
              <tr>
                <th class="px-4 py-3">任务</th>
                <th class="px-4 py-3">状态</th>
                <th class="px-4 py-3">开始时间</th>
                <th class="px-4 py-3">结果</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-900 bg-slate-950/40">
              <tr v-for="task in tasks.slice(0, 10)" :key="task.task_id">
                <td class="px-4 py-3 text-white">{{ task.command }}</td>
                <td class="px-4 py-3">
                  <span class="rounded-full px-2.5 py-1 text-xs" :class="taskStatusClass(task.status)">
                    {{ taskStatusLabel(task.status) }}
                  </span>
                </td>
                <td class="px-4 py-3 text-slate-400">{{ formatTime(task.started_at || task.created_at) }}</td>
                <td class="px-4 py-3 text-slate-300">{{ formatResult(task) }}</td>
              </tr>
              <tr v-if="tasks.length === 0">
                <td colspan="4" class="px-4 py-8 text-center text-slate-500">暂无任务记录</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="rounded-3xl border border-slate-800 bg-slate-950/70 p-6">
        <div class="flex items-start justify-between gap-3">
          <div>
            <h2 class="text-xl font-semibold text-white">最近子号</h2>
            <p class="mt-1 text-sm text-slate-400">重点看正在占位但还没收敛的账号。</p>
          </div>
          <div class="rounded-full border border-slate-800 bg-slate-900/70 px-3 py-1 text-xs text-slate-300">
            {{ latestChildren.length }} 条
          </div>
        </div>

        <div class="mt-4 space-y-3">
          <div
            v-for="child in latestChildren"
            :key="child.id"
            class="rounded-2xl border border-slate-800 bg-[linear-gradient(135deg,rgba(15,23,42,0.9),rgba(2,6,23,0.7))] p-4"
          >
            <div class="flex items-center justify-between gap-3">
              <div>
                <div class="font-medium text-white">{{ child.email }}</div>
                <div class="mt-1 text-xs text-slate-400">
                  {{ child.parent_email || '未归属母号' }} / {{ child.workspace_name || '-' }}
                </div>
              </div>
              <span class="rounded-full px-2.5 py-1 text-xs" :class="statusClass(child.status)">
                {{ statusLabel(child.status) }}
              </span>
            </div>
            <div class="mt-3 flex flex-wrap gap-2 text-xs text-slate-400">
              <span class="rounded-full bg-slate-900/80 px-3 py-1">远端 {{ remoteStateLabel(child.remote_state) }}</span>
              <span v-if="child.health_status && child.health_status !== 'unknown'" class="rounded-full bg-slate-900/80 px-3 py-1">
                健康 {{ healthStatusLabel(child.health_status) }}
              </span>
              <span class="rounded-full bg-slate-900/80 px-3 py-1">阶段 {{ errorStageLabel(child.error_stage) }}</span>
              <span class="rounded-full bg-slate-900/80 px-3 py-1">{{ child.auth_file ? 'Auth 已保存' : 'Auth 未保存' }}</span>
              <span class="rounded-full bg-slate-900/80 px-3 py-1">{{ child.cpa_uploaded_at ? 'CPA 已上传' : 'CPA 未上传' }}</span>
            </div>
          </div>

          <div
            v-if="latestChildren.length === 0"
            class="rounded-2xl border border-dashed border-slate-800 p-6 text-center text-sm text-slate-500"
          >
            暂无子号记录
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  status: { type: Object, required: true },
  tasks: { type: Array, default: () => [] },
  runningTask: { type: Object, default: null },
})

const emit = defineEmits(['batch-run', 'fill-all', 'check-health', 'delete-401', 'repair-links', 'repair', 'clear-pending', 'resync'])

const summary = computed(() => props.status.summary || {})
const latestChildren = computed(() => [...(props.status.children || [])].slice(-6).reverse())
const fillAllTarget = computed(() => summary.value.target_children_per_parent || 4)
const healthCheckConcurrency = ref(5)
const repairLinksConcurrency = ref(2)
const cards = computed(() => [
  { label: '启用母号', value: summary.value.enabled_parents || 0, hint: '参与执行的母号数量' },
  { label: '已完成', value: summary.value.ready_children || 0, hint: 'Auth 与 CPA 均已完成' },
  { label: '已加入待授权', value: summary.value.accepted_children || 0, hint: '远端已是成员，但本地还没产出 Auth' },
  { label: '已授权待 CPA', value: summary.value.auth_saved_children || 0, hint: 'Auth 已保存，CPA 还可补传' },
  { label: '疑似封禁', value: summary.value.blocked_children || 0, hint: '仍在 Team 内占位，需要人工确认移出' },
  { label: '待移出', value: summary.value.pending_removal_children || 0, hint: 'blocked + member，移出后才释放席位' },
  { label: '额度耗尽', value: summary.value.quota_exhausted_children || 0, hint: '429 usage_limit_reached，不释放席位' },
  { label: '已移出', value: summary.value.removed_children || 0, hint: '远端成员已移出，可由一键补满补位' },
  { label: '可恢复', value: summary.value.recoverable_children || 0, hint: '下次会优先修复，不会先乱补号' },
  { label: '漂移母号', value: summary.value.drift_parents || 0, hint: '远端有占位，但本地未完全匹配' },
])

function formatTime(ts) {
  if (!ts) return '-'
  const date = new Date(ts * 1000)
  return `${date.toLocaleDateString()} ${date.toLocaleTimeString()}`
}

function repairConcurrencyValue() {
  const next = Number(repairLinksConcurrency.value)
  const normalized = Number.isFinite(next) ? Math.trunc(next) : 2
  return Math.min(5, Math.max(1, normalized))
}

function healthConcurrencyValue() {
  const next = Number(healthCheckConcurrency.value)
  const normalized = Number.isFinite(next) ? Math.trunc(next) : 5
  return Math.min(5, Math.max(1, normalized))
}

function emitCheckHealth() {
  const concurrency = healthConcurrencyValue()
  healthCheckConcurrency.value = concurrency
  emit('check-health', { concurrency })
}

function emitDelete401() {
  const concurrency = healthConcurrencyValue()
  healthCheckConcurrency.value = concurrency
  const confirmed = window.confirm(
    '确认一键删除 401 账号吗？\n\n系统会先检测子号健康状态，再自动移出疑似封禁的 Team 成员；不会删除本地 auth/CPA 文件。移出后需要手动点击“一键补满”。',
  )
  if (!confirmed) return
  emit('delete-401', { concurrency })
}

function emitRepairLinks() {
  const concurrency = repairConcurrencyValue()
  repairLinksConcurrency.value = concurrency
  emit('repair-links', { concurrency })
}

function taskStatusClass(status) {
  if (status === 'completed') return 'bg-emerald-500/15 text-emerald-300'
  if (status === 'failed') return 'bg-rose-500/15 text-rose-300'
  return 'bg-cyan-500/15 text-cyan-200'
}

function taskStatusLabel(status) {
  if (status === 'pending') return '等待中'
  if (status === 'running') return '执行中'
  if (status === 'completed') return '已完成'
  if (status === 'failed') return '失败'
  return status || '-'
}

function formatResult(task) {
  if (task.error) return task.error
  if (task.result?.removed != null || task.result?.remove_failed != null) {
    return `检测 ${task.result.checked || 0} / 401标记 ${task.result.blocked || 0} / 已移出 ${task.result.removed || 0} / 移出失败 ${task.result.remove_failed || 0} / 额度耗尽 ${task.result.quota_exhausted || 0} / 检测失败 ${task.result.check_failed || 0}`
  }
  if (task.result?.cancelled != null) {
    return `清理 ${task.result.cancelled} / 对账 ${task.result.stale_local || 0} / 远端成员 ${task.result.remote_members || 0} / 失败 ${task.result.failed || 0}`
  }
  if (task.result?.uploaded != null) {
    return `上传 ${task.result.uploaded} / 修复引用 ${task.result.repaired || 0} / 失败 ${task.result.failed || 0}`
  }
  if (task.result?.checked != null) {
    return `检测 ${task.result.checked} / 正常 ${task.result.healthy || 0} / 额度耗尽 ${task.result.quota_exhausted || 0} / 疑似封禁 ${task.result.blocked || 0} / 失败 ${task.result.check_failed || 0}`
  }
  if (task.result?.logged_in != null || task.result?.manual_required != null) {
    return `登录 ${task.result.logged_in || 0} / 需人工 ${task.result.manual_required || 0} / 跳过 ${task.result.skipped || 0} / 失败 ${task.result.failed || 0}`
  }
  if (task.result?.relinked != null || task.result?.created_from_auth != null) {
    return `关联 ${task.result.relinked || 0} / 从 Auth 补建 ${task.result.created_from_auth || 0} / 远端无本地 ${task.result.remote_only || 0} / 冲突 ${task.result.conflicts || 0} / 阻塞母号 ${task.result.blocked_parents || 0} / 失败 ${task.result.failed || 0}`
  }
  if (task.result?.created != null || task.result?.recovered != null) {
    const created = task.result?.created || 0
    const recovered = task.result?.recovered || 0
    const failed = task.result?.failed || 0
    const blocked = task.result?.blocked_parents || 0
    const target = task.result?.target_per_parent || '-'
    return `目标 ${target} / 新建 ${created} / 修复 ${recovered} / 失败 ${failed}${blocked ? ` / 阻塞母号 ${blocked}` : ''}`
  }
  return '-'
}

function statusClass(status) {
  if (status === 'blocked') return 'bg-rose-500/15 text-rose-200'
  if (status === 'removed') return 'bg-slate-700/70 text-slate-200'
  if (status === 'ready') return 'bg-emerald-500/15 text-emerald-300'
  if (status === 'auth_saved') return 'bg-cyan-500/15 text-cyan-200'
  if (status === 'accepted') return 'bg-sky-500/15 text-sky-200'
  if (status === 'failed') return 'bg-rose-500/15 text-rose-300'
  if (status === 'cancelled') return 'bg-slate-700 text-slate-200'
  return 'bg-amber-500/15 text-amber-200'
}

function statusLabel(status) {
  if (status === 'blocked') return '疑似封禁'
  if (status === 'removed') return '已移出'
  if (status === 'invited') return '待接受'
  if (status === 'accepted') return '已加入待授权'
  if (status === 'auth_saved') return '已授权待 CPA'
  if (status === 'ready') return '已完成'
  if (status === 'failed') return '失败未占位'
  if (status === 'cancelled') return '已取消'
  return status || '-'
}

function healthStatusLabel(status) {
  if (status === 'healthy') return '正常'
  if (status === 'quota_exhausted') return '额度耗尽'
  if (status === 'auth_error') return '认证失效'
  if (status === 'check_failed') return '检测失败'
  return '未知'
}

function remoteStateLabel(remoteState) {
  if (remoteState === 'pending_invite') return '待邀请'
  if (remoteState === 'member') return '已加入'
  if (remoteState === 'absent') return '不存在'
  return '未知'
}

function errorStageLabel(stage) {
  if (stage === 'invite') return '发邀请'
  if (stage === 'registration') return '注册接受'
  if (stage === 'codex') return '授权'
  if (stage === 'cpa') return 'CPA'
  if (stage === 'reconcile') return '对账'
  if (stage === 'unknown') return '未知'
  return stage || '无错误'
}
</script>

<style scoped>
.action-btn {
  @apply rounded-2xl px-4 py-2 text-sm font-medium transition;
}

.action-btn-cyan {
  @apply bg-cyan-500 text-slate-950 hover:bg-cyan-400;
}

.action-btn-emerald {
  @apply bg-emerald-500 text-slate-950 hover:bg-emerald-400;
}

.action-btn-amber {
  @apply bg-amber-400 text-slate-950 hover:bg-amber-300;
}

.action-btn-rose {
  @apply bg-rose-500 text-white hover:bg-rose-400;
}

.action-btn-danger {
  @apply border border-rose-500/40 bg-rose-500/15 text-rose-100 hover:border-rose-400 hover:bg-rose-500/25 hover:text-white;
}

.action-btn-slate {
  @apply border border-slate-700 text-slate-200 hover:border-cyan-500 hover:text-white;
}
</style>
