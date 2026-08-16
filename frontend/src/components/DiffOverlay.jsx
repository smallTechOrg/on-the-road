import { useState, useEffect } from 'react'
import { fetchDiff } from '../lib/api'

function parseDiff(raw) {
  const files = []
  let current = null
  for (const line of raw.split('\n')) {
    if (line.startsWith('diff --git')) {
      const m = line.split(' b/')
      current = { path: m.length > 1 ? m[1] : 'unknown', lines: [] }
      files.push(current)
    } else if (!current) {
      continue
    } else if (line.startsWith('@@')) {
      current.lines.push({ type: 'hunk', text: line })
    } else if (line.startsWith('+++ ') || line.startsWith('--- ')) {
      // skip
    } else if (line.startsWith('+')) {
      current.lines.push({ type: 'add', text: line.slice(1) })
    } else if (line.startsWith('-')) {
      current.lines.push({ type: 'del', text: line.slice(1) })
    } else {
      current.lines.push({ type: 'ctx', text: line.startsWith(' ') ? line.slice(1) : line })
    }
  }
  return files
}

export default function DiffOverlay({ sessionId, onClose }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchDiff(sessionId)
      .then(d => { setData(d); setLoading(false) })
      .catch(err => { setError(err.message); setLoading(false) })
  }, [sessionId])

  const files = data?.diff ? parseDiff(data.diff) : []
  const stats = data?.stats || {}
  const fc = data?.files_changed?.length || 0

  return (
    <div className="overlay">
      <div className="overlay-header">
        <h2>Diff</h2>
        <button className="btn" onClick={onClose}>Close</button>
      </div>

      {loading && <div className="overlay-body"><p className="muted center" style={{ padding: '2rem' }}>Loading diff...</p></div>}
      {error && <div className="overlay-body"><p className="error-text" style={{ padding: '2rem' }}>Failed: {error}</p></div>}

      {data && (
        <>
          <div className="diff-stats">
            {fc} file{fc !== 1 ? 's' : ''} changed,{' '}
            <span className="add">+{stats.insertions || 0}</span>,{' '}
            <span className="del">-{stats.deletions || 0}</span>
          </div>
          <div className="overlay-body">
            {files.length === 0 && <p className="muted center" style={{ padding: '2rem' }}>No changes</p>}
            {files.map((file, fi) => (
              <details key={fi} className="diff-file" open>
                <summary>{file.path}</summary>
                {file.lines.map((ln, li) => {
                  if (ln.type === 'hunk') {
                    return <div key={li} className="diff-hunk-header">{ln.text}</div>
                  }
                  return (
                    <div key={li} className={`diff-line ${ln.type === 'add' ? 'add' : ln.type === 'del' ? 'del' : ''}`}>
                      <span className="diff-line-num">{li + 1}</span>
                      <span className="diff-line-content">{ln.text}</span>
                    </div>
                  )
                })}
              </details>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
