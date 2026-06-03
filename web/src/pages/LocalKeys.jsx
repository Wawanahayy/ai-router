import { useEffect, useState } from 'react'
import { getLocalKeys, createLocalKey, deleteLocalKey, toggleLocalKey } from '../api'
import { Plus, Trash2, Key, Copy, Eye, EyeOff } from 'lucide-react'
import ToggleSwitch from '../components/ToggleSwitch'

export default function LocalKeys() {
  const [keys, setKeys] = useState([])
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(false)
  const [copied, setCopied] = useState('')
  const [revealed, setRevealed] = useState({})
  const [pendingDelete, setPendingDelete] = useState(null)

  useEffect(() => { load() }, [])

  async function load() {
    try {
      setKeys(await getLocalKeys())
    } catch (e) { console.error(e) }
    finally { setLoading(false) }
  }

  async function handleAdd(e) {
    e.preventDefault()
    const fd = new FormData(e.target)
    const name = fd.get('name') || ''
    const customKey = fd.get('key') || null
    const result = await createLocalKey(name, customKey)
    setShowAdd(false)
    e.target.reset()
    load()
    if (result?.id) {
      setRevealed(prev => ({ ...prev, [result.id]: true }))
    }
  }

  async function handleDelete(id) {
    await deleteLocalKey(id)
    setPendingDelete(null)
    load()
  }

  async function handleToggle(id, isActive) {
    await toggleLocalKey(id, isActive ? 0 : 1)
    load()
  }

  function copyKey(keyValue, id) {
    navigator.clipboard.writeText(keyValue)
    setCopied(id)
    setTimeout(() => setCopied(''), 2000)
  }

  function toggleReveal(id) {
    setRevealed(prev => ({ ...prev, [id]: !prev[id] }))
  }

  function maskKey(key) {
    if (!key) return '???'
    if (key.length <= 16) return key.slice(0, 6) + '...' + key.slice(-4)
    return key.slice(0, 8) + '...' + key.slice(-4)
  }

  function activeKeyValue() {
    return keys.find(k => k.is_active !== 0)?.key_value || 'ar-your-key-here'
  }

  function commandExamples() {
    const apiKey = activeKeyValue()
    return [
      {
        id: 'openai-chat',
        title: 'OpenAI-compatible model',
        body: `curl http://localhost:32128/v1/chat/completions \\
  -H "Authorization: Bearer ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "gpt-4",
    "messages": [
      {"role": "user", "content": "hi"}
    ]
  }'`
      },
      {
        id: 'anthropic-messages',
        title: 'Anthropic-compatible model',
        body: `curl http://localhost:32128/v1/messages \\
  -H "Authorization: Bearer ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -H "anthropic-version: 2023-06-01" \\
  -d '{
    "model": "claude-3-haiku-20240307",
    "max_tokens": 256,
    "messages": [
      {"role": "user", "content": "hi"}
    ]
  }'`
      },
      {
        id: 'combo-chat',
        title: 'Combo route',
        body: `curl http://localhost:32128/v1/chat/completions \\
  -H "Authorization: Bearer ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "your-combo-name",
    "messages": [
      {"role": "user", "content": "hi"}
    ]
  }'`
      },
      {
        id: 'prefix-chat',
        title: 'Provider prefix model',
        body: `curl http://localhost:32128/v1/chat/completions \\
  -H "Authorization: Bearer ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "provider-prefix/model-id",
    "messages": [
      {"role": "user", "content": "hi"}
    ]
  }'`
      },
      {
        id: 'claude-cli-chat',
        title: 'Claude CLI provider model',
        body: `curl http://localhost:32128/v1/chat/completions \\
  -H "Authorization: Bearer ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "claude-sonnet-4-6",
    "messages": [
      {"role": "user", "content": "halo, jawab singkat"}
    ]
  }'`
      }
    ]
  }

  if (loading) return <div className="text-slate-500">Loading...</div>

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-white">API Keys</h2>
          <p className="text-xs text-slate-500 mt-1">Your own API keys for external users, apps, agents, or automation to access this router.</p>
        </div>
        <button onClick={() => setShowAdd(!showAdd)} className="btn-primary flex items-center gap-2 text-sm">
          <Plus size={16} /> Create Key
        </button>
      </div>

      {showAdd && (
        <form onSubmit={handleAdd} className="card space-y-3">
          <h3 className="font-semibold text-white">Create API Key</h3>
          <div>
            <label className="text-xs text-slate-500">Name / Label (optional, auto apikey-1 if empty)</label>
              <input name="name" className="input" placeholder="apikey-1" />
          </div>
          <div>
 <label className="text-xs text-slate-500">Custom Key (leave empty to auto-generate ar-xxx)</label>
 <input name="key" className="input font-mono text-xs" placeholder="ar-xxxx... (auto-generated if empty)" />
          </div>
          <p className="text-xs text-amber-400/70">Important: Copy the key after creation. It won't be shown in full again.</p>
          <div className="flex gap-2">
            <button type="submit" className="btn-primary text-sm">Create</button>
            <button type="button" onClick={() => setShowAdd(false)} className="btn-ghost text-sm">Cancel</button>
          </div>
        </form>
      )}

      {keys.length === 0 ? (
        <div className="card text-slate-500 text-sm">
          <Key size={32} className="mx-auto mb-2 text-slate-700" />
          <p>No API keys yet. Create one to give external users access to your proxy.</p>
        </div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-slate-500 text-xs border-b border-[#1e1e2e]">
                <th className="text-left py-2 px-2">Key</th>
                <th className="text-left py-2 px-2">Name</th>
                <th className="text-left py-2 px-2">Status</th>
                <th className="text-left py-2 px-2">Requests</th>
                <th className="text-left py-2 px-2">Tokens</th>
                <th className="text-left py-2 px-2">Last Used</th>
                <th className="text-right py-2 px-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {keys.map(k => (
                <tr key={k.id} className="border-b border-[#1e1e2e]/50 hover:bg-white/[0.02]">
                  <td className="py-2 px-2 font-mono text-xs text-slate-400">
                    <div className="flex items-center gap-1">
                      <span className="select-all">{revealed[k.id] ? k.key_value : maskKey(k.key_value)}</span>
                      <button onClick={() => toggleReveal(k.id)} className="text-slate-600 hover:text-slate-400" title={revealed[k.id] ? 'Hide' : 'Reveal'}>
                        {revealed[k.id] ? <EyeOff size={12} /> : <Eye size={12} />}
                      </button>
                      <button onClick={() => copyKey(k.key_value, k.id)} className="text-slate-600 hover:text-cyan-400" title="Copy">
                        <Copy size={12} />
                      </button>
                      {copied === k.id && <span className="text-emerald-400 text-[10px]">Copied!</span>}
                    </div>
                  </td>
                  <td className="py-2 px-2 text-slate-300">{k.name || '-'}</td>
                  <td className="py-2 px-2">
                    <div className="flex items-center gap-2 text-xs text-slate-400">
                      <ToggleSwitch
                        checked={k.is_active !== 0}
                        onChange={() => handleToggle(k.id, k.is_active)}
                        label={k.is_active ? 'Disable API key' : 'Enable API key'}
                      />
                      <span className={k.is_active ? 'text-emerald-400' : 'text-red-400'}>
                        {k.is_active ? 'Active' : 'Disabled'}
                      </span>
                    </div>
                  </td>
                  <td className="py-2 px-2 text-xs text-slate-500">{k.total_requests || 0}</td>
                  <td className="py-2 px-2 text-xs text-slate-500">{k.total_tokens || 0}</td>
                  <td className="py-2 px-2 text-xs text-slate-500">{k.last_used ? new Date(k.last_used).toLocaleString() : '-'}</td>
                  <td className="py-2 px-2 text-right">
                    {pendingDelete === k.id ? (
                      <div className="inline-confirm ml-auto">
                        <span>Delete API key?</span>
                        <button onClick={() => handleDelete(k.id)} className="btn-danger text-xs">Delete</button>
                        <button onClick={() => setPendingDelete(null)} className="btn-ghost text-xs">Cancel</button>
                      </div>
                    ) : (
                      <button onClick={() => setPendingDelete(k.id)} className="btn-danger text-xs flex items-center gap-1 ml-auto" title="Delete">
                        <Trash2 size={12} />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card space-y-2">
        <h3 className="font-semibold text-white text-sm">How to Use</h3>
        <div className="text-xs text-slate-400 space-y-1">
          <p><span className="text-slate-500">OpenAI endpoint:</span> <code className="text-emerald-400 font-mono">http://your-server:32128/v1/chat/completions</code></p>
          <p><span className="text-slate-500">Anthropic endpoint:</span> <code className="text-emerald-400 font-mono">http://your-server:32128/v1/messages</code></p>
          <p><span className="text-slate-500">Auth:</span> <code className="text-emerald-400 font-mono">Authorization: Bearer {activeKeyValue()}</code></p>
          <p><span className="text-slate-500">Model:</span> use a provider model, alias, combo name, or Claude CLI model that exists in AI Router.</p>
        </div>
        <div className="grid gap-3">
          {commandExamples().map(example => (
            <div key={example.id} className="rounded border border-[#1e1e2e] bg-black/20 overflow-hidden">
              <div className="flex items-center justify-between gap-3 border-b border-[#1e1e2e] px-3 py-2">
                <span className="text-xs font-medium text-slate-300">{example.title}</span>
                <button onClick={() => copyKey(example.body, example.id)} className="btn-ghost text-xs flex items-center gap-1">
                  <Copy size={12} /> {copied === example.id ? 'Copied' : 'Copy'}
                </button>
              </div>
              <pre className="p-3 text-xs text-slate-400 overflow-x-auto font-mono whitespace-pre">{example.body}</pre>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
