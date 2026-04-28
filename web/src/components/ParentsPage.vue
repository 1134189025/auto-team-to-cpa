<template>
  <div class="space-y-6">
    <section class="rounded-3xl border border-slate-800 bg-slate-950/70 p-6">
      <div class="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div>
          <h2 class="text-xl font-semibold text-white">导入母号</h2>
          <p class="mt-1 text-sm text-slate-400">支持单个添加或批量粘贴，导入后可以一键登录所有未保存登录态的母号。</p>
        </div>
        <div class="flex flex-wrap items-center gap-3">
          <label class="flex items-center gap-2 text-xs text-slate-400">
            并发
            <input v-model.number="loginAllConcurrency" type="number" min="1" max="5" class="input w-24 py-2" />
          </label>
          <button class="btn btn-primary" @click="emitLoginAll">一键登录所有母号</button>
        </div>
      </div>

      <div class="mt-5 grid gap-5 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
        <div>
          <div class="grid gap-3 md:grid-cols-4 xl:grid-cols-1 2xl:grid-cols-4">
            <input v-model.trim="createForm.label" placeholder="备注名称" class="input" />
            <input v-model.trim="createForm.email" placeholder="母号邮箱" class="input" />
            <input
              v-model.number="createForm.default_batch_size"
              type="number"
              min="1"
              placeholder="默认批量数"
              class="input"
            />
            <button class="btn btn-primary" @click="submitCreate">新增母号</button>
          </div>
        </div>

        <div class="space-y-3">
          <textarea
            v-model="bulkText"
            rows="7"
            class="input min-h-[168px] resize-y font-mono leading-6"
            placeholder="每行一个母号：&#10;owner1@example.com,password1&#10;备注,owner2@example.com,password2,Team Workspace&#10;owner3@example.com----password3"
          ></textarea>
          <div class="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div class="flex items-center gap-3">
              <input v-model.number="bulkDefaultBatchSize" type="number" min="1" class="input w-32" placeholder="默认批量数" />
              <span class="text-xs text-slate-500">支持逗号、Tab、竖线、空格和 ---- 分隔。</span>
            </div>
            <button class="btn btn-primary" :disabled="!bulkText.trim()" @click="submitBulkImport">批量导入</button>
          </div>
          <div v-if="bulkError" class="rounded-2xl border border-rose-500/20 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
            {{ bulkError }}
          </div>
          <div
            v-else-if="bulkImportResult"
            class="rounded-2xl border border-cyan-500/20 bg-cyan-500/10 px-4 py-3 text-sm text-cyan-100"
          >
            导入结果：新建 {{ bulkImportResult.created || 0 }} / 更新 {{ bulkImportResult.updated || 0 }} /
            跳过 {{ bulkImportResult.skipped || 0 }} / 失败 {{ bulkImportResult.failed || 0 }}
            <div v-if="bulkImportResult.skipped || bulkImportResult.failed" class="mt-2 space-y-1 text-xs text-cyan-200/80">
              <div v-for="item in (bulkImportResult.items || []).filter(row => row.error).slice(0, 5)" :key="`${item.index}-${item.email}`">
                第 {{ item.index }} 行 {{ item.email || '-' }}：{{ item.error }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="rounded-3xl border border-slate-800 bg-slate-950/70 p-6">
      <div class="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h2 class="text-xl font-semibold text-white">母号管理</h2>
          <p class="mt-1 text-sm text-slate-400">
            这里重点看每个母号的远端占位、有效子号、待移出数量和漂移告警。
          </p>
        </div>
        <div class="flex flex-wrap gap-3">
          <label class="flex items-center gap-2 text-xs text-slate-400">
            并发
            <input v-model.number="loginAllConcurrency" type="number" min="1" max="5" class="input w-24 py-2" />
          </label>
          <button class="btn btn-primary" @click="emitLoginAll">一键登录所有母号</button>
          <button class="btn btn-invite" @click="$emit('clear-pending-all')">一键清空待邀请</button>
          <button class="btn" @click="$emit('refresh')">刷新</button>
        </div>
      </div>

      <div class="mt-6 space-y-4">
        <article
          v-for="parent in parents"
          :key="parent.id"
          class="rounded-3xl border border-slate-800 bg-[linear-gradient(135deg,rgba(15,23,42,0.92),rgba(2,6,23,0.84))] p-5"
        >
          <div class="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
            <div class="flex-1 space-y-4">
              <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
                <input
                  :value="draftValue(parent, 'label')"
                  class="input"
                  placeholder="备注名称"
                  @input="updateDraft(parent.id, 'label', $event.target.value)"
                />
                <input
                  :value="draftValue(parent, 'email')"
                  class="input"
                  placeholder="母号邮箱"
                  @input="updateDraft(parent.id, 'email', $event.target.value)"
                />
                <input
                  :value="draftValue(parent, 'workspace_name')"
                  class="input"
                  placeholder="工作空间名称"
                  @input="updateDraft(parent.id, 'workspace_name', $event.target.value)"
                />
                <input
                  :value="draftValue(parent, 'password')"
                  type="password"
                  class="input"
                  placeholder="母号密码（可选）"
                  @input="updateDraft(parent.id, 'password', $event.target.value)"
                />
                <input
                  :value="draftValue(parent, 'default_batch_size')"
                  type="number"
                  min="1"
                  class="input"
                  placeholder="默认批量数"
                  @input="updateDraft(parent.id, 'default_batch_size', toNumber($event.target.value, parent.default_batch_size))"
                />
              </div>

              <div class="flex flex-wrap gap-2 text-xs text-slate-400">
                <span class="tag">ID {{ parent.id.slice(0, 8) }}</span>
                <span class="tag">Session {{ parent.session_present ? '已保存' : '未保存' }}</span>
                <span class="tag">密码 {{ parent.password_saved ? '已保存' : '未保存' }}</span>
                <span class="tag">Account {{ parent.account_id || '-' }}</span>
                <span class="tag">Workspace {{ parent.workspace_name || '-' }}</span>
                <span class="tag">最近任务 {{ parent.last_run_status || '-' }}</span>
                <span class="tag">最近对账 {{ formatTime(parent.last_reconciled_at) }}</span>
              </div>

              <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
                <div class="metric-card">
                  <div class="metric-label">远端待邀请</div>
                  <div class="metric-value text-amber-200">{{ parent.remote_pending_count || 0 }}</div>
                  <div class="metric-hint">pending invite</div>
                </div>
                <div class="metric-card">
                  <div class="metric-label">远端已加入</div>
                  <div class="metric-value text-emerald-200">{{ parent.remote_member_count || 0 }}</div>
                  <div class="metric-hint">member</div>
                </div>
                <div class="metric-card">
                  <div class="metric-label">有效子号</div>
                  <div class="metric-value text-cyan-200">{{ usableMemberCount(parent) }}</div>
                  <div class="metric-hint">不含疑似封禁占位</div>
                </div>
                <div class="metric-card">
                  <div class="metric-label">待移出</div>
                  <div class="metric-value" :class="pendingRemovalCount(parent) > 0 ? 'text-rose-200' : 'text-slate-200'">
                    {{ pendingRemovalCount(parent) }}
                  </div>
                  <div class="metric-hint">blocked member</div>
                </div>
                <div class="metric-card">
                  <div class="metric-label">可恢复账号</div>
                  <div class="metric-value text-cyan-200">{{ parent.recoverable_count || 0 }}</div>
                  <div class="metric-hint">accepted / auth_saved</div>
                </div>
                <div class="metric-card">
                  <div class="metric-label">漂移告警</div>
                  <div class="metric-value" :class="(parent.drift_count || 0) > 0 ? 'text-rose-200' : 'text-slate-200'">
                    {{ parent.drift_count || 0 }}
                  </div>
                  <div class="metric-hint">远端有占位但本地没对上</div>
                </div>
              </div>

              <div
                v-if="pendingRemovalCount(parent) > 0"
                class="rounded-2xl border border-rose-500/25 bg-rose-500/10 px-4 py-3 text-sm text-rose-100"
              >
                检测到 {{ pendingRemovalCount(parent) }} 个疑似封禁子号仍在 Team 内占位。远端已加入
                {{ parent.remote_member_count || 0 }}，有效子号 {{ usableMemberCount(parent) }}；需要先人工移出，再手动点击“一键补满”补位。
              </div>

              <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <div class="metric-card">
                  <div class="metric-label">主号 Auth</div>
                  <div class="metric-value" :class="mainAuthStatusClass(parent)">{{ mainAuthStatusLabel(parent) }}</div>
                  <div class="metric-hint">{{ parent.main_auth_filename || '-' }}</div>
                </div>
                <div class="metric-card">
                  <div class="metric-label">Plan Type</div>
                  <div class="metric-value text-sky-200">{{ parent.main_auth_plan_type || '-' }}</div>
                  <div class="metric-hint">主号 Codex plan</div>
                </div>
                <div class="metric-card">
                  <div class="metric-label">最近刷新</div>
                  <div class="metric-value text-cyan-200 text-base">{{ formatTime(parent.main_auth_refreshed_at) }}</div>
                  <div class="metric-hint">auth 文件刷新时间</div>
                </div>
                <div class="metric-card">
                  <div class="metric-label">最近上传 CPA</div>
                  <div class="metric-value text-emerald-200 text-base">{{ formatTime(parent.main_cpa_uploaded_at) }}</div>
                  <div class="metric-hint">主号一键传 CPA</div>
                </div>
              </div>

              <div class="flex flex-wrap gap-2">
                <button class="btn btn-primary" @click="startMainCodex(parent)">主号传 CPA</button>
              </div>

              <div
                v-if="parent.last_reconcile_error"
                class="rounded-2xl border border-rose-500/20 bg-rose-500/10 px-4 py-3 text-sm text-rose-200"
              >
                对账失败：{{ parent.last_reconcile_error }}
              </div>

              <div
                v-if="parent.main_codex_error"
                class="rounded-2xl border border-rose-500/20 bg-rose-500/10 px-4 py-3 text-sm text-rose-200"
              >
                主号传 CPA 失败（{{ mainCodexErrorStageLabel(parent.main_codex_error_stage) }}）：{{ parent.main_codex_error }}
              </div>
            </div>

            <div class="flex flex-wrap gap-2 xl:max-w-[460px] xl:justify-end">
              <button class="btn" @click="toggleEnabled(parent)">{{ parent.enabled ? '停用' : '启用' }}</button>
              <button class="btn btn-primary" @click="saveParent(parent)">保存</button>
              <button class="btn btn-primary" @click="startLogin(parent)">自动登录</button>
              <button class="btn" @click="openSessionImport(parent)">导入 Session</button>
              <button class="btn btn-invite" @click="toggleInvites(parent)">
                {{ inviteTarget === parent.id ? '收起待邀请' : '待邀请管理' }}
              </button>
              <button class="btn btn-danger" @click="toggleBlocked(parent)">
                {{ blockedTarget === parent.id ? '收起疑似封禁' : '疑似封禁子号' }}
              </button>
              <button
                v-if="pendingRemovalCount(parent) > 0"
                class="btn btn-danger"
                :disabled="blockedForParent(parent.id).loading"
                @click="removeAllBlocked(parent)"
              >
                移出全部待处理
              </button>
              <button class="btn btn-danger" @click="$emit('remove', parent.id)">删除</button>
            </div>
          </div>

          <div
            v-if="loginStatus.in_progress && loginStatus.parent_id === parent.id"
            class="mt-5 rounded-2xl border border-amber-500/20 bg-amber-500/10 p-4"
          >
            <div class="flex flex-col gap-1">
              <div class="text-sm text-amber-100">当前母号：{{ loginStatus.email || parent.email }}</div>
              <div class="text-sm text-amber-50">{{ loginStatus.message || formatStep(loginStatus.step) }}</div>
              <div v-if="loginStatus.detail" class="text-xs text-amber-200/80">{{ loginStatus.detail }}</div>
            </div>

            <div v-if="loginStatus.step === 'password_required'" class="mt-4 flex flex-col gap-3 md:flex-row">
              <input v-model="passwordDraft" type="password" placeholder="输入密码继续登录" class="input flex-1" />
              <button class="btn btn-primary" @click="$emit('submit-password', { id: parent.id, password: passwordDraft })">
                提交密码
              </button>
            </div>

            <div v-else-if="loginStatus.step === 'code_required'" class="mt-4 flex flex-col gap-3 md:flex-row">
              <input v-model="codeDraft" placeholder="输入 6 位验证码" class="input flex-1" />
              <button class="btn btn-primary" @click="$emit('submit-code', { id: parent.id, code: codeDraft })">
                提交验证码
              </button>
            </div>

            <div v-else-if="loginStatus.step === 'workspace_required'" class="mt-4 space-y-3">
              <div
                v-if="!(loginStatus.workspace_options || []).length"
                class="rounded-2xl border border-slate-700 bg-slate-950/50 px-4 py-3 text-sm text-slate-300"
              >
                当前没有读取到可选工作空间，请刷新后重试，或改用导入 Session。
              </div>
              <button
                v-for="option in loginStatus.workspace_options || []"
                :key="option.id"
                class="flex w-full items-center justify-between rounded-2xl border border-slate-700 px-4 py-3 text-left text-sm text-slate-100 transition hover:border-cyan-500"
                @click="$emit('submit-workspace', { id: parent.id, optionId: option.id })"
              >
                <span>{{ option.label }}</span>
                <span class="text-xs uppercase tracking-[0.3em] text-cyan-300">{{ option.kind || 'team' }}</span>
              </button>
            </div>

            <button class="mt-4 btn" @click="$emit('cancel-login', parent.id)">取消登录</button>
          </div>

          <div
            v-if="mainCodexStatus.in_progress && mainCodexStatus.parent_id === parent.id"
            class="mt-5 rounded-2xl border border-cyan-500/20 bg-cyan-500/10 p-4"
          >
            <div class="flex flex-col gap-1">
              <div class="text-sm text-cyan-100">当前主号同步：{{ mainCodexStatus.email || parent.email }}</div>
              <div class="text-sm text-cyan-50">{{ mainCodexStatus.message || formatMainCodexStep(mainCodexStatus.step) }}</div>
              <div v-if="mainCodexStatus.detail" class="text-xs text-cyan-200/80">{{ mainCodexStatus.detail }}</div>
            </div>

            <div v-if="mainCodexStatus.step === 'password_required'" class="mt-4 flex flex-col gap-3 md:flex-row">
              <input v-model="mainCodexPasswordDraft" type="password" placeholder="输入母号密码继续主号同步" class="input flex-1" />
              <button
                class="btn btn-primary"
                @click="$emit('submit-main-codex-password', { id: parent.id, password: mainCodexPasswordDraft })"
              >
                提交密码
              </button>
            </div>

            <div v-else-if="mainCodexStatus.step === 'code_required'" class="mt-4 flex flex-col gap-3 md:flex-row">
              <input v-model="mainCodexCodeDraft" placeholder="输入 6 位验证码继续主号同步" class="input flex-1" />
              <button
                class="btn btn-primary"
                @click="$emit('submit-main-codex-code', { id: parent.id, code: mainCodexCodeDraft })"
              >
                提交验证码
              </button>
            </div>

            <button class="mt-4 btn" @click="$emit('cancel-main-codex', parent.id)">取消主号同步</button>
          </div>

          <div v-if="sessionTarget === parent.id" class="mt-5 rounded-2xl border border-cyan-500/20 bg-cyan-500/10 p-4">
            <div class="text-sm text-cyan-100">为 {{ parent.email }} 导入 session_token</div>
            <div class="mt-3 flex flex-col gap-3 md:flex-row">
              <input v-model.trim="sessionDraft" placeholder="session_token" class="input flex-1" />
              <button class="btn btn-primary" @click="submitSession(parent)">提交</button>
            </div>
          </div>

          <div v-if="inviteTarget === parent.id" class="mt-5 rounded-2xl border border-emerald-500/20 bg-emerald-500/10 p-4">
            <div class="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <div class="text-sm text-emerald-100">待邀请列表：{{ parent.workspace_name || parent.email }}</div>
                <div class="mt-1 text-xs text-emerald-200/80">
                  这里只展示 pending invite。清理待邀请不会删除已加入成员；如果待邀请为 0 但已加入不为 0，说明远端仍有占位。
                </div>
              </div>
              <button class="btn" :disabled="inviteForParent(parent.id).loading" @click="refreshInvites(parent)">
                {{ inviteForParent(parent.id).loading ? '加载中...' : '刷新待邀请' }}
              </button>
            </div>

            <div
              v-if="inviteForParent(parent.id).error"
              class="mt-4 rounded-2xl border border-rose-500/20 bg-rose-500/10 px-4 py-3 text-sm text-rose-200"
            >
              {{ inviteForParent(parent.id).error }}
            </div>

            <div
              v-else-if="inviteForParent(parent.id).loading"
              class="mt-4 rounded-2xl border border-slate-800 bg-slate-950/60 px-4 py-6 text-sm text-slate-300"
            >
              正在加载待邀请列表...
            </div>

            <div
              v-else-if="inviteForParent(parent.id).invites.length === 0"
              class="mt-4 rounded-2xl border border-slate-800 bg-slate-950/60 px-4 py-6 text-sm text-slate-400"
            >
              当前没有待邀请。远端已加入人数：{{ parent.remote_member_count || 0 }}
            </div>

            <div v-else class="mt-4 overflow-hidden rounded-2xl border border-slate-800">
              <table class="min-w-full divide-y divide-slate-800 text-sm">
                <thead class="bg-slate-900/80 text-left text-slate-400">
                  <tr>
                    <th class="px-4 py-3">邮箱</th>
                    <th class="px-4 py-3">角色</th>
                    <th class="px-4 py-3">来源</th>
                    <th class="px-4 py-3">本地状态</th>
                    <th class="px-4 py-3 text-right">操作</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-slate-900 bg-slate-950/40">
                  <tr v-for="invite in inviteForParent(parent.id).invites" :key="invite.invite_id || invite.email">
                    <td class="px-4 py-3 text-white">{{ invite.email || '-' }}</td>
                    <td class="px-4 py-3 text-slate-300">{{ invite.role || 'standard-user' }}</td>
                    <td class="px-4 py-3">
                      <span
                        class="rounded-full px-2.5 py-1 text-xs"
                        :class="invite.is_local ? 'bg-cyan-500/15 text-cyan-200' : 'bg-slate-800 text-slate-300'"
                      >
                        {{ invite.is_local ? '本地子号' : '外部邀请' }}
                      </span>
                    </td>
                    <td class="px-4 py-3">
                      <span class="rounded-full px-2.5 py-1 text-xs" :class="statusClass(invite.local_status)">
                        {{ formatLocalStatus(invite.local_status) }}
                      </span>
                    </td>
                    <td class="px-4 py-3 text-right">
                      <button
                        class="btn btn-danger"
                        :disabled="inviteForParent(parent.id).loading || !invite.invite_id"
                        @click="cancelInvite(parent, invite)"
                      >
                        取消邀请
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <div v-if="blockedTarget === parent.id" class="mt-5 rounded-2xl border border-rose-500/20 bg-rose-500/10 p-4">
            <div class="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <div class="text-sm text-rose-100">待移出子号：{{ parent.workspace_name || parent.email }}</div>
                <div class="mt-1 text-xs text-rose-200/80">
                  这里只移出 Team 成员，不删除本地 auth/CPA 文件；移出后需要手动点击“一键补满”。
                </div>
              </div>
              <div class="flex flex-wrap gap-2">
                <button class="btn" :disabled="blockedForParent(parent.id).loading" @click="refreshBlocked(parent)">
                  {{ blockedForParent(parent.id).loading ? '加载中...' : '刷新待移出' }}
                </button>
                <button
                  class="btn btn-danger"
                  :disabled="blockedForParent(parent.id).loading || (blockedForParent(parent.id).members.length === 0 && pendingRemovalCount(parent) === 0)"
                  @click="removeAllBlocked(parent)"
                >
                  移出全部待处理
                </button>
              </div>
            </div>

            <div
              v-if="blockedForParent(parent.id).error"
              class="mt-4 rounded-2xl border border-rose-500/20 bg-rose-500/10 px-4 py-3 text-sm text-rose-200"
            >
              {{ blockedForParent(parent.id).error }}
            </div>

            <div
              v-else-if="blockedForParent(parent.id).loading"
              class="mt-4 rounded-2xl border border-slate-800 bg-slate-950/60 px-4 py-6 text-sm text-slate-300"
            >
              正在加载待移出子号...
            </div>

            <div
              v-else-if="blockedForParent(parent.id).members.length === 0"
              class="mt-4 rounded-2xl border border-slate-800 bg-slate-950/60 px-4 py-6 text-sm text-slate-400"
            >
              当前没有待移出的疑似封禁子号。
            </div>

            <div v-else class="mt-4 overflow-hidden rounded-2xl border border-slate-800">
              <table class="min-w-full divide-y divide-slate-800 text-sm">
                <thead class="bg-slate-900/80 text-left text-slate-400">
                  <tr>
                    <th class="px-4 py-3">邮箱</th>
                    <th class="px-4 py-3">健康状态</th>
                    <th class="px-4 py-3">最近检测</th>
                    <th class="px-4 py-3">错误摘要</th>
                    <th class="px-4 py-3 text-right">操作</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-slate-900 bg-slate-950/40">
                  <tr v-for="member in blockedForParent(parent.id).members" :key="member.id">
                    <td class="px-4 py-3 text-white">{{ member.email || '-' }}</td>
                    <td class="px-4 py-3">
                      <span class="rounded-full bg-rose-500/15 px-2.5 py-1 text-xs text-rose-200">
                        {{ healthStatusLabel(member.health_status) }}
                      </span>
                    </td>
                    <td class="px-4 py-3 text-slate-300">{{ formatTime(member.health_checked_at) }}</td>
                    <td class="px-4 py-3 text-xs text-rose-100">{{ member.health_error || member.error || '-' }}</td>
                    <td class="px-4 py-3 text-right">
                      <button
                        class="btn btn-danger"
                        :disabled="blockedForParent(parent.id).loading || !member.remote_member_id"
                        @click="removeBlocked(parent, member)"
                      >
                        {{ member.remote_member_id ? '移出 Team' : '缺少成员 ID' }}
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'

const props = defineProps({
  parents: { type: Array, default: () => [] },
  loginStatus: {
    type: Object,
    default: () => ({ in_progress: false, workspace_options: [], detail: '', message: '', auto_mode: false }),
  },
  mainCodexStatus: {
    type: Object,
    default: () => ({ in_progress: false, detail: '', message: '', auto_mode: false }),
  },
  inviteState: { type: Object, default: () => ({}) },
  blockedState: { type: Object, default: () => ({}) },
  bulkImportResult: { type: Object, default: null },
})

const emit = defineEmits([
  'refresh',
  'create',
  'bulk-import',
  'update',
  'remove',
  'start-login',
  'login-all',
  'import-session',
  'submit-password',
  'submit-code',
  'submit-workspace',
  'cancel-login',
  'start-main-codex',
  'submit-main-codex-password',
  'submit-main-codex-code',
  'cancel-main-codex',
  'clear-pending-all',
  'load-invites',
  'cancel-invite',
  'load-blocked',
  'remove-blocked',
  'remove-all-blocked',
])

const createForm = reactive({
  label: '',
  email: '',
  default_batch_size: 1,
})

const drafts = reactive({})
const passwordDraft = ref('')
const codeDraft = ref('')
const mainCodexPasswordDraft = ref('')
const mainCodexCodeDraft = ref('')
const bulkText = ref('')
const bulkDefaultBatchSize = ref(1)
const bulkError = ref('')
const loginAllConcurrency = ref(2)
const sessionDraft = ref('')
const sessionTarget = ref('')
const inviteTarget = ref('')
const blockedTarget = ref('')

function draftValue(parent, key) {
  return drafts[parent.id]?.[key] ?? parent[key] ?? ''
}

function toNumber(value, fallback = 1) {
  const next = Number(value)
  return Number.isFinite(next) && next > 0 ? next : fallback
}

function submitCreate() {
  emit('create', { ...createForm, enabled: true })
  createForm.label = ''
  createForm.email = ''
  createForm.default_batch_size = 1
}

function splitBulkLine(line) {
  for (const separator of ['----', '\t', '|', ',']) {
    if (line.includes(separator)) {
      return line.split(separator).map(part => part.trim()).filter(Boolean)
    }
  }
  return line.split(/\s+/).map(part => part.trim()).filter(Boolean)
}

function looksLikeEmail(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(value || '').trim())
}

