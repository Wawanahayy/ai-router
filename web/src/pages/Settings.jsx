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

        <div className="border-t border-[#1e1e2e] pt-4">
          <div className="flex items-center justify-between">
            <div>
              <h4 className="text-sm font-semibold text-white">Upstream Proxy</h4>
              <p className="text-xs text-slate-500 mt-0.5">Route all upstream API requests through a proxy. Works with any proxy provider that supports SOCKS5 or HTTP/HTTPS.</p>
            </div>
            <ToggleSwitch
              checked={settings.proxy_enabled === 'true'}
              onChange={() => setSettings({ ...settings, proxy_enabled: settings.proxy_enabled === 'true' ? 'false' : 'true' })}
              label="Toggle upstream proxy"
            />
          </div>

          <details className="mt-3 bg-white/[0.035] border border-white/[0.06] rounded-lg">
            <summary className="text-xs text-slate-400 cursor-pointer px-3 py-2 select-none">📖 Supported proxy formats & examples</summary>
            <div className="px-3 pb-3 text-xs text-slate-400 space-y-2">
              <div>
                <p className="text-slate-300 font-semibold mb-1">Supported types:</p>
                <ul className="list-disc list-inside space-y-0.5">
                  <li><code className="text-cyan-400">socks5</code> — SOCKS5 proxy (Tor, Dante, etc.)</li>
                  <li><code className="text-cyan-400">socks5h</code> — SOCKS5 with DNS resolution via proxy (prevents DNS leak)</li>
                  <li><code className="text-cyan-400">http</code> — HTTP CONNECT proxy</li>
                  <li><code className="text-cyan-400">https</code> — HTTPS proxy (encrypted to proxy)</li>
                </ul>
              </div>
              <div>
                <p className="text-slate-300 font-semibold mb-1">Examples:</p>
                <ul className="space-y-1">
                  <li><span className="text-slate-500">Tor (local, no auth):</span> <code className="text-emerald-400">socks5://127.0.0.1:9050</code></li>
                  <li><span className="text-slate-500">Tor with DNS leak protection:</span> <code className="text-emerald-400">socks5h://127.0.0.1:9050</code></li>
                  <li><span className="text-slate-500">2Captcha proxy:</span> <code className="text-emerald-400">socks5://username:password@1.2.3.4:1080</code></li>
                  <li><span className="text-slate-500">Bright Data residential:</span> <code className="text-emerald-400">http://user-session-xyz:pass@1.2.3.4:22225</code></li>
                  <li><span className="text-slate-500">Webshare datacenter:</span> <code className="text-emerald-400">socks5h://user:pass@1.2.3.4:1080</code></li>
                  <li><span className="text-slate-500">Oxylabs rotating:</span> <code className="text-emerald-400">http://customer-cc:pass@1.2.3.4:7777</code></li>
                  <li><span className="text-slate-500">Smartproxy/storm:</span> <code className="text-emerald-400">http://user-pass-abc:pass@gate.smartproxy.com:7000</code></li>
                </ul>
              </div>
              <div>
                <p className="text-slate-300 font-semibold mb-1">How to use:</p>
                <p>Fill in <b>Type</b> + <b>Host</b> + <b>Port</b> (and optionally <b>Username</b> + <b>Password</b>) — the URL is built automatically. Or paste a full URL in the raw field below (overrides the structured fields).</p>
              </div>
              <div>
                <p className="text-slate-300 font-semibold mb-1">Notes:</p>
                <ul className="list-disc list-inside space-y-0.5">
                  <li>SOCKS5 requires <code className="text-cyan-400">socksio</code> package (auto-installed in venv)</li>
                  <li>For Tor: set host=127.0.0.1, port=9050, type=socks5, no username/password</li>
                  <li>For authenticated proxies (2Captcha, Bright Data, Webshare, etc.): fill username + password</li>
                  <li>Use <code className="text-cyan-400">socks5h</code> if you want DNS resolved through proxy (prevents DNS leak to VPS resolver)</li>
                  <li>Proxy applies to ALL upstream provider requests — provider tests, model fetch, pricing fetch, chat completions, streaming</li>
                </ul>
              </div>
            </div>
          </details>

          <div className="grid grid-cols-2 gap-3 mt-3">
            <div>
              <label className="text-xs text-slate-500">Proxy Type</label>
              <select
                value={settings.proxy_type || 'socks5'}
                onChange={e => setSettings({ ...settings, proxy_type: e.target.value })}
                className="input"
              >
                <option value="socks5">SOCKS5</option>
                <option value="socks5h">SOCKS5 (DNS via proxy)</option>
                <option value="http">HTTP</option>
                <option value="https">HTTPS</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-slate-500">Host / IP</label>
              <input
                type="text"
                value={settings.proxy_host || ''}
                onChange={e => setSettings({ ...settings, proxy_host: e.target.value })}
                className="input"
                placeholder="127.0.0.1"
              />
            </div>
            <div>
              <label className="text-xs text-slate-500">Port</label>
              <input
                type="text"
                value={settings.proxy_port || ''}
                onChange={e => setSettings({ ...settings, proxy_port: e.target.value })}
                className="input"
                placeholder="9050"
              />
            </div>
            <div>
              <label className="text-xs text-slate-500">Username (optional)</label>
              <input
                type="text"
                value={settings.proxy_username || ''}
                onChange={e => setSettings({ ...settings, proxy_username: e.target.value })}
                className="input"
                placeholder="proxyuser"
              />
            </div>
            <div>
              <label className="text-xs text-slate-500">Password (optional)</label>
              <input
                type="password"
                value={settings.proxy_password || ''}
                onChange={e => setSettings({ ...settings, proxy_password: e.target.value })}
                className="input"
                placeholder="••••••••"
              />
            </div>
          </div>
          <div className="mt-3">
            <label className="text-xs text-slate-500">Or raw proxy URL (overrides fields above)</label>
            <input
              type="text"
              value={settings.proxy_url || ''}
              onChange={e => setSettings({ ...settings, proxy_url: e.target.value })}
              className="input"
              placeholder="socks5://user:pass@127.0.0.1:9050"
            />
          </div>
          <p className="text-xs mt-1">
            <span className={settings.proxy_enabled === 'true' ? 'text-emerald-400' : 'text-slate-500'}>
              {settings.proxy_enabled === 'true'
                ? (settings.proxy_host && settings.proxy_port
                  ? `Active: ${(settings.proxy_type || 'socks5')}://${settings.proxy_host}:${settings.proxy_port}`
                  : settings.proxy_url
                    ? `Active: ${settings.proxy_url}`
                    : 'Proxy enabled but no host/port or URL set')
                : 'Proxy disabled'}
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
