const API = ''

export async function apiFetch(path, opts = {}) {
  const token = localStorage.getItem('ai_router_token')
  const res = await fetch(`${API}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...opts.headers
    },
    ...opts,
  })
  if (!res.ok) {
    const err = await res.text().catch(() => res.statusText)
    if (res.status === 401 && !path.startsWith('/api/auth/')) {
      location.hash = '/login'
    }
    throw new Error(err)
  }
  return res.json()
}

export const login = (password) => apiFetch('/api/auth/login', { method: 'POST', body: JSON.stringify({ password }) })
export const logout = () => apiFetch('/api/auth/logout', { method: 'POST' })
export const authStatus = () => apiFetch('/api/auth/status')

export const getProviders = () => apiFetch('/api/providers')
export const createProvider = (data) => apiFetch('/api/providers', { method: 'POST', body: JSON.stringify(data) })
export const updateProvider = (id, data) => apiFetch(`/api/providers/${id}`, { method: 'PUT', body: JSON.stringify(data) })
export const deleteProvider = (id) => apiFetch(`/api/providers/${id}`, { method: 'DELETE' })
export const deleteProviderModels = (id) => apiFetch(`/api/providers/${id}/models`, { method: 'DELETE' })
export const testProvider = (id) => apiFetch(`/api/providers/${id}/test`, { method: 'POST' })
export const testProviderTools = (id, model) => apiFetch(`/api/providers/${id}/test-tools`, {
  method: 'POST',
  body: JSON.stringify(model ? { model } : {})
})
export const fetchModels = (id) => apiFetch(`/api/providers/${id}/fetch-models`, { method: 'POST' })

export const getKeys = (providerId, status) => apiFetch(`/api/keys${providerId ? `?provider_id=${providerId}` : ''}${status ? `&status=${status}` : ''}`)
export const addKey = (providerId, key, label = '') => apiFetch('/api/keys', { method: 'POST', body: JSON.stringify({ provider_id: providerId, key, label }) })
export const addKeysBulk = (providerId, keys) => apiFetch('/api/keys/bulk', { method: 'POST', body: JSON.stringify({ provider_id: providerId, keys }) })
export const deleteKey = (id) => apiFetch(`/api/keys/${id}`, { method: 'DELETE' })
export const activateKey = (id) => apiFetch(`/api/keys/${id}/activate`, { method: 'POST' })
export const deactivateKey = (id) => apiFetch(`/api/keys/${id}/deactivate`, { method: 'POST' })

export const getLocalKeys = () => apiFetch('/api/local-keys')
export const createLocalKey = (name, key) => apiFetch('/api/local-keys', { method: 'POST', body: JSON.stringify({ name, key }) })
export const deleteLocalKey = (id) => apiFetch(`/api/local-keys/${id}`, { method: 'DELETE' })
export const toggleLocalKey = (id, isActive) => apiFetch(`/api/local-keys/${id}/toggle`, { method: 'POST', body: JSON.stringify({ is_active: isActive }) })

export const getAliases = (providerId) => apiFetch(`/api/aliases${providerId ? `?provider_id=${providerId}` : ''}`)
export const addAlias = (alias, providerId, modelId) => apiFetch('/api/aliases', { method: 'POST', body: JSON.stringify({ alias, provider_id: providerId, model_id: modelId }) })
export const deleteAlias = (alias) => apiFetch('/api/aliases/delete', { method: 'POST', body: JSON.stringify({ alias }) })
export const activateAlias = (alias) => apiFetch('/api/aliases/activate', { method: 'POST', body: JSON.stringify({ alias }) })
export const deactivateAlias = (alias) => apiFetch('/api/aliases/deactivate', { method: 'POST', body: JSON.stringify({ alias }) })

export const getLogs = (limit = 100) => apiFetch(`/api/logs?limit=${limit}`)
export const getStats = () => apiFetch('/api/stats')

export const getSettings = () => apiFetch('/api/settings')
export const updateSettings = (data) => apiFetch('/api/settings', { method: 'PUT', body: JSON.stringify(data) })

export const getPresets = () => apiFetch('/api/providers/presets')

export const getCombos = () => apiFetch('/api/combos')
export const getCombo = (id) => apiFetch(`/api/combos/${id}`)
export const createCombo = (data) => apiFetch('/api/combos', { method: 'POST', body: JSON.stringify(data) })
export const updateCombo = (id, data) => apiFetch(`/api/combos/${id}`, { method: 'PUT', body: JSON.stringify(data) })
export const deleteCombo = (id) => apiFetch(`/api/combos/${id}`, { method: 'DELETE' })

export const addComboModel = (comboId, data) => apiFetch(`/api/combos/${comboId}/models`, { method: 'POST', body: JSON.stringify(data) })
export const removeComboModel = (comboId, modelId) => apiFetch(`/api/combos/${comboId}/models/${modelId}`, { method: 'DELETE' })
export const updateComboModel = (comboId, modelId, data) => apiFetch(`/api/combos/${comboId}/models/${modelId}`, { method: 'PUT', body: JSON.stringify(data) })

export const updateLocalKey = (id, data) => apiFetch(`/api/local-keys/${id}`, { method: 'PUT', body: JSON.stringify(data) })