function parseBulkLine(line, index) {
  const parts = splitBulkLine(line)
  const emailIndex = parts.findIndex(looksLikeEmail)
  if (emailIndex < 0) {
    throw new Error(`第 ${index} 行缺少有效邮箱`)
  }

  const email = parts[emailIndex]
  const label = emailIndex > 0 ? parts.slice(0, emailIndex).join(' ') : ''
  const tail = parts.slice(emailIndex + 1)
  return {
    label,
    email,
    password: tail[0] || '',
    workspace_name: tail[1] || '',
  }
}

function submitBulkImport() {
  bulkError.value = ''
  const rows = []
  try {
    bulkText.value.split(/\r?\n/).forEach((rawLine, lineIndex) => {
      const line = rawLine.trim()
      if (!line || line.startsWith('#')) return
      rows.push(parseBulkLine(line, lineIndex + 1))
    })
  } catch (error) {
    bulkError.value = error?.message || '批量导入内容格式错误'
    return
  }

  if (!rows.length) {
    bulkError.value = '没有可导入的母号'
    return
  }
  emit('bulk-import', { parents: rows, defaultBatchSize: toNumber(bulkDefaultBatchSize.value, 1) })
}

function loginConcurrencyValue() {
  const next = Number(loginAllConcurrency.value)
  const normalized = Number.isFinite(next) ? Math.trunc(next) : 2
  return Math.min(5, Math.max(1, normalized))
}

