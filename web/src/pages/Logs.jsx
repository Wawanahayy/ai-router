import { Fragment, useCallback, useEffect, useState } from 'react'
import { getLogs, getStats } from '../api'

export default function Logs() {
  const [logs, setLogs] = useState([])
  const [loading, setLoading] = useState(true)
  const [limit, setLimit] = useState(100)
  const [expanded, setExpanded] = useState(null)
  const [stats, setStats] = useState(null)

  const load = useCallback(async () => {
    try {
      const [nextLogs, nextStats] = await Promise.all([getLogs(limit), getStats()])
      setLogs(nextLogs)
      setStats(nextStats)
    } catch (e) { console.error(e) }
    finally { setLoading(false) }
  }, [limit])

  useEffect(() => { load() }, [load])

  if (loading) return <div className="text-slate-500">Loading...</div>

  function parseChain(value) {
    if (!value) return []
    if (Array.isArray(value)) return value
    try {
      const parsed = JSON.parse(value)
      return Array.isArray(parsed) ? parsed : []
    } catch {
      return []
    }
  }

  const usagePeriods = stats?.usage_periods || {}

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold text-white">Request Logs</h2>
        <div className="flex items-center gap-2">
          <label className="text-sm text-slate-400">Limit:</label>
          <select value={limit} onChange={e => setLimit(Number(e.target.value))} className="input max-w-[100px]">
            <option value={50}>50</option>
            <option value={100}>100</option>
            <option value={250}>250</option>
            <option value={500}>500</option>
          </select>
          <button onClick={load} className="btn-ghost text-sm">Refresh</button>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-3">
        {[
          ['Daily', usagePeriods.daily],
          ['Weekly', usagePeriods.weekly],
          ['Monthly', usagePeriods.monthly],
          ['All-time', usagePeriods.all_time],
        ].map(([label, usage]) => (
          <div key={label} className="card space-y-2">
            <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
            <div className="text-lg font-semibold text-white">{formatUsd(usage?.total_cost_usd || 0)}</div>
            {(usage?.unpriced_tokens || 0) > 0 && (
              <div className="text-xs text-amber-300">{formatNum(usage.unpriced_tokens)} tokens unpriced</div>
            )}
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div>
                <span className="text-slate-500">Input</span>
                <div className="text-slate-300">{formatNum(usage?.tokens_in || 0)}</div>
              </div>
              <div>
                <span className="text-slate-500">Output</span>
                <div className="text-slate-300">{formatNum(usage?.tokens_out || 0)}</div>
              </div>
            </div>
            <div className="text-xs text-slate-600">
              {formatNum(usage?.tokens_total || 0)} tokens / {usage?.requests || 0} req
              {(usage?.priced_requests || 0) > 0 ? ` / ${usage.priced_requests} priced` : ''}
            </div>
          </div>
        ))}
      </div>

      {logs.length === 0 ? (
        <div className="card text-slate-500 text-sm">No logs yet. Requests will appear here.</div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-slate-500 text-xs border-b border-[#1e1e2e]">
                <th className="text-left py-2 px-2">Time</th>
                <th className="text-left py-2 px-2">Provider</th>
                <th className="text-left py-2 px-2">Model</th>
                <th className="text-left py-2 px-2">Status</th>
                <th className="text-left py-2 px-2">Latency</th>
                <th className="text-left py-2 px-2">Tokens</th>
                <th className="text-left py-2 px-2">Cost</th>
                <th className="text-left py-2 px-2">Error</th>
                <th className="text-left py-2 px-2">Fallback</th>
              </tr>
            </thead>
            <tbody>
              {logs.map(l => {
                const chain = parseChain(l.fallback_chain)
                const isExpanded = expanded === l.id
                return (
                  <Fragment key={l.id}>
                    <tr key={l.id} className="border-b border-[#1e1e2e]/50 hover:bg-white/[0.02]">
                      <td className="py-2 px-2 text-xs text-slate-400 whitespace-nowrap">
                        {l.created_at ? new Date(l.created_at + 'Z').toLocaleString() : '-'}
                      </td>
                      <td className="py-2 px-2 text-slate-300">{l.provider_name || l.provider_id || '-'}</td>
                      <td className="py-2 px-2 text-slate-300 font-mono text-xs">{l.model || '-'}</td>
                      <td className="py-2 px-2">
                        <span className={`text-xs font-medium ${l.status_code < 400 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {l.status_code}
                        </span>
                      </td>
                      <td className="py-2 px-2 text-xs text-slate-500">{l.latency_ms ? `${l.latency_ms}ms` : '-'}</td>
                      <td className="py-2 px-2 text-xs text-slate-500">
                        {(l.tokens_in || l.tokens_out) ? `${l.tokens_in || 0}+${l.tokens_out || 0}` : '-'}
                      </td>
                      <td className="py-2 px-2 text-xs text-slate-500">
                        {l.pricing_source ? (
                          <div>
                            <div>{formatUsd(l.total_cost_usd)}</div>
                            <div className="text-slate-600">{formatUsd(l.input_cost_usd || 0)}+{formatUsd(l.output_cost_usd || 0)}</div>
                          </div>
                        ) : <span className="text-amber-300">unpriced</span>}
                      </td>
                      <td className="py-2 px-2 text-xs text-red-400 max-w-[250px] truncate">{l.error || '-'}</td>
                      <td className="py-2 px-2 text-xs">
                        {chain.length ? (
                          <button onClick={() => setExpanded(isExpanded ? null : l.id)} className="text-amber-300 hover:text-amber-200">
                            {chain.length} attempt{chain.length > 1 ? 's' : ''}
                          </button>
                        ) : (
                          <span className="text-slate-600">-</span>
                        )}
                      </td>
                    </tr>
                    {isExpanded && chain.length > 0 && (
                      <tr key={`${l.id}-fallback`} className="border-b border-[#1e1e2e]/50 bg-black/20">
                        <td colSpan={9} className="px-2 py-3">
                          <div className="space-y-1">
                            {chain.map((item, idx) => (
                              <div key={idx} className="text-xs text-slate-300 font-mono">
                                {idx + 1}. {item.provider || item.provider_id || '-'} / {item.model || '-'} / {item.key_label || item.key_id || 'no-key'} {'->'} {item.status_code || '-'} {item.error_kind || ''}
                                {item.latency_ms ? ` (${item.latency_ms}ms)` : ''}
                                {item.error ? <span className="text-red-300"> | {item.error}</span> : null}
                              </div>
                            ))}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function formatUsd(value) {
  const n = Number(value || 0)
  if (!n) return '$0'
  if (n < 0.0001) return `$${n.toFixed(6)}`
  if (n < 0.01) return `$${n.toFixed(5)}`
  return `$${n.toFixed(4)}`
}

function formatNum(n) {
  const value = Number(n || 0)
  if (value >= 1000000) return (value / 1000000).toFixed(1) + 'M'
  if (value >= 1000) return (value / 1000).toFixed(1) + 'K'
  return String(value)
}
