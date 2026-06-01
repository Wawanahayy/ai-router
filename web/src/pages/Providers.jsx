import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getProviders, createProvider, updateProvider, deleteProvider, deleteProviderModels, testProvider, testProviderTools, apiFetch, getPresets } from '../api'
import { Plus, Trash2, Zap, ChevronDown, ChevronUp, Edit3, RefreshCw, X } from 'lucide-react'
import ToggleSwitch from '../components/ToggleSwitch'

export default function Providers() {
  const [providers, setProviders] = useState([])
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(false)
  const [expanded, setExpanded] = useState(null)
  const [testing, setTesting] = useState(null)
  const [testingTools, setTestingTools] = useState(null)
  const [testResult, setTestResult] = useState({})
  const [fetchingModels, setFetchingModels] = useState(null)
  const [modelInput, setModelInput] = useState('')
  const [addModelFor, setAddModelFor] = useState(null)
  const [newModelId, setNewModelId] = useState('')
  const [pendingDelete, setPendingDelete] = useState(null)
  const [editingProvider, setEditingProvider] = useState(null)
  const [editType, setEditType] = useState('openai-compatible')
  const [editPrefixEnabled, setEditPrefixEnabled] = useState(false)
  const navigate = useNavigate()

 const [formModels, setFormModels] = useState([])
 const [formPrefixEnabled, setFormPrefixEnabled] = useState(false)
 const [formType, setFormType] = useState('openai-compatible')
 const [presets, setPresets] = useState([])
 const [selectedPreset, setSelectedPreset] = useState('')

 useEffect(() => { load(); loadPresets() }, [])

 async function loadPresets() {
 try { setPresets(await getPresets()) } catch (e) { console.error(e) }
 }

 function applyPreset(presetName) {
  const p = presets.find(x => x.name === presetName)
  if (!p) { setSelectedPreset(''); return }
  setSelectedPreset(presetName)
  const form = document.querySelector('form')
  if (form) {
    const nameEl = form.querySelector('[name="name"]')
    const urlEl = form.querySelector('[name="base_url"]')
    const typeEl = form.querySelector('[name="type"]')
    const prefixEl = form.querySelector('[name="prefix"]')
    const authTypeEl = form.querySelector('[name="auth_type"]')
    const authHeaderEl = form.querySelector('[name="auth_header"]')
    const authPrefixEl = form.querySelector('[name="auth_prefix"]')
    const queryParamEl = form.querySelector('[name="key_query_param"]')
    const chatPathEl = form.querySelector('[name="chat_path"]')
    const modelsPathEl = form.querySelector('[name="models_path"]')
    const requestFormatEl = form.querySelector('[name="request_format"]')
    const anthropicVersionEl = form.querySelector('[name="anthropic_version"]')
    if (nameEl) nameEl.value = p.name || ''
    if (urlEl) urlEl.value = p.base_url || ''
    if (typeEl) typeEl.value = p.type || 'openai-compatible'
    setFormType(p.type || 'openai-compatible')
    if (prefixEl) prefixEl.value = p.prefix || ''
    setFormPrefixEnabled(!!p.prefix_enabled && !!p.prefix)
    if (authTypeEl) authTypeEl.value = p.auth_type || ''
    if (authHeaderEl) authHeaderEl.value = p.auth_header || ''
    if (authPrefixEl) authPrefixEl.value = p.auth_prefix || ''
    if (queryParamEl) queryParamEl.value = p.key_query_param || ''
    if (chatPathEl) chatPathEl.value = p.chat_path || ''
    if (modelsPathEl) modelsPathEl.value = p.models_path || ''
    if (requestFormatEl) requestFormatEl.value = p.request_format || ''
    if (anthropicVersionEl) anthropicVersionEl.value = p.anthropic_version || '2023-06-01'
    setFormModels(p.models || [])
  }
 }

  async function load() {
    try {
      setProviders(await getProviders())
    } catch (e) { console.error(e) }
    finally { setLoading(false) }
  }

  async function handleAdd(e) {
    e.preventDefault()
    const fd = new FormData(e.target)
    const prefixVal = fd.get('prefix') || ''
    let extraHeaders
    try {
      extraHeaders = fd.get('extra_headers') ? JSON.parse(fd.get('extra_headers')) : {}
    } catch {
      setTestResult({ add: { valid: false, error: 'Extra Headers must be valid JSON.' } })
      return
    }
    const data = {
      name: fd.get('name'),
      type: fd.get('type'),
      base_url: fd.get('base_url'),
      prefix: prefixVal,
      prefix_enabled: formPrefixEnabled && prefixVal ? 1 : 0,
      api_type: fd.get('api_type') || 'chat',
      supports_tools: fd.get('supports_tools') ? 1 : 0,
      supports_streaming: fd.get('supports_streaming') ? 1 : 0,
      supports_json_mode: fd.get('supports_json_mode') ? 1 : 0,
      extra_headers: extraHeaders,
      auth_type: fd.get('auth_type') || '',
      auth_header: fd.get('auth_header') || '',
      auth_prefix: fd.get('auth_prefix') || '',
      key_query_param: fd.get('key_query_param') || '',
      chat_path: fd.get('chat_path') || '',
      models_path: fd.get('models_path') || '',
      request_format: fd.get('request_format') || '',
      anthropic_version: fd.get('anthropic_version') || '2023-06-01',
      models: formModels,
    }
    if (!data.name || !data.base_url) return
    await createProvider(data)
    setShowAdd(false)
    setFormModels([])
    setModelInput('')
    setFormPrefixEnabled(false)
    setFormType('openai-compatible')
    setTestResult({})
    e.target.reset()
    load()
  }

  async function handleDelete(id) {
    await deleteProvider(id)
    setPendingDelete(null)
    load()
  }

  function startEditProvider(provider) {
    setExpanded(provider.id)
    setEditingProvider(provider.id)
    setEditType(provider.type || 'openai-compatible')
    setEditPrefixEnabled(provider.prefix_enabled !== 0 && !!provider.prefix)
    setTestResult(prev => ({ ...prev, [provider.id]: { ...prev[provider.id], editError: null } }))
  }

  function cancelEditProvider() {
    setEditingProvider(null)
    setEditType('openai-compatible')
    setEditPrefixEnabled(false)
  }

  async function handleEditProvider(providerId, e) {
    e.preventDefault()
    const fd = new FormData(e.target)
    const prefixVal = fd.get('prefix') || ''
    let extraHeaders
    try {
      extraHeaders = fd.get('extra_headers') ? JSON.parse(fd.get('extra_headers')) : {}
    } catch {
      setTestResult(prev => ({
        ...prev,
        [providerId]: { ...prev[providerId], editError: 'Extra Headers must be valid JSON.' }
      }))
      return
    }
    const data = {
      name: fd.get('name'),
      type: fd.get('type'),
      base_url: fd.get('base_url'),
      prefix: prefixVal,
      prefix_enabled: editPrefixEnabled && prefixVal ? 1 : 0,
      api_type: fd.get('api_type') || 'chat',
      supports_tools: fd.get('supports_tools') ? 1 : 0,
      supports_streaming: fd.get('supports_streaming') ? 1 : 0,
      supports_json_mode: fd.get('supports_json_mode') ? 1 : 0,
      extra_headers: extraHeaders,
      auth_type: fd.get('auth_type') || '',
      auth_header: fd.get('auth_header') || '',
      auth_prefix: fd.get('auth_prefix') || '',
      key_query_param: fd.get('key_query_param') || '',
      chat_path: fd.get('chat_path') || '',
      models_path: fd.get('models_path') || '',
      request_format: fd.get('request_format') || '',
      anthropic_version: fd.get('anthropic_version') || '2023-06-01',
    }
    if (!data.name || !data.base_url) return
    await updateProvider(providerId, data)
    cancelEditProvider()
    load()
  }

  async function handleToggle(id, active) {
    await updateProvider(id, { is_active: active ? 0 : 1 })
    load()
  }

  async function handleTogglePrefix(id, currentEnabled, prefix) {
    if (!currentEnabled && !prefix) {
      setExpanded(id)
      setTestResult(prev => ({
        ...prev,
        [id]: { ...prev[id], prefixMessage: 'Set a prefix value before enabling prefix routing.' }
      }))
      return
    }
    await updateProvider(id, { prefix_enabled: currentEnabled ? 0 : 1 })
    load()
  }

  async function handleToggleCapability(id, field, value) {
    await updateProvider(id, { [field]: value ? 0 : 1 })
    load()
  }

  async function handleTest(id) {
    setTesting(id)
    setTestResult({})
    const res = await testProvider(id)
    setTestResult(prev => ({ ...prev, [id]: res }))
    setTesting(null)
  }

  async function handleTestTools(id) {
    setTestingTools(id)
    try {
      const res = await testProviderTools(id)
      setTestResult(prev => ({ ...prev, [id]: { ...prev[id], toolTest: res } }))
      load()
    } catch (e) {
      setTestResult(prev => ({ ...prev, [id]: { ...prev[id], toolTest: { valid: false, agent_ready: false, error: e.message } } }))
    }
    setTestingTools(null)
  }

  async function handleFetchModels(id) {
    setFetchingModels(id)
    try {
      const res = await apiFetch(`/api/providers/${id}/fetch-models`, { method: 'POST' })
      setTestResult(prev => ({ ...prev, [id]: { ...prev[id], fetchedModels: res } }))
      load()
    } catch (e) {
      setTestResult(prev => ({ ...prev, [id]: { ...prev[id], fetchError: e.message } }))
    }
    setFetchingModels(null)
  }

  async function handleToggleModel(alias, isActive) {
    const endpoint = isActive ? '/api/aliases/deactivate' : '/api/aliases/activate'
    await apiFetch(endpoint, { method: 'POST', body: JSON.stringify({ alias }) })
    load()
  }

  async function handleDeleteModel(alias) {
    await apiFetch('/api/aliases/delete', { method: 'POST', body: JSON.stringify({ alias }) })
    setPendingDelete(null)
    load()
  }

  async function handleDeleteAllModels(providerId) {
    await deleteProviderModels(providerId)
    setPendingDelete(null)
    load()
  }

  async function handleAddModel(providerId, effectivePrefix, e) {
    e.preventDefault()
    const modelId = newModelId.trim()
    if (!modelId) return
    const alias = effectivePrefix ? `${effectivePrefix}/${modelId}` : modelId
    await apiFetch('/api/aliases', {
      method: 'POST',
      body: JSON.stringify({ alias, provider_id: providerId, model_id: modelId })
    })
    setAddModelFor(null)
    setNewModelId('')
    load()
  }

  function addFormModel() {
    const m = modelInput.trim()
    if (m && !formModels.includes(m)) {
      setFormModels([...formModels, m])
      setModelInput('')
    }
  }

  function removeFormModel(m) {
    setFormModels(formModels.filter(x => x !== m))
  }

  const providerTypeHelp = formType === 'anthropic-compatible'
    ? 'Anthropic-compatible upstream defaults to /messages. Anthropic clients can call /v1/messages; OpenAI clients can call /v1/chat/completions and ai-router will convert.'
    : 'OpenAI-compatible upstream defaults to /chat/completions and /models. OpenAI-compatible clients call /v1/chat/completions.'

  if (loading) return <div className="text-slate-500">Loading...</div>

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold text-white">Providers</h2>
        <button onClick={() => setShowAdd(!showAdd)} className="btn-primary flex items-center gap-2 text-sm">
          <Plus size={16} /> Add Provider
        </button>
      </div>

      {showAdd && (
 <form onSubmit={handleAdd} className="card space-y-3">
 <h3 className="font-semibold text-white">New Provider</h3>
 {presets.length > 0 && (
  <div>
   <label className="text-xs text-slate-500">Quick Setup from Preset</label>
   <select
    value={selectedPreset}
    onChange={e => applyPreset(e.target.value)}
    className="input"
   >
    <option value="">-- Choose a preset or fill manually --</option>
    {presets.map(p => (
     <option key={p.name} value={p.name}>{p.name} ({p.type})</option>
    ))}
   </select>
  </div>
 )}
 <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-slate-500">Name *</label>
              <input name="name" className="input" placeholder="Nvidia NIM" required />
            </div>
 <div>
 <label className="text-xs text-slate-500">Type *</label>
 <select name="type" className="input" required value={formType} onChange={e => setFormType(e.target.value)}>
 <option value="">-- Select type --</option>
 <option value="openai-compatible">OpenAI Compatible</option>
 <option value="anthropic-compatible">Anthropic Compatible</option>
 </select>
 </div>
 <div className="col-span-2 text-xs text-slate-500 bg-white/[0.035] border border-white/[0.06] rounded-lg px-3 py-2">
  {providerTypeHelp}
 </div>
            <div>
              <label className="text-xs text-slate-500">Base URL *</label>
              <input name="base_url" className="input" placeholder="https://integrate.api.nvidia.com/v1" required />
            </div>
            <div className="flex items-end gap-2">
              <div className="flex-1">
                <label className="text-xs text-slate-500">Prefix (optional)</label>
                <input name="prefix" className="input" placeholder="nvidia" disabled={!formPrefixEnabled} />
              </div>
              <div className="flex flex-col items-center pb-1">
                <label className="text-xs text-slate-500 mb-1">On</label>
                <ToggleSwitch
                  checked={formPrefixEnabled}
                  onChange={() => setFormPrefixEnabled(!formPrefixEnabled)}
                  label="Toggle provider prefix"
                />
              </div>
            </div>
            <div>
              <label className="text-xs text-slate-500">API Type</label>
              <select name="api_type" className="input">
                <option value="chat">Chat Completions</option>
                <option value="responses">Responses API</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-slate-500">Extra Headers (JSON)</label>
              <input name="extra_headers" className="input" placeholder='{"X-Custom":"val"}' />
            </div>
            <div>
              <label className="text-xs text-slate-500">Auth Type</label>
              <select name="auth_type" className="input">
                <option value="">Auto by type</option>
                <option value="bearer">Authorization: Bearer key</option>
                <option value="x-api-key">x-api-key header</option>
                <option value="api-key">api-key header</option>
                <option value="header">Custom header</option>
                <option value="query">Query param</option>
                <option value="none">No auth</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-slate-500">Auth Header</label>
              <input name="auth_header" className="input" placeholder="Authorization, x-api-key, X-API-Key" />
            </div>
            <div>
              <label className="text-xs text-slate-500">Auth Prefix</label>
              <input name="auth_prefix" className="input" placeholder="Bearer " />
            </div>
            <div>
              <label className="text-xs text-slate-500">Query Key Param</label>
              <input name="key_query_param" className="input" placeholder="key, api_key" />
            </div>
            <div>
              <label className="text-xs text-slate-500">Chat Path</label>
              <input name="chat_path" className="input" placeholder={formType === 'anthropic-compatible' ? '/messages' : '/chat/completions'} />
            </div>
            <div>
              <label className="text-xs text-slate-500">Models Path</label>
              <input name="models_path" className="input" placeholder={formType === 'anthropic-compatible' ? 'optional' : '/models'} />
            </div>
            <div>
              <label className="text-xs text-slate-500">Request Format</label>
              <select name="request_format" className="input">
                <option value="">Same as type</option>
                <option value="openai-compatible">OpenAI format</option>
                <option value="anthropic-compatible">Anthropic format</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-slate-500">Anthropic Version</label>
              <input name="anthropic_version" className="input" placeholder="2023-06-01" defaultValue="2023-06-01" />
            </div>
          </div>

          {testResult.add && (
            <div className="text-sm p-2 rounded-lg bg-red-500/10 text-red-400">
              {testResult.add.error}
            </div>
          )}

          <div className="grid grid-cols-3 gap-2">
            {[
              ['supports_tools', 'Tools'],
              ['supports_streaming', 'Streaming'],
              ['supports_json_mode', 'JSON mode'],
            ].map(([name, label]) => (
              <label key={name} className="flex items-center gap-2 text-xs text-slate-400 bg-white/5 rounded-lg px-3 py-2">
                <input name={name} type="checkbox" defaultChecked className="accent-indigo-500" />
                {label}
              </label>
            ))}
          </div>

          <div>
            <label className="text-xs text-slate-500">Models</label>
            <div className="flex gap-2 mt-1">
              <input
                value={modelInput}
                onChange={e => setModelInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addFormModel())}
                className="input flex-1"
                placeholder="e.g. deepseek-r1, claude-sonnet-4-6 (Enter to add)"
              />
              <button type="button" onClick={addFormModel} className="btn-ghost text-xs">Add</button>
            </div>
            {formModels.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-2">
                {formModels.map(m => (
                  <span key={m} className="text-xs bg-cyan-500/10 text-cyan-400 px-2 py-1 rounded flex items-center gap-1">
                    {m}
                    <button type="button" onClick={() => removeFormModel(m)} className="text-cyan-400/50 hover:text-cyan-400">
                      <X size={12} />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>

          <div className="flex gap-2">
            <button type="submit" className="btn-primary text-sm">Create</button>
            <button type="button" onClick={() => { setShowAdd(false); setFormModels([]); setModelInput(''); setFormPrefixEnabled(false); setFormType('openai-compatible'); setTestResult({}) }} className="btn-ghost text-sm">Cancel</button>
          </div>
        </form>
      )}

      {providers.length === 0 ? (
        <div className="card text-slate-500 text-sm">No providers yet. Click "Add Provider" to get started.</div>
      ) : (
        <div className="space-y-3">
          {providers.map(p => {
            const effectivePrefix = (p.prefix_enabled && p.prefix) ? p.prefix : ''
            return (
              <div key={p.id} className="card">
                <div
                  className="flex items-center justify-between cursor-pointer"
                  onClick={() => setExpanded(expanded === p.id ? null : p.id)}
                >
                  <div className="flex items-center gap-3">
                    <span className={`w-2.5 h-2.5 rounded-full ${p.is_active ? 'bg-emerald-400' : 'bg-slate-600'}`} />
                    <span className="font-semibold text-white">{p.name}</span>
                    <span className="text-xs text-slate-500 bg-white/5 px-2 py-0.5 rounded">{p.type}</span>
                    {effectivePrefix && <span className="text-xs text-indigo-400 bg-indigo-500/10 px-2 py-0.5 rounded">{effectivePrefix}/*</span>}
                    <div className="flex items-center gap-1.5 text-xs text-slate-500">
                      <span>Prefix</span>
                      <ToggleSwitch
                        checked={p.prefix_enabled !== 0}
                        onChange={e => { e.stopPropagation(); handleTogglePrefix(p.id, p.prefix_enabled, p.prefix) }}
                        label={p.prefix_enabled ? 'Disable prefix' : 'Enable prefix'}
                      />
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-slate-500">{p.key_stats?.alive || 0} keys</span>
                    <span className="text-xs text-cyan-500/70">{p.aliases?.length || 0} models</span>
                    <div className="flex items-center gap-1.5 text-xs text-slate-400" onClick={e => e.stopPropagation()}>
                      <ToggleSwitch
                        checked={p.is_active !== 0}
                        onChange={() => handleToggle(p.id, p.is_active)}
                        label={p.is_active ? 'Disable provider' : 'Enable provider'}
                      />
                      <span>{p.is_active ? 'Enabled' : 'Disabled'}</span>
                    </div>
                    {pendingDelete === `provider:${p.id}` ? (
                      <div className="inline-confirm" onClick={e => e.stopPropagation()}>
                        <span>Delete provider?</span>
                        <button onClick={() => handleDelete(p.id)} className="btn-danger text-xs">Delete</button>
                        <button onClick={() => setPendingDelete(null)} className="btn-ghost text-xs">Cancel</button>
                      </div>
                    ) : (
                      <button
                        onClick={e => { e.stopPropagation(); setPendingDelete(`provider:${p.id}`) }}
                        className="btn-danger text-xs flex items-center gap-1"
                        title="Delete provider"
                      >
                        <Trash2 size={12} /> Delete Provider
                      </button>
                    )}
                    <button
                      onClick={e => { e.stopPropagation(); startEditProvider(p) }}
                      className="btn-ghost text-xs flex items-center gap-1"
                      title="Edit provider"
                    >
                      <Edit3 size={12} /> Edit
                    </button>
                    <span className="text-xs text-slate-600">{expanded === p.id ? <ChevronUp size={14} /> : <ChevronDown size={14} />}</span>
                  </div>
                </div>

                {expanded === p.id && (
                  <div className="mt-4 pt-3 border-t border-[#1e1e2e] space-y-3" onClick={e => e.stopPropagation()}>
                    {editingProvider === p.id && (
                      <form onSubmit={(e) => handleEditProvider(p.id, e)} className="bg-white/[0.035] border border-white/[0.06] rounded-lg p-3 space-y-3">
                        <div className="flex items-center justify-between">
                          <h3 className="font-semibold text-white text-sm">Edit Provider</h3>
                          <button type="button" onClick={cancelEditProvider} className="btn-ghost text-xs flex items-center gap-1">
                            <X size={12} /> Close
                          </button>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <label className="text-xs text-slate-500">Name *</label>
                            <input name="name" className="input" defaultValue={p.name || ''} required />
                          </div>
                          <div>
                            <label className="text-xs text-slate-500">Type *</label>
                            <select name="type" className="input" value={editType} onChange={e => setEditType(e.target.value)} required>
                              <option value="openai-compatible">OpenAI Compatible</option>
                              <option value="anthropic-compatible">Anthropic Compatible</option>
                            </select>
                          </div>
                          <div>
                            <label className="text-xs text-slate-500">Base URL *</label>
                            <input name="base_url" className="input" defaultValue={p.base_url || ''} required />
                          </div>
                          <div className="flex items-end gap-2">
                            <div className="flex-1">
                              <label className="text-xs text-slate-500">Prefix</label>
                              <input name="prefix" className="input" defaultValue={p.prefix || ''} disabled={!editPrefixEnabled} />
                            </div>
                            <div className="flex flex-col items-center pb-1">
                              <label className="text-xs text-slate-500 mb-1">On</label>
                              <ToggleSwitch
                                checked={editPrefixEnabled}
                                onChange={() => setEditPrefixEnabled(!editPrefixEnabled)}
                                label="Toggle provider prefix"
                              />
                            </div>
                          </div>
                          <div>
                            <label className="text-xs text-slate-500">API Type</label>
                            <select name="api_type" className="input" defaultValue={p.api_type || 'chat'}>
                              <option value="chat">Chat Completions</option>
                              <option value="responses">Responses API</option>
                            </select>
                          </div>
                          <div>
                            <label className="text-xs text-slate-500">Extra Headers (JSON)</label>
                            <input
                              name="extra_headers"
                              className="input"
                              defaultValue={typeof p.extra_headers === 'string' ? p.extra_headers : JSON.stringify(p.extra_headers || {})}
                              placeholder='{"X-Custom":"val"}'
                            />
                          </div>
                          <div>
                            <label className="text-xs text-slate-500">Auth Type</label>
                            <select name="auth_type" className="input" defaultValue={p.auth_type || ''}>
                              <option value="">Auto by type</option>
                              <option value="bearer">Authorization: Bearer key</option>
                              <option value="x-api-key">x-api-key header</option>
                              <option value="api-key">api-key header</option>
                              <option value="header">Custom header</option>
                              <option value="query">Query param</option>
                              <option value="none">No auth</option>
                            </select>
                          </div>
                          <div>
                            <label className="text-xs text-slate-500">Auth Header</label>
                            <input name="auth_header" className="input" defaultValue={p.auth_header || ''} placeholder="Authorization, x-api-key, X-API-Key" />
                          </div>
                          <div>
                            <label className="text-xs text-slate-500">Auth Prefix</label>
                            <input name="auth_prefix" className="input" defaultValue={p.auth_prefix || ''} placeholder="Bearer " />
                          </div>
                          <div>
                            <label className="text-xs text-slate-500">Query Key Param</label>
                            <input name="key_query_param" className="input" defaultValue={p.key_query_param || ''} placeholder="key, api_key" />
                          </div>
                          <div>
                            <label className="text-xs text-slate-500">Chat Path</label>
                            <input name="chat_path" className="input" defaultValue={p.chat_path || ''} placeholder={editType === 'anthropic-compatible' ? '/messages' : '/chat/completions'} />
                          </div>
                          <div>
                            <label className="text-xs text-slate-500">Models Path</label>
                            <input name="models_path" className="input" defaultValue={p.models_path || ''} placeholder={editType === 'anthropic-compatible' ? 'optional' : '/models'} />
                          </div>
                          <div>
                            <label className="text-xs text-slate-500">Request Format</label>
                            <select name="request_format" className="input" defaultValue={p.request_format || ''}>
                              <option value="">Same as type</option>
                              <option value="openai-compatible">OpenAI format</option>
                              <option value="anthropic-compatible">Anthropic format</option>
                            </select>
                          </div>
                          <div>
                            <label className="text-xs text-slate-500">Anthropic Version</label>
                            <input name="anthropic_version" className="input" defaultValue={p.anthropic_version || '2023-06-01'} placeholder="2023-06-01" />
                          </div>
                        </div>

                        <div className="grid grid-cols-3 gap-2">
                          {[
                            ['supports_tools', 'Tools'],
                            ['supports_streaming', 'Streaming'],
                            ['supports_json_mode', 'JSON mode'],
                          ].map(([name, label]) => (
                            <label key={name} className="flex items-center gap-2 text-xs text-slate-400 bg-white/5 rounded-lg px-3 py-2">
                              <input name={name} type="checkbox" defaultChecked={p[name] !== 0} className="accent-indigo-500" />
                              {label}
                            </label>
                          ))}
                        </div>

                        {testResult[p.id]?.editError && (
                          <div className="text-sm p-2 rounded-lg bg-red-500/10 text-red-400">
                            {testResult[p.id].editError}
                          </div>
                        )}

                        <div className="flex gap-2">
                          <button type="submit" className="btn-primary text-sm">Save Provider</button>
                          <button type="button" onClick={cancelEditProvider} className="btn-ghost text-sm">Cancel</button>
                          <button type="button" onClick={() => navigate(`/keys/${p.id}`)} className="btn-ghost text-sm flex items-center gap-1">
                            <Edit3 size={12} /> Edit API Keys
                          </button>
                        </div>
                      </form>
                    )}

                    <div className="grid grid-cols-2 gap-3 text-sm">
                      <div>
                        <span className="text-slate-500">Base URL:</span>
                        <span className="ml-2 text-slate-300 font-mono text-xs">{p.base_url}</span>
                      </div>
                      <div>
                        <span className="text-slate-500">API Type:</span>
                        <span className="ml-2 text-slate-300">{p.api_type}</span>
                      </div>
                      <div>
                        <span className="text-slate-500">Auth:</span>
                        <span className="ml-2 text-slate-300 font-mono text-xs">{p.auth_type || 'auto'}{p.auth_header ? ` / ${p.auth_header}` : ''}</span>
                      </div>
                      <div>
                        <span className="text-slate-500">Paths:</span>
                        <span className="ml-2 text-slate-300 font-mono text-xs">{p.chat_path || 'default chat'} / {p.models_path || 'default models'}</span>
                      </div>
                      <div>
                        <span className="text-slate-500">Prefix:</span>
                        <span className="ml-2 text-slate-300 font-mono text-xs">{effectivePrefix || <span className="text-slate-600 italic">none</span>}</span>
                        <span className={`ml-2 text-xs ${p.prefix_enabled ? 'text-indigo-400' : 'text-slate-600'}`}>({p.prefix_enabled ? 'enabled' : 'disabled'})</span>
                      </div>
                      <div className="col-span-2 flex items-center gap-2">
                        <span className="text-slate-500">Capabilities:</span>
                        {[
                          ['supports_tools', 'Tools'],
                          ['supports_streaming', 'Streaming'],
                          ['supports_json_mode', 'JSON'],
                        ].map(([field, label]) => (
                          <label key={field} className="flex items-center gap-1.5 text-xs text-slate-400">
                            <ToggleSwitch
                              checked={p[field] !== 0}
                              onChange={() => handleToggleCapability(p.id, field, p[field] !== 0)}
                              label={`Toggle ${label}`}
                            />
                            {label}
                          </label>
                        ))}
                      </div>
                    </div>

                    <div>
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-sm text-slate-500">Models</span>
                        <div className="flex gap-1">
                          <button onClick={() => { setAddModelFor(addModelFor === p.id ? null : p.id); setNewModelId('') }} className="btn-ghost text-xs flex items-center gap-1">
                            <Plus size={11} /> Add Model
                          </button>
                          <button onClick={() => handleFetchModels(p.id)} disabled={fetchingModels === p.id} className="btn-ghost text-xs flex items-center gap-1">
                            <RefreshCw size={11} className={fetchingModels === p.id ? 'animate-spin' : ''} />
                            {fetchingModels === p.id ? 'Fetching...' : 'Fetch from Upstream'}
                          </button>
                          {p.aliases?.length > 0 && (
                            pendingDelete === `models:${p.id}` ? (
                              <div className="inline-confirm">
                                <span>Delete all {p.aliases.length} models?</span>
                                <button onClick={() => handleDeleteAllModels(p.id)} className="btn-danger text-xs">Delete All</button>
                                <button onClick={() => setPendingDelete(null)} className="btn-ghost text-xs">Cancel</button>
                              </div>
                            ) : (
                              <button onClick={() => setPendingDelete(`models:${p.id}`)} className="btn-danger text-xs flex items-center gap-1">
                                <Trash2 size={11} /> Delete All Models
                              </button>
                            )
                          )}
                        </div>
                      </div>
                      {addModelFor === p.id && (
                        <form onSubmit={(e) => handleAddModel(p.id, effectivePrefix, e)} className="flex items-end gap-2 mb-3 bg-white/[0.035] border border-white/[0.06] rounded-lg p-3">
                          <div className="flex-1">
                            <label className="text-xs text-slate-500">Model ID</label>
                            <input
                              value={newModelId}
                              onChange={e => setNewModelId(e.target.value)}
                              className="input"
                              placeholder="e.g. claude-sonnet-4-6"
                              autoFocus
                            />
                          </div>
                          <button type="submit" className="btn-primary text-xs">Add Model</button>
                          <button type="button" onClick={() => { setAddModelFor(null); setNewModelId('') }} className="btn-ghost text-xs">Cancel</button>
                        </form>
                      )}
                      {p.aliases?.length > 0 ? (
                        <div className="space-y-2">
                          {p.aliases.map(a => (
                            <div
                              key={a.alias}
                              className={`flex items-center gap-3 rounded-lg px-3 py-2 text-xs ${
                                a.is_active !== 0
                                  ? 'bg-cyan-500/10 text-cyan-300'
                                  : 'bg-slate-800 text-slate-500'
                              }`}
                            >
                              <div className="flex-1 min-w-0">
                                <div className={`font-mono truncate ${a.is_active !== 0 ? '' : 'line-through'}`}>{a.alias}</div>
                                <div className="mt-0.5 text-[11px] text-slate-500 font-mono truncate">{a.model_id}</div>
                              </div>
                              <div className="flex items-center gap-2 text-slate-400">
                                <ToggleSwitch
                                  checked={a.is_active !== 0}
                                  onChange={() => handleToggleModel(a.alias, a.is_active !== 0)}
                                  label={a.is_active !== 0 ? 'Disable model' : 'Enable model'}
                                />
                                <span>{a.is_active !== 0 ? 'Active' : 'Off'}</span>
                              </div>
                              {pendingDelete === `model:${a.alias}` ? (
                                <div className="inline-confirm">
                                  <span>Delete model?</span>
                                  <button onClick={() => handleDeleteModel(a.alias)} className="btn-danger text-xs">Delete</button>
                                  <button onClick={() => setPendingDelete(null)} className="btn-ghost text-xs">Cancel</button>
                                </div>
                              ) : (
                                <button onClick={() => setPendingDelete(`model:${a.alias}`)} className="btn-danger text-xs flex items-center gap-1">
                                  <X size={12} /> Delete Model
                                </button>
                              )}
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="text-xs text-slate-600">No models registered. Click "Fetch from Upstream" or add manually.</div>
                      )}
                    </div>

                    {testResult[p.id] && (
                      <div className="space-y-2">
                        {('valid' in testResult[p.id]) && (
                          <div className={`text-sm p-2 rounded-lg ${testResult[p.id].valid ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'}`}>
                            {testResult[p.id].valid
                              ? `Connected (${testResult[p.id].latency_ms}ms)`
                              : `${testResult[p.id].error || 'Failed'}`}
                          </div>
                        )}
                        {testResult[p.id].fetchedModels && (
                          <div className="text-sm p-2 rounded-lg bg-cyan-500/10 text-cyan-400">
                            Fetched {testResult[p.id].fetchedModels.added?.length || 0} models
                          </div>
                        )}
                        {testResult[p.id].toolTest && (
                          <div className={`text-sm p-2 rounded-lg ${testResult[p.id].toolTest.agent_ready ? 'bg-emerald-500/10 text-emerald-400' : 'bg-amber-500/10 text-amber-300'}`}>
                            {testResult[p.id].toolTest.agent_ready
                              ? `Agent command OK (${testResult[p.id].toolTest.model}, ${testResult[p.id].toolTest.latency_ms}ms, ${testResult[p.id].toolTest.tool_call_count || 0} tool_calls)`
                              : `Not support agent command: ${testResult[p.id].toolTest.error || 'No tool_calls returned'}`}
                          </div>
                        )}
                        {testResult[p.id].prefixMessage && (
                          <div className="text-sm p-2 rounded-lg bg-amber-500/10 text-amber-300">
                            {testResult[p.id].prefixMessage}
                          </div>
                        )}
                      </div>
                    )}

                    <div className="flex gap-2">
                      <button onClick={() => handleTest(p.id)} disabled={testing === p.id} className="btn-ghost text-xs flex items-center gap-1">
                        <Zap size={12} /> {testing === p.id ? 'Testing...' : 'Test'}
                      </button>
                      <button onClick={() => handleTestTools(p.id)} disabled={testingTools === p.id} className="btn-ghost text-xs flex items-center gap-1">
                        <Zap size={12} /> {testingTools === p.id ? 'Testing Agent...' : 'Agent Tools'}
                      </button>
                      <button onClick={() => navigate(`/keys/${p.id}`)} className="btn-ghost text-xs flex items-center gap-1">
                        <Edit3 size={12} /> Manage Keys
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