function emitLoginAll() {
  const concurrency = loginConcurrencyValue()
  loginAllConcurrency.value = concurrency
  emit('login-all', { concurrency })
}

function updateDraft(id, key, value) {
  drafts[id] = { ...(drafts[id] || {}), [key]: value }
}

function saveParent(parent) {
  emit('update', { id: parent.id, payload: drafts[parent.id] || {} })
}

function toggleEnabled(parent) {
  emit('update', { id: parent.id, payload: { enabled: !parent.enabled } })
}

function startLogin(parent) {
  passwordDraft.value = ''
  codeDraft.value = ''
  emit('start-login', {
    id: parent.id,
    email: String(drafts[parent.id]?.email ?? parent.email ?? '').trim(),
  })
}

function startMainCodex(parent) {
  mainCodexPasswordDraft.value = ''
  mainCodexCodeDraft.value = ''
  emit('start-main-codex', parent.id)
}

function openSessionImport(parent) {
  sessionTarget.value = sessionTarget.value === parent.id ? '' : parent.id
  sessionDraft.value = ''
}

function submitSession(parent) {
  emit('import-session', {
    id: parent.id,
    email: String(drafts[parent.id]?.email ?? parent.email ?? '').trim(),
    sessionToken: sessionDraft.value,
  })
  sessionDraft.value = ''
  sessionTarget.value = ''
}

