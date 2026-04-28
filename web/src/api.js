const BASE = '/api'

function getApiKey() {
  return localStorage.getItem('autoteam_api_key') || ''
}

export function setApiKey(key) {
  localStorage.setItem('autoteam_api_key', key)
}

export function clearApiKey() {
  localStorage.removeItem('autoteam_api_key')
}

async function request(method, path, body = null) {
  const headers = { 'Content-Type': 'application/json' }
  const key = getApiKey()
  if (key) headers.Authorization = `Bearer ${key}`

  const options = { method, headers }
  if (body) options.body = JSON.stringify(body)

  const response = await fetch(`${BASE}${path}`, options)
  let data
  try {
    data = await response.json()
  } catch {
    const error = new Error(`HTTP ${response.status}: 服务端返回了非 JSON 响应`)
    error.status = response.status
    throw error
  }

  if (!response.ok) {
    const error = new Error(data?.detail?.message || data?.detail || `HTTP ${response.status}`)
    error.status = response.status
    throw error
  }

  return data
}

export const api = {
  checkAuth: () => request('GET', '/auth/check'),
  getSetupStatus: () => request('GET', '/setup/status'),
  saveSetup: (config) => request('POST', '/setup/save', config),
  getSettings: () => request('GET', '/settings'),
  saveSettings: (config) => request('POST', '/settings', config),

  getStatus: () => request('GET', '/status'),
  getParents: () => request('GET', '/parents'),
  createParent: (payload) => request('POST', '/parents', payload),
  bulkImportParents: (parents, defaultBatchSize = 1) =>
    request('POST', '/parents/bulk-import', { parents, default_batch_size: defaultBatchSize }),
  updateParent: (id, payload) => request('PUT', `/parents/${id}`, payload),
  deleteParent: (id) => request('DELETE', `/parents/${id}`),

  getParentLoginStatus: () => request('GET', '/parents/login/status'),
  getParentMainCodexStatus: () => request('GET', '/parents/main-codex/status'),
  startParentLogin: (id, email) => request('POST', `/parents/${id}/login/start`, { email }),
  importParentSession: (id, email, sessionToken) =>
    request('POST', `/parents/${id}/login/session`, { email, session_token: sessionToken }),
  submitParentPassword: (id, password) => request('POST', `/parents/${id}/login/password`, { password }),
  submitParentCode: (id, code) => request('POST', `/parents/${id}/login/code`, { code }),
  submitParentWorkspace: (id, optionId) => request('POST', `/parents/${id}/login/workspace`, { option_id: optionId }),
  cancelParentLogin: (id) => request('POST', `/parents/${id}/login/cancel`),
  startParentMainCodex: (id) => request('POST', `/parents/${id}/main-codex/start`),
  submitParentMainCodexPassword: (id, password) => request('POST', `/parents/${id}/main-codex/password`, { password }),
  submitParentMainCodexCode: (id, code) => request('POST', `/parents/${id}/main-codex/code`, { code }),
  cancelParentMainCodex: (id) => request('POST', `/parents/${id}/main-codex/cancel`),
  getParentInvites: (id) => request('GET', `/parents/${id}/team/invites`),
  cancelParentInvite: (parentId, inviteId) => request('POST', `/parents/${parentId}/team/invites/${inviteId}/cancel`),
  getParentBlockedMembers: (id) => request('GET', `/parents/${id}/team/blocked-members`),
  removeBlockedMember: (parentId, childId) => request('POST', `/parents/${parentId}/team/members/${childId}/remove`),
  removeAllBlockedMembers: (parentId) => request('POST', `/parents/${parentId}/team/blocked-members/remove-all`),

  startBatchRun: () => request('POST', '/tasks/batch-run'),
  startFillAll: () => request('POST', '/tasks/fill-all'),
  startCheckChildHealth: (concurrency = 5) => request('POST', '/tasks/check-child-health', { concurrency }),
  startDelete401Children: (concurrency = 5) => request('POST', '/tasks/delete-401-children', { concurrency }),
  startLoginAllParents: (concurrency = 2) => request('POST', '/tasks/login-all-parents', { concurrency }),
  startClearPendingInvites: () => request('POST', '/tasks/clear-pending-invites'),
  startRepairStuckAccounts: () => request('POST', '/tasks/repair-stuck-accounts'),
  startRepairParentChildLinks: (concurrency = 2) => request('POST', '/tasks/repair-parent-child-links', { concurrency }),
  getTasks: () => request('GET', '/tasks'),
  getTask: (id) => request('GET', `/tasks/${id}`),

  resyncCpa: () => request('POST', '/cpa/resync'),
  getLogs: (limit = 100, since = 0) => request('GET', `/logs?limit=${limit}&since=${since}`),
}
