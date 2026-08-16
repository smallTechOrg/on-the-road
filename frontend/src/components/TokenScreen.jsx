import { useState } from 'react'
import { setToken, checkToken } from '../lib/api'

export default function TokenScreen({ onAuth }) {
  const [value, setValue] = useState('')
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    const token = value.trim()
    if (!token) return
    setLoading(true)
    setError(null)
    setToken(token)
    try {
      await checkToken()
      onAuth()
    } catch (err) {
      setError(err.network ? 'Server unreachable' : 'Invalid token')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="screen screen-center">
      <div className="token-card">
        <h1>On The Road</h1>
        <p className="muted">Enter your access token to connect.</p>
        <form onSubmit={handleSubmit}>
          <input
            type="password"
            value={value}
            onChange={e => setValue(e.target.value)}
            placeholder="Access token"
            autoComplete="current-password"
            required
          />
          <button type="submit" className="btn primary" disabled={loading}>
            {loading ? 'Connecting…' : 'Connect'}
          </button>
        </form>
        {error && <p className="error-text">{error}</p>}
      </div>
    </div>
  )
}