function inviteForParent(parentId) {
  return props.inviteState[parentId] || { invites: [], loading: false, error: '' }
}

function blockedForParent(parentId) {
  return props.blockedState[parentId] || { members: [], loading: false, error: '' }
}

function pendingRemovalCount(parent) {
  return Number(parent.pending_removal_count ?? parent.blocked_member_count ?? 0) || 0
}

function usableMemberCount(parent) {
  const fallback = Math.max(0, Number(parent.remote_member_count || 0) - pendingRemovalCount(parent))
  return Number(parent.usable_member_count ?? fallback) || 0
}

function toggleInvites(parent) {
  const open = inviteTarget.value === parent.id
  inviteTarget.value = open ? '' : parent.id
  if (!open) emit('load-invites', parent.id)
}

function refreshInvites(parent) {
  emit('load-invites', parent.id)
}

function cancelInvite(parent, invite) {
  const target = invite.email || invite.invite_id
  if (!window.confirm(`确认取消 ${target} 的待邀请吗？`)) return
  emit('cancel-invite', { parentId: parent.id, inviteId: invite.invite_id })
}

function toggleBlocked(parent) {
  const open = blockedTarget.value === parent.id
  blockedTarget.value = open ? '' : parent.id
  if (!open) emit('load-blocked', parent.id)
}

