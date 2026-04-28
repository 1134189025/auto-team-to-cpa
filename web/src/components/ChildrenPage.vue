<template>
  <section class="rounded-3xl border border-slate-800 bg-slate-950/70 p-6">
    <div class="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
      <div>
        <h2 class="text-xl font-semibold">子号记录</h2>
        <p class="mt-1 text-sm text-slate-400">
          这里会同时展示本地状态、远端 Team 状态和卡住阶段，方便判断该修复、补 CPA 还是继续补号。
        </p>
      </div>
      <div class="text-sm text-slate-500">共 {{ orderedChildren.length }} 条</div>
    </div>

    <div class="mt-6 overflow-hidden rounded-2xl border border-slate-800">
      <table class="min-w-full divide-y divide-slate-800 text-sm">
        <thead class="bg-slate-900/80 text-left text-slate-400">
          <tr>
            <th class="px-4 py-3">邮箱</th>
            <th class="px-4 py-3">母号</th>
            <th class="px-4 py-3">状态</th>
            <th class="px-4 py-3">远端</th>
            <th class="px-4 py-3">卡住阶段</th>
            <th class="px-4 py-3">Auth</th>
            <th class="px-4 py-3">CPA</th>
            <th class="px-4 py-3">错误</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-900 bg-slate-950/40">
          <tr v-for="child in orderedChildren" :key="child.id">
            <td class="px-4 py-3 text-white">
              <div>{{ child.email }}</div>
              <div class="mt-1 text-xs text-slate-500">{{ child.workspace_name || '-' }}</div>
            </td>
            <td class="px-4 py-3 text-slate-300">
              <div>{{ child.parent_email || '-' }}</div>
              <div class="mt-1 text-xs text-slate-500">{{ child.mail_provider || '-' }}</div>
            </td>
            <td class="px-4 py-3">
              <span class="rounded-full px-2.5 py-1 text-xs" :class="statusClass(child.status, child.health_status)">
                {{ statusLabel(child.status, child.health_status) }}
              </span>
            </td>
            <td class="px-4 py-3">
              <span class="rounded-full px-2.5 py-1 text-xs" :class="remoteStateClass(child.remote_state)">
                {{ remoteStateLabel(child.remote_state) }}
              </span>
            </td>
            <td class="px-4 py-3 text-slate-300">{{ errorStageLabel(child.error_stage) }}</td>
            <td class="px-4 py-3 text-slate-300">
              <div>{{ child.auth_file ? '已保存' : '未保存' }}</div>
              <div v-if="child.health_status && child.health_status !== 'unknown'" class="mt-1 text-xs text-slate-500">
                {{ healthStatusLabel(child.health_status) }}
              </div>
            </td>
            <td class="px-4 py-3 text-slate-300">{{ child.cpa_uploaded_at ? '已上传' : '未上传' }}</td>
            <td class="px-4 py-3 text-xs text-rose-200">{{ child.error || '-' }}</td>
          </tr>
          <tr v-if="orderedChildren.length === 0">
            <td colspan="8" class="px-4 py-8 text-center text-slate-500">暂无子号记录</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  children: { type: Array, default: () => [] },
})

const orderedChildren = computed(() => [...props.children].reverse())

function statusClass(status, healthStatus = '') {
  if (status === 'blocked') return 'bg-rose-500/15 text-rose-200'
  if (status === 'removed') return 'bg-slate-700/70 text-slate-200'
  if (healthStatus === 'quota_exhausted') return 'bg-amber-500/15 text-amber-200'
  if (status === 'ready') return 'bg-emerald-500/15 text-emerald-300'
  if (status === 'auth_saved') return 'bg-cyan-500/15 text-cyan-200'
  if (status === 'accepted') return 'bg-sky-500/15 text-sky-200'
  if (status === 'failed') return 'bg-rose-500/15 text-rose-300'
  if (status === 'cancelled') return 'bg-slate-700/70 text-slate-200'
  return 'bg-amber-500/15 text-amber-200'
}

function statusLabel(status, healthStatus = '') {
  if (status === 'blocked') return '疑似封禁'
  if (status === 'removed') return '已移出'
  if (healthStatus === 'quota_exhausted') return '额度耗尽'
  if (status === 'invited') return '待接受'
  if (status === 'accepted') return '已加入待授权'
  if (status === 'auth_saved') return '已授权待 CPA'
  if (status === 'ready') return '已完成'
  if (status === 'failed') return '失败未占位'
  if (status === 'cancelled') return '已取消'
  return status || '-'
}

function healthStatusLabel(status) {
  if (status === 'healthy') return '健康：正常'
  if (status === 'quota_exhausted') return '健康：额度耗尽'
  if (status === 'auth_error') return '健康：认证失效'
  if (status === 'check_failed') return '健康：检测失败'
  return '健康：未知'
}

function remoteStateClass(remoteState) {
  if (remoteState === 'member') return 'bg-emerald-500/15 text-emerald-300'
  if (remoteState === 'pending_invite') return 'bg-amber-500/15 text-amber-200'
  if (remoteState === 'absent') return 'bg-slate-700/70 text-slate-200'
  return 'bg-slate-800 text-slate-300'
}

function remoteStateLabel(remoteState) {
  if (remoteState === 'pending_invite') return '待邀请'
  if (remoteState === 'member') return '已加入'
  if (remoteState === 'absent') return '远端不存在'
  return '未知'
}

function errorStageLabel(stage) {
  if (stage === 'invite') return '发邀请'
  if (stage === 'registration') return '注册接受'
  if (stage === 'codex') return '授权'
  if (stage === 'cpa') return 'CPA 上传'
  if (stage === 'reconcile') return '远端对账'
  if (stage === 'unknown') return '未知'
  return '-'
}
</script>
