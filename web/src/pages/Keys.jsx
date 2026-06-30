import { useCallback, useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { getProviders, getKeys, addKey, addKeysBulk, deleteKey, activateKey, deactivateKey } from '../api'
import { Plus, Trash2, RotateCcw, Upload } from 'lucide-react'

export default function Keys() {
  const { providerId } = useParams()
  const navigate = useNavigate()
  const [providers, setProviders] = useState([])
  const [selectedProvider, setSelectedProvider] = useState(providerId || '')
  const [keys, setKeys] = useState([])
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(false)
  const [showBulk, setShowBulk] = useState(false)
  const [filterStatus, setFilterStatus] = useState('')
  const [pendingDelete, setPendingDelete] = useState(null)
  const [now, setNow] = useState(() => Date.now())

  // Auto-refresh countdown every second
  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(interval)
  }, [])

  const loadProviders = useCallback(async () => {
    try {
      const p = await getProviders()
      setProviders(p)
      // Don't auto-select — default stays "" (All Providers)
    } catch (e) { console.error(e) }
    finally { setLoading(false) }
  }, [])

  const loadKeys = useCallback(async () => {
    try {
      const k = await getKeys(selectedProvider || undefined) // undefined = all
      setKeys(filterStatus ? k.filter(x => x.status === filterStatus) : k)
    } catch (e) { console.error(e) }
  }, [selectedProvider, filterStatus])

  useEffect(() => {
    loadProviders()
  }, [loadProviders])

  useEffect(() => {
    if (!loading) loadKeys()
  }, [selectedProvider, filterStatus, loading, loadKeys])

  async function handleAddKey(e) {
    e.preventDefault()
    const fd = new FormData(e.target)
    await addKey(selectedProvider, fd.get('key'), fd.get('label'))
    setShowAdd(false)
    e.target.reset()
    loadKeys()
  }

  async function handleBulkAdd(e) {
    e.preventDefault()
    const fd = new FormData(e.target)
    const raw = fd.get('keys').trim()
    const keyList = raw.split('\n').map(k => k.trim()).filter(Boolean)
    if (keyList.length === 0) return
    await addKeysBulk(selectedProvider, keyList)
    setShowBulk(false)
    e.target.reset()
    loadKeys()
  }

  async function handleDelete(id) {
    await deleteKey(id)
    setPendingDelete(null)
    loadKeys()
  }

  async function handleActivate(id) {
    await activateKey(id)
    loadKeys()
  }

  async function handleDeactivate(id) {
    await deactivateKey(id)
    loadKeys()
  }

  if (loading) return <div className="text-slate-500">Loading...</div>

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold text-white">API Keys</h2>
        <div className="flex gap-2">
          <button onClick={() => setShowAdd(!showAdd)} className="btn-primary text-sm flex items-center gap-1">
            <Plus size={14} /> Add Key
          </button>
          <button onClick={() => setShowBulk(!showBulk)} className="btn-ghost text-sm flex items-center gap-1">
            <Upload size={14} /> Bulk Add
          </button>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <label className="text-sm text-slate-400">Provider:</label>
        <select
          value={selectedProvider}
          onChange={e => {
            const val = e.target.value
            setSelectedProvider(val)
            navigate(val ? `/keys/${val}` : '/keys', { replace: true })
          }}
          className="input max-w-xs"
        >
          <option value="">All Providers</option>
          {providers.map(p => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>

        <label className="text-sm text-slate-400 ml-4">Status:</label>
        <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)} className="input max-w-[120px]">
          <option value="">All</option>
          <option value="alive">Alive</option>
          <option value="cooldown">Cooldown</option>
          <option value="dead">Dead</option>
        </select>
      </div>

      {showAdd && (
        <form onSubmit={handleAddKey} className="card space-y-3">
          <h3 className="font-semibold text-white">Add Key</h3>
          <div>
            <label className="text-xs text-slate-500">API Key</label>
            <input name="key" className="input" placeholder="nvapi-..." required />
          </div>
          <div>
            <label className="text-xs text-slate-500">Label (optional, auto apikey-1 if empty)</label>
            <input name="label" className="input" placeholder="apikey-1" />
          </div>
          <div className="flex gap-2">
            <button type="submit" className="btn-primary text-sm">Add</button>
            <button type="button" onClick={() => setShowAdd(false)} className="btn-ghost text-sm">Cancel</button>
          </div>
        </form>
      )}

      {showBulk && (
        <form onSubmit={handleBulkAdd} className="card space-y-3">
          <h3 className="font-semibold text-white">Bulk Add Keys</h3>
          <div>
            <label className="text-xs text-slate-500">API Keys (one per line, labels auto-numbered)</label>
            <textarea name="keys" className="input h-32 font-mono text-xs" placeholder="nvapi-xxx&#10;nvapi-yyy&#10;nvapi-zzz" required />
          </div>
          <div className="flex gap-2">
            <button type="submit" className="btn-primary text-sm">Add All</button>
            <button type="button" onClick={() => setShowBulk(false)} className="btn-ghost text-sm">Cancel</button>
          </div>
        </form>
      )}

      {keys.length === 0 ? (
        <div className="card text-slate-500 text-sm">No keys found for this provider.</div>
      ) : (() => {
        const grouped = {}
        for (const k of keys) {
          const pn = k.provider_name || 'unknown'
          if (!grouped[pn]) grouped[pn] = []
          grouped[pn].push(k)
        }
        return (
          <div className="space-y-6">
            {Object.entries(grouped).map(([providerName, providerKeys]) => (
              <div key={providerName} className="card overflow-x-auto">
                <h3 className="text-sm font-semibold text-slate-400 mb-3 uppercase tracking-wider">{providerName}</h3>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-slate-500 text-xs border-b border-[#1e1e2e]">
                      <th className="text-left py-2 px-2">Key</th>
                      <th className="text-left py-2 px-2">Label</th>
                      <th className="text-left py-2 px-2">Status</th>
                      <th className="text-left py-2 px-2">Cooldown</th>
                      <th className="text-left py-2 px-2">Last Used</th>
                      <th className="text-left py-2 px-2">Error</th>
                      <th className="text-left py-2 px-2">Reqs</th>
                      <th className="text-right py-2 px-2">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {providerKeys.map(k => {
                      const lock = k.lock
                      const remaining = lock ? Math.max(0, lock.remaining - Math.floor((Date.now() - now) / 1000)) : 0
                      const statusLocked = lock && remaining > 0 && k.status === 'alive'
                      return (
                        <tr key={k.id} className="border-b border-[#1e1e2e]/50 hover:bg-white/[0.02]">
                          <td className="py-2 px-2 font-mono text-xs text-slate-400">{maskKey(k.key_value)}</td>
                          <td className="py-2 px-2 text-slate-300">{k.label || '-'}</td>
                          <td className="py-2 px-2">
                            <StatusBadge status={k.status} locked={statusLocked} />
                          </td>
                          <td className="py-2 px-2">
                            {statusLocked ? (
                              <CountdownTimer seconds={remaining} retry={lock?.retry_count || 0} />
                            ) : k.status === 'cooldown' ? (
                              <span className="text-yellow-400/70 text-xs">cooldown</span>
                            ) : k.status === 'dead' ? (
                              <span className="text-red-400/70 text-xs">-</span>
                            ) : (
                              <span className="text-slate-600 text-xs">-</span>
                            )}
                          </td>
                          <td className="py-2 px-2 text-xs text-slate-500">{k.last_used ? new Date(k.last_used).toLocaleString() : '-'}</td>
                          <td className="py-2 px-2 text-xs max-w-[200px]">
                            {statusLocked && lock ? (
                              <span title={lock.error} className="text-amber-400 truncate block">
                                {lock.error_code}: {lock.model} locked
                              </span>
                            ) : k.last_error ? (
                              <span className="text-red-400 truncate block" title={k.last_error}>
                                {k.error_code && `${k.error_code}: `}{k.last_error.slice(0, 60)}
                              </span>
                            ) : '-'}
                          </td>
                          <td className="py-2 px-2 text-xs text-slate-500">{k.total_requests || 0}</td>
                          <td className="py-2 px-2 text-right">
                            <div className="flex gap-1 justify-end">
                              {k.status === 'dead' && (
                                <button onClick={() => handleActivate(k.id)} className="btn-ghost text-xs flex items-center gap-1" title="Reactivate">
                                  <RotateCcw size={12} />
                                </button>
                              )}
                              {k.status === 'alive' && (
                                <button onClick={() => handleDeactivate(k.id)} className="btn-ghost text-xs" title="Deactivate">
                                  Disable
                                </button>
                              )}
                              {pendingDelete === k.id ? (
                                <div className="inline-confirm">
                                  <span>Delete key?</span>
                                  <button onClick={() => handleDelete(k.id)} className="btn-danger text-xs">Delete</button>
                                  <button onClick={() => setPendingDelete(null)} className="btn-ghost text-xs">Cancel</button>
                                </div>
                              ) : (
                                <button onClick={() => setPendingDelete(k.id)} className="btn-danger text-xs flex items-center gap-1" title="Delete">
                                  <Trash2 size={12} />
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        )
      })()}
    </div>
  )
}

function StatusBadge({ status, locked }) {
  if (locked) {
    return <span className="badge-cooldown">cooldown</span>
  }
  return <span className={`badge-${status}`}>{status}</span>
}

function CountdownTimer({ seconds, retry }) {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  const display = m > 0 ? `${m}m${s}s` : `${s}s`
  // Color: brighter yellow when almost done
  const pct = Math.min(seconds / 60, 1)
  const brightness = Math.floor(50 + pct * 50)
  return (
    <span
      className="font-mono text-xs"
      style={{ color: `hsl(45, 100%, ${brightness}%)` }}
      title={`Retry #${retry}`}
    >
      {display}
    </span>
  )
}

function maskKey(key) {
  if (!key) return '???'
  if (key.length <= 12) return key.slice(0, 4) + '...' + key.slice(-4)
  return key.slice(0, 8) + '...' + key.slice(-4)
}