function refreshBlocked(parent) {
  emit('load-blocked', parent.id)
}

function removeBlocked(parent, member) {
  const target = member.email || member.id
  const confirmed = window.confirm(
    `确认将 ${target} 从 Team 移出吗？\n\n只会移出 Team 成员，不删除本地 auth/CPA 文件。移出后需要手动点击“一键补满”。`,
  )
  if (!confirmed) return
  emit('remove-blocked', { parentId: parent.id, childId: member.id })
}

function removeAllBlocked(parent) {
  const listedCount = blockedForParent(parent.id).members.length
  const count = pendingRemovalCount(parent) || listedCount
  const confirmed = window.confirm(
    `确认将 ${count} 个待移出子号从 Team 移出吗？\n\n只移出 Team 成员，不删除本地 auth/CPA 文件；移出后需要手动点击“一键补满”。`,
  )
  if (!confirmed) return
  emit('remove-all-blocked', parent.id)
}

function formatStep(step) {
  if (step === 'starting') return '正在启动自动登录'
  if (step === 'submitting_email') return '正在提交母号邮箱'
  if (step === 'waiting_code') return '正在等待验证码'
  if (step === 'submitting_code') return '正在提交验证码'
  if (step === 'selecting_workspace') return '正在选择工作空间'
  if (step === 'password_required') return '需要密码继续登录'
  if (step === 'code_required') return '需要验证码继续登录'
  if (step === 'workspace_required') return '需要手动选择工作空间'
  if (step === 'completed') return '登录已完成'
  if (step === 'error') return '登录失败'
  return step || '处理中'
}

