import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { Activity, Braces, ChevronLeft, ChevronRight, Gauge, KeyRound, LayoutDashboard, Layers, LogOut, ScrollText, Server, Settings, Shield, Terminal } from 'lucide-react'
import { logout } from './api'

const nav = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/providers', icon: Server, label: 'Providers' },
  { to: '/keys', icon: KeyRound, label: 'Upstream Keys' },
  { to: '/api-keys', icon: Shield, label: 'API Keys' },
  { to: '/combos', icon: Layers, label: 'Combos' },
  { to: '/logs', icon: ScrollText, label: 'Logs' },
  { to: '/settings', icon: Settings, label: 'Settings' },
]

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(true)

  async function handleLogout() {
    try { await logout() } catch (e) { console.error(e) }
    localStorage.removeItem('ai_router_token')
    location.hash = '/login'
  }

  return (
    <div className={`app-shell flex h-screen text-[var(--text)] ${sidebarOpen ? 'sidebar-open' : 'sidebar-collapsed'}`}>
      <aside className="app-sidebar">
        <div className="p-4 border-b border-[var(--line)]">
          <div className="flex items-center gap-3 min-w-0">
            <img src="/favicon.png" alt="" className="sidebar-logo" />
            <div className="sidebar-copy">
              <h1 className="text-[15px] font-semibold text-white leading-tight">AI Router</h1>
              <p className="text-[11px] text-[var(--muted)] mt-0.5">Provider control plane</p>
            </div>
            <button
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="sidebar-toggle"
              title={sidebarOpen ? 'Hide menu' : 'Show menu'}
            >
              {sidebarOpen ? <ChevronLeft size={15} /> : <ChevronRight size={15} />}
            </button>
          </div>
        </div>

        <nav className="flex-1 p-2.5 space-y-1">
          {nav.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-md text-[13px] font-medium transition-colors border ${
                  isActive
                    ? 'bg-white/[0.08] text-white border-white/10'
                    : 'text-[var(--muted)] border-transparent hover:text-slate-100 hover:bg-white/[0.045]'
                }`
              }
            >
              <Icon size={16} />
              <span className="nav-label">{label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="p-3 border-t border-[var(--line)] space-y-2">
          <button onClick={handleLogout} className="btn-ghost w-full text-xs flex items-center justify-center gap-2">
            <LogOut size={14} /> <span className="nav-label">Logout</span>
          </button>
          <div className="sidebar-port flex items-center justify-center gap-2 text-[11px] text-[var(--faint)]">
            <span className="h-1.5 w-1.5 rounded-full bg-[var(--green)]" />
            Port 32128
          </div>
        </div>
      </aside>

      <main className="flex-1 overflow-auto">
        <div className="max-w-7xl mx-auto p-6">
          <Outlet />
        </div>
      </main>

      <aside className="operator-rail">
        <div className="rail-card identity">
          <div className="rail-logo">
            <img src="/favicon.png" alt="" />
          </div>
          <div>
            <strong>AI Router</strong>
            <span>local gateway</span>
          </div>
        </div>

        <div className="rail-section">
          <div className="rail-title">Runtime</div>
          <RailItem icon={Activity} label="Status" value="online" tone="green" />
          <RailItem icon={Gauge} label="Port" value="32128" />
          <RailItem icon={Terminal} label="Agent tools" value="guarded" tone="teal" />
        </div>

        <div className="rail-section">
          <div className="rail-title">Gateway</div>
          <RailItem icon={Braces} label="API shape" value="OpenAI + Anthropic" />
          <RailItem icon={Layers} label="Fallback" value="combo first" tone="amber" />
          <RailItem icon={KeyRound} label="Keys" value="rotating" />
        </div>

        <div className="rail-note">
          <span>OpenAI</span>
          <code>/v1/chat/completions</code>
          <span className="mt-2">Anthropic</span>
          <code>/v1/messages</code>
        </div>
      </aside>
    </div>
  )
}

function RailItem({ icon: Icon, label, value, tone }) {
  return (
    <div className="rail-item">
      <Icon size={14} className={tone ? `rail-${tone}` : ''} />
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
    </div>
  )
}
