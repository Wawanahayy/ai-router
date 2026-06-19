import { useEffect, useState } from 'react'

function formatUptime(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds))
  const days = Math.floor(s / 86400)
  const hours = Math.floor((s % 86400) / 3600)
  const minutes = Math.floor((s % 3600) / 60)
  const seconds = s % 60

  if (days > 0) {
    return `${days}D ${String(hours).padStart(2, '0')}H ${String(minutes).padStart(2, '0')}M`
  }
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

export default function UptimeHUD() {
  // null until we successfully read service start from backend.
  // No page-load fallback — uptime must come from ai-router.service, never reset on refresh.
  const [startedAt, setStartedAt] = useState(null)
  const [now, setNow] = useState(() => Date.now() / 1000)

  useEffect(() => {
    let cancelled = false
    fetch('/api/uptime', { credentials: 'include' })
      .then(r => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(data => {
        if (!cancelled && typeof data.started_at === 'number') {
          setStartedAt(data.started_at)
        }
      })
      .catch(() => { /* endpoint not yet available — keep null, show ··· */ })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (startedAt == null) return
    const id = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(id)
  }, [startedAt])

  const display = startedAt == null ? '· · · · ·' : formatUptime(now - startedAt)

  return (
    <div className="hud-text" aria-label="SYSTEM UPTIME" title="Service process uptime — counted from ai-router.service start, persists across page refreshes">
      <span className={`hud-dot ${startedAt == null ? 'hud-dot--dead' : ''}`} />
      <span className="hud-label">SYSTEM UPTIME</span>
      <span className="hud-value">{display}</span>
    </div>
  )
}