function formatMainCodexStep(step) {
  if (step === 'starting') return '正在启动主号同步'
  if (step === 'waiting_code') return '正在等待主号验证码'
  if (step === 'submitting_code') return '正在提交主号验证码'
  if (step === 'uploading_cpa') return '正在上传主号 auth 到 CPA'
  if (step === 'password_required') return '需要密码继续主号同步'
  if (step === 'code_required') return '需要验证码继续主号同步'
  if (step === 'completed') return '主号传 CPA 已完成'
  if (step === 'error') return '主号传 CPA 失败'
  return step || '处理中'
}

function formatTime(ts) {
  if (!ts) return '未对账'
  const date = new Date(ts * 1000)
  return `${date.toLocaleDateString()} ${date.toLocaleTimeString()}`
}

function formatLocalStatus(status) {
  if (status === 'blocked') return '疑似封禁'
  if (status === 'removed') return '已移出'
  if (status === 'invited') return '待接受'
  if (status === 'accepted') return '已加入待授权'
  if (status === 'auth_saved') return '已授权待 CPA'
  if (status === 'ready') return '已完成'
  if (status === 'failed') return '失败未占位'
  if (status === 'cancelled') return '已取消'
  return '-'
}

function statusClass(status) {
  if (status === 'blocked') return 'bg-rose-500/15 text-rose-200'
  if (status === 'removed') return 'bg-slate-700 text-slate-200'
  if (status === 'ready') return 'bg-emerald-500/15 text-emerald-200'
  if (status === 'auth_saved') return 'bg-cyan-500/15 text-cyan-200'
  if (status === 'accepted') return 'bg-sky-500/15 text-sky-200'
  if (status === 'invited') return 'bg-amber-500/15 text-amber-200'
  if (status === 'failed') return 'bg-rose-500/15 text-rose-200'
  if (status === 'cancelled') return 'bg-slate-700 text-slate-200'
  return 'bg-slate-800 text-slate-400'
}

