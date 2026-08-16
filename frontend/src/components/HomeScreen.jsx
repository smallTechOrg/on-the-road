import { useState, useEffect, useRef, useCallback } from 'react'
import { listSessions, createSession } from '../lib/api'

function relativeTime(iso) {
  if (!iso) return ''
  const secs = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000))
  if (secs < 60) return 'just now'
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

function SessionCard({ session, onClick }) {
  return (
    <button type="button" className="session-card" onClick={() => onClick(session)}>
      <div className="row1">
        <span className="title">{session.title || `${session.adapter} session`}</span>
        <span className={`pill ${session.status}`}>{session.status}</span>
      </div>
      <div className="row2">
        <span className="badge">
          {session.adapter === 'echo' ? 'Echo (dev)' : 'Hermes'}
        </span>
        <span>{relativeTime(session.updated_at)}</span>
        <span>{session.last_seq} events</span>
      </div>
    </button>
  )
}

function NewSessionSheet({ onClose, onCreated }) {
  const [adapter, setAdapter] = useState('hermes')
  const [title, setTitle] = useState('')
  const [workdir, setWorkdir] = useState('')
  const [error, setError] = useState(null)
  const [creating, setCreating] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setCreating(true)
    setError(null)
    try {
      const body = { adapter }
      if (title.trim()) body.title = title.trim()
      if (workdir.trim()) body.workdir = workdir.trim()
      const session = await createSession(body)
      onCreated(session)
    } catch (err) {
      setError(err.code === 'adapter_unavailable'
        ? `Agent unavailable: ${err.message}`
        : `Create failed: ${err.message}`)
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="sheet-backdrop" onClick={e => e.target === e.currentTarget && onClose()}>
      <form className="sheet" onSubmit={handleSubmit}>
        <h2>New session</h2>
        <label>
          Agent
          <select value={adapter} onChange={e => setAdapter(e.target.value)}>
            <option value="hermes">Hermes</option>
            <option value="echo">Echo (dev) — stub agent</option>
          </select>
        </label>
        <label>
          Title (optional)
          <input type="text" value={title} onChange={e => setTitle(e.target.value)} placeholder="e.g. Fix the login bug" />
        </label>
        <label>
          Workdir (optional)
          <input type="text" value={workdir} onChange={e => setWorkdir(e.target.value)} placeholder="server default" />
        </label>
        {error && <p className="error-text">{error}</p>}
        <div className="sheet-actions">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={creating}>
            {creating ? 'Creating…' : 'Create'}
          </button>
        </div>
      </form>
    </div>
  )
}

export default function HomeScreen({ onOpenSession, onLogout }) {
  const [sessions, setSessions] = useState(null)
  const [error, setError] = useState(null)
  const [showNew, setShowNew] = useState(false)
  const listRef = useRef(null)
  const touchStartY = useRef(null)

  const refresh = useCallback(async () => {
    try {
      const list = await listSessions()
      setSessions(list)
      setError(null)
    } catch (err) {
      if (err.status === 401) { onLogout(); return }
      setError(err.network ? 'Server unreachable — retrying…' : err.message)
    }
  }, [onLogout])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 15000)
    return () => clearInterval(id)
  }, [refresh])

  useEffect(() => {
    function onVis() {
      if (document.visibilityState === 'visible') refresh()
    }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [refresh])

  function handleTouchStart(e) {
    const el = listRef.current
    touchStartY.current = el && el.scrollTop === 0 ? e.touches[0].clientY : null
  }

  function handleTouchEnd(e) {
    if (touchStartY.current !== null && e.changedTouches[0].clientY - touchStartY.current > 80) {
      refresh()
    }
    touchStartY.current = null
  }

  return (
    <div className="screen">
      <header className="appbar">
        <h1>On The Road</h1>
      </header>

      <main
        ref={listRef}
        className="session-list"
        onTouchStart={handleTouchStart}
        onTouchEnd={handleTouchEnd}
      >
        {sessions === null && <p className="muted center">Loading sessions…</p>}
        {error && <p className="error-text center">{error}</p>}
        {sessions && sessions.length === 0 && (
          <p className="muted center">No sessions yet — create one</p>
        )}
        {sessions && sessions.map(s => (
          <SessionCard key={s.id} session={s} onClick={onOpenSession} />
        ))}
      </main>

      <button className="fab" onClick={() => setShowNew(true)} aria-label="New session">＋</button>

      {showNew && (
        <NewSessionSheet
          onClose={() => setShowNew(false)}
          onCreated={session => {
            setShowNew(false)
            onOpenSession(session)
          }}
        />
      )}
    </div>
  )
}
