import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { login } from '../api'

export default function Login() {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(password)
      localStorage.setItem('ai_router_token', password)
      navigate('/')
    } catch {
      setError('Invalid password')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#0a0a0f] px-4">
      <form onSubmit={handleSubmit} className="card w-full max-w-sm space-y-4">
        <div>
          <h1 className="text-lg font-semibold text-white">AI Router</h1>
          <p className="text-xs text-slate-500 mt-1">Dashboard authentication</p>
        </div>
        <div>
          <label className="text-xs text-slate-500">Password</label>
          <input
            type="password"
            className="input mt-1"
            value={password}
            onChange={e => setPassword(e.target.value)}
            autoFocus
            required
          />
        </div>
        {error && <div className="text-sm text-red-400">{error}</div>}
        <button type="submit" className="btn-primary w-full" disabled={loading}>
          {loading ? 'Signing in...' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