function healthStatusLabel(status) {
  if (status === 'healthy') return '正常'
  if (status === 'quota_exhausted') return '额度耗尽'
  if (status === 'auth_error') return '认证失效'
  if (status === 'check_failed') return '检测失败'
  return '未知'
}

function mainAuthStatusClass(parent) {
  if (parent.main_codex_error) return 'text-rose-200'
  if (parent.main_auth_present) return 'text-cyan-200'
  return 'text-slate-200'
}

function mainAuthStatusLabel(parent) {
  if (parent.main_codex_error) return '最近失败'
  return parent.main_auth_present ? '已保存' : '未保存'
}

function mainCodexErrorStageLabel(stage) {
  if (stage === 'cpa') return 'CPA'
  if (stage === 'codex') return '授权'
  return stage || '-'
}
</script>

<style scoped>
.input {
  @apply w-full rounded-2xl border border-slate-800 bg-slate-950 px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-500;
}

.btn {
  @apply rounded-2xl border border-slate-700 px-4 py-2 text-sm text-slate-200 transition hover:border-cyan-500 hover:text-white disabled:cursor-not-allowed disabled:opacity-60;
}

.btn-primary {
  @apply border-cyan-500/30 bg-cyan-500 text-slate-950 hover:border-cyan-400 hover:bg-cyan-400;
}

.btn-danger {
  @apply border-rose-500/30 text-rose-200 hover:border-rose-400 hover:text-white;
}

.btn-invite {
  @apply border-emerald-500/30 bg-emerald-500/10 text-emerald-100 hover:border-emerald-400 hover:bg-emerald-500/20 hover:text-white;
}

.tag {
  @apply rounded-full bg-slate-800 px-3 py-1;
}

.metric-card {
  @apply rounded-2xl border border-slate-800 bg-slate-950/70 px-4 py-3;
}

.metric-label {
  @apply text-xs uppercase tracking-[0.3em] text-slate-500;
}

.metric-value {
  @apply mt-2 text-2xl font-semibold;
}

.metric-hint {
  @apply mt-1 text-xs text-slate-500;
}
</style>
