import { useEffect, useState } from 'react'
import { getSettings, updateSettings, getProviders, getAliases, addAlias, deleteAlias } from '../api'
import { Plus, Trash2 } from 'lucide-react'
import ToggleSwitch from '../components/ToggleSwitch'

export default function Settings() {
  const [settings, setSettings] = useState({})
  const [providers, setProviders] = useState([])
  const [aliases, setAliases] = useState([])
  const [loading, setLoading] = useState(true)
  const [showAlias, setShowAlias] = useState(false)
  const [saved, setSaved] = useState(false)
  const [pendingDelete, setPendingDelete] = useState(null)

  useEffect(() => { load() }, [])

  async function load() {
    try {
      const [s, p, a] = await Promise.all([getSettings(), getProviders(), getAliases()])
      setSettings(s)
      setProviders(p)
      setAliases(a)
    } catch (e) { console.error(e) }
    finally { setLoading(false) }
  }

  async function handleSave() {
    await updateSettings(settings)
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  async function handleAddAlias(e) {
    e.preventDefault()
    const fd = new FormData(e.target)
    await addAlias(fd.get('alias'), fd.get('provider_id'), fd.get('model_id'))
    setShowAlias(false)
    e.target.reset()
    load()
  }

  async function handleDeleteAlias(alias) {
    await deleteAlias(alias)
    setPendingDelete(null)
    load()
  }

  if (loading) return <div className="text-slate-500">Loading...</div>

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold text-white">Settings</h2>

      <div className="card space-y-4">
        <h3 className="font-semibold text-white">General</h3>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="text-xs text-slate-500">Load Balance Strategy</label>
            <select
              value={settings.strategy || 'round-robin'}
              onChange={e => setSettings({ ...settings, strategy: e.target.value })}
              className="input"
            >
              <option value="round-robin">Round Robin</option>
              <option value="fallback">Fallback</option>
              <option value="random">Random</option>
            </select>
          </div>
          <div>
            <label className="text-xs text-slate-500">Require Login</label>
            <select
              value={settings.require_login || 'false'}
              onChange={e => setSettings({ ...settings, require_login: e.target.value })}
              className="input"
            >
              <option value="false">No</option>
              <option value="true">Yes</option>
            </select>
          </div>
        </div>

        {settings.require_login === 'true' && (
          <div>
            <label className="text-xs text-slate-500">Dashboard Password</label>
            <input
              type="password"
              value={settings.login_password || ''}
              onChange={e => setSettings({ ...settings, login_password: e.target.value })}
              className="input max-w-xs"
              placeholder="Set password..."
            />
          </div>
        )}

        <div className="border-t border-[#1e1e2e] pt-4">
          <div className="flex items-center justify-between">
            <div>
              <h4 className="text-sm font-semibold text-white">Require API Key for Proxy</h4>
              <p className="text-xs text-slate-500 mt-0.5">When ON, requests to /v1/* must include a valid API key from the "API Keys" tab. When OFF, anyone can use the proxy.</p>
            </div>
            <ToggleSwitch
              checked={settings.require_api_key === 'true'}
              onChange={() => setSettings({ ...settings, require_api_key: settings.require_api_key === 'true' ? 'false' : 'true' })}
              label="Toggle proxy API key requirement"
            />
          </div>
          <p className="text-xs mt-1">
            <span className={settings.require_api_key === 'true' ? 'text-emerald-400' : 'text-amber-400'}>
              {settings.require_api_key === 'true' ? 'API Key required' : 'Proxy is open - no auth needed'}
            </span>
          </p>
        </div>

        <button onClick={handleSave} className="btn-primary text-sm">
          {saved ? 'Saved' : 'Save Settings'}
        </button>
      </div>

      <div className="card space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-white">Model Aliases</h3>
          <button onClick={() => setShowAlias(!showAlias)} className="btn-ghost text-sm flex items-center gap-1">
            <Plus size={14} /> Add Alias
          </button>
        </div>

        <p className="text-xs text-slate-500">Map short model names to provider + actual model. e.g. "gpt4" -&gt; openai/gpt-4</p>

        {showAlias && (
          <form onSubmit={handleAddAlias} className="bg-white/5 rounded-lg p-3 space-y-3">
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="text-xs text-slate-500">Alias</label>
                <input name="alias" className="input" placeholder="gpt4" required />
              </div>
              <div>
                <label className="text-xs text-slate-500">Provider</label>
                <select name="provider_id" className="input" required>
                  <option value="">-- pick --</option>
                  {providers.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs text-slate-500">Actual Model</label>
                <input name="model_id" className="input" placeholder="gpt-4" required />
              </div>
            </div>
            <div className="flex gap-2">
              <button type="submit" className="btn-primary text-sm">Add</button>
              <button type="button" onClick={() => setShowAlias(false)} className="btn-ghost text-sm">Cancel</button>
            </div>
          </form>
        )}

        {aliases.length === 0 ? (
          <p className="text-sm text-slate-500">No aliases yet.</p>
        ) : (
          <div className="space-y-1">
            {aliases.map(a => (
              <div key={a.alias} className="flex items-center justify-between bg-white/5 rounded-lg px-3 py-2 text-sm">
                <div className="flex items-center gap-3">
                  <span className="font-mono text-cyan-400">{a.alias}</span>
                  <span className="text-slate-600">-&gt;</span>
                  <span className="text-slate-300">{a.provider_name || a.provider_id}</span>
                  <span className="text-slate-600">/</span>
                  <span className="text-slate-400 font-mono text-xs">{a.model_id}</span>
                </div>
                {pendingDelete === a.alias ? (
                  <div className="inline-confirm">
                    <span>Delete alias?</span>
                    <button onClick={() => handleDeleteAlias(a.alias)} className="btn-danger text-xs">Delete</button>
                    <button onClick={() => setPendingDelete(null)} className="btn-ghost text-xs">Cancel</button>
                  </div>
                ) : (
                  <button onClick={() => setPendingDelete(a.alias)} className="btn-danger text-xs flex items-center gap-1">
                    <Trash2 size={12} /> Delete
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card space-y-3">
        <h3 className="font-semibold text-white">Connection Info</h3>
        <div className="text-sm space-y-1">
          <div className="flex items-center gap-2">
            <span className="text-slate-500 w-32">OpenAI:</span>
            <code className="text-emerald-400 font-mono text-xs">http://localhost:32128/v1/chat/completions</code>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-slate-500 w-32">Anthropic:</span>
            <code className="text-emerald-400 font-mono text-xs">http://localhost:32128/v1/messages</code>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-slate-500 w-32">OpenAI format:</span>
            <span className="text-slate-300">Chat completions with Bearer token</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-slate-500 w-32">Anthropic format:</span>
            <span className="text-slate-300">Messages API with Bearer token from ai-router</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-slate-500 w-32">Auth Key:</span>
            <span className="text-slate-300 text-xs">
              {settings.require_api_key === 'true'
                ? 'Your API key from "API Keys" tab (ar-xxx...)'
                : 'None required (proxy is open)'}
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}
