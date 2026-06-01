import { useCallback, useEffect, useState } from 'react'
import { getStats, getProviders, getSettings, getLocalKeys } from '../api'
import { Activity, Check, Coins, Copy, Key, Link, Server, Zap } from 'lucide-react'

export default function Dashboard() {
  const [stats, setStats] = useState(null)
  const [providers, setProviders] = useState([])
  const [settings, setSettings] = useState({})
  const [localKeys, setLocalKeys] = useState([])
  const [loading, setLoading] = useState(true)
  const [copied, setCopied] = useState(null)

  const load = useCallback(async () => {
    try {
      const [s, p, st, lk] = await Promise.all([getStats(), getProviders(), getSettings(), getLocalKeys()])
      setStats(s)
      setProviders(p)
      setSettings(st || {})
      setLocalKeys(lk || [])
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const iv = setInterval(load, 10000)
    return () => clearInterval(iv)
  }, [load])

  function copyToClipboard(text, label) {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(label)
      setTimeout(() => setCopied(null), 1500)
    })
  }

  if (loading) return <div className="text-slate-500">Loading...</div>

  const keyStats = stats?.key_stats || {}
  const totalKeys = Object.values(keyStats).reduce((a, b) => a + b, 0)
  const aliveKeys = keyStats.alive || 0
  const deadKeys = keyStats.dead || 0
  const cooldownKeys = keyStats.cooldown || 0
  const requireApiKey = settings?.require_api_key === 'true'
  const activeLocalKeys = (localKeys || []).filter(k => k.is_active)
  const openAIEndpoint = 'http://localhost:32128/v1/chat/completions'
  const anthropicEndpoint = 'http://localhost:32128/v1/messages'

  return (
    <div className="space-y-5">
      <header className="page-header">
        <div>
          <div className="page-kicker">Router status</div>
          <h2 className="page-title">Control room</h2>
        </div>
        <div className="live-pill">
          <span />
          Auto refresh 10s
        </div>
      </header>

      <section className="grid grid-cols-4 gap-3">
        <Metric icon={Zap} label="Requests today" value={stats?.total_today || 0} tone="blue" />
        <Metric icon={Coins} label="Tokens today" value={formatNum(stats?.tokens_today || 0)} tone="green" />
        <Metric icon={Server} label="Active providers" value={stats?.active_providers || 0} tone="amber" />
        <Metric icon={Key} label="Alive keys" value={`${aliveKeys}/${totalKeys}`} tone="teal" />
      </section>

      <section className="grid grid-cols-[1.4fr_0.8fr] gap-4">
        <div className="card endpoint-panel">
          <div className="section-head">
            <div>
              <div className="section-kicker">OpenAI + Anthropic compatible</div>
              <h3>Connection</h3>
            </div>
            <Link size={16} />
          </div>

          <div className="endpoint-box mb-2">
            <div>
              <span>OpenAI endpoint</span>
              <code>{openAIEndpoint}</code>
            </div>
            <button onClick={() => copyToClipboard(openAIEndpoint, 'openai-endpoint')} className="icon-button" title="Copy OpenAI endpoint">
              {copied === 'openai-endpoint' ? <Check size={15} /> : <Copy size={15} />}
            </button>
          </div>

          <div className="endpoint-box">
            <div>
              <span>Anthropic endpoint</span>
              <code>{anthropicEndpoint}</code>
            </div>
            <button onClick={() => copyToClipboard(anthropicEndpoint, 'anthropic-endpoint')} className="icon-button" title="Copy Anthropic endpoint">
              {copied === 'anthropic-endpoint' ? <Check size={15} /> : <Copy size={15} />}
            </button>
          </div>

          <div className="status-grid">
            <Info label="OpenAI format" value="Chat completions, combos, fallback" />
            <Info label="Anthropic format" value="Messages API, anthropic-version, streaming" />
            <Info
              label="Auth"
              value={
                requireApiKey
                  ? activeLocalKeys.length > 0
                    ? `Bearer ${activeLocalKeys[0].key_value.slice(0, 12)}...`
                    : 'Required, no active key'
                  : 'Open proxy'
              }
              warning={requireApiKey && activeLocalKeys.length === 0}
            />
          </div>

          {requireApiKey && activeLocalKeys.length > 0 && (
            <button onClick={() => copyToClipboard(activeLocalKeys[0].key_value, 'key')} className="btn-ghost text-xs mt-3">
              {copied === 'key' ? 'Copied API key' : 'Copy active API key'}
            </button>
          )}
        </div>

        <div className="card health-card">
          <div className="section-head">
            <div>
              <div className="section-kicker">Pool</div>
              <h3>Key health</h3>
            </div>
            <Activity size={16} />
          </div>
          <HealthRow label="Alive" value={aliveKeys} tone="green" total={totalKeys} />
          <HealthRow label="Cooldown" value={cooldownKeys} tone="amber" total={totalKeys} />
          <HealthRow label="Dead" value={deadKeys} tone="red" total={totalKeys} />
        </div>
      </section>

      <section className="grid grid-cols-[1fr_0.9fr] gap-4">
        <div className="card">
          <div className="section-head">
            <div>
              <div className="section-kicker">Routing</div>
              <h3>Providers</h3>
            </div>
            <span className="count-chip">{providers.length}</span>
          </div>

          {providers.length === 0 ? (
            <p className="empty-copy">No providers yet. Add one in the Providers tab.</p>
          ) : (
            <div className="provider-list">
              {providers.map(p => (
                <div key={p.id} className="provider-row">
                  <div className="provider-main">
                    <span className={`status-dot ${p.is_active ? 'on' : 'off'}`} />
                    <div>
                      <strong>{p.name}</strong>
                      <small>{p.type}</small>
                    </div>
                  </div>
                  <div className="provider-meta">
                    <span className="text-emerald-300">{p.key_stats?.alive || 0} alive</span>
                    <span>{p.aliases?.length || 0} models</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card">
          <div className="section-head">
            <div>
              <div className="section-kicker">Today</div>
              <h3>Usage by provider</h3>
            </div>
          </div>
          {stats?.provider_stats?.length > 0 ? (
            <div className="usage-list">
              {stats.provider_stats.map((ps, i) => (
                <div key={i} className="usage-row">
                  <span>{ps.name}</span>
                  <div>
                    <strong>{ps.requests}</strong>
                    <small>{formatNum(ps.tokens)} tokens</small>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="empty-copy">No provider traffic today.</p>
          )}
        </div>
      </section>
    </div>
  )
}

function Metric({ icon: Icon, label, value, tone }) {
  return (
    <div className={`metric-card ${tone}`}>
      <div className="metric-icon"><Icon size={18} /></div>
      <div>
        <strong>{value}</strong>
        <span>{label}</span>
      </div>
    </div>
  )
}

function Info({ label, value, warning }) {
  return (
    <div className={warning ? 'info-line warning' : 'info-line'}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function HealthRow({ label, value, tone, total }) {
  const pct = total > 0 ? Math.min(100, Math.round((value / total) * 100)) : 0
  return (
    <div className="health-row">
      <div className="flex items-center justify-between">
        <span>{label}</span>
        <strong className={`tone-${tone}`}>{value}</strong>
      </div>
      <div className="health-track">
        <i className={`tone-${tone}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

function formatNum(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K'
  return String(n)
}
