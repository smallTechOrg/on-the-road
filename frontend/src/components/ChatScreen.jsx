import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { useStream } from '../hooks/useStream'

const TOOL_ICONS = {
  execute: '🖥️', read: '📄', edit: '✏️', write: '✏️', delete: '🗑️',
  search: '🔎', fetch: '🌐', think: '💭', other: '🔧',
}

function escapeHtml(s) {
  const d = document.createElement('div')
  d.textContent = s
  return d.innerHTML
}

function renderMarkdown(text) {
  let html = escapeHtml(text)
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>')
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  html = html.replace(/\n/g, '<br/>')
  return html
}

function ConnIndicator({ state }) {
  const text = state === 'live' ? 'live' : state === 'reconnecting' ? 'reconnecting…' : state
  return (
    <div className="conn-indicator" data-state={state}>
      <span className="dot" />
      <span>{text}</span>
    </div>
  )
}

function UserBubble({ text }) {
  return <div className="evt evt-user">{text}</div>
}

function AgentBubble({ html }) {
  return <div className="evt evt-agent" dangerouslySetInnerHTML={{ __html: html }} />
}

function ThoughtBubble({ html }) {
  return <div className="evt evt-agent evt-thought" dangerouslySetInnerHTML={{ __html: html }} />
}

function ToolEvent({ evt }) {
  const p = evt.payload || {}
  const kind = p.kind || 'other'
  const icon = TOOL_ICONS[kind] || TOOL_ICONS.other
  const title = p.title || kind
  const status = evt.type === 'tool_end' ? (p.status || 'completed') : (p.status || 'running')
  const parts = []
  if (p.command) parts.push(`$ ${p.command}`)
  if (p.input) parts.push(typeof p.input === 'string' ? p.input : JSON.stringify(p.input, null, 2))
  if (p.output) parts.push(typeof p.output === 'string' ? p.output : JSON.stringify(p.output, null, 2))

  return (
    <details className="evt evt-tool">
      <summary>
        <span className="tool-kind">{icon}</span>
        <span className="tool-title">{title}</span>
        <span className={`tool-status ${status}`}>{status}</span>
      </summary>
      <div className="tool-body">{parts.join('\n\n') || '(no payload)'}</div>
    </details>
  )
}

function ErrorBubble({ message }) {
  return <div className="evt evt-error">{message}</div>
}

function MetaEvent({ text }) {
  return <div className="evt evt-meta">{text}</div>
}

function coalesceEvents(events) {
  const result = []
  let textBuf = null
  let thoughtBuf = null

  function flushText() {
    if (textBuf) { result.push({ kind: 'agent', html: renderMarkdown(textBuf) }); textBuf = null }
  }
  function flushThought() {
    if (thoughtBuf) { result.push({ kind: 'thought', html: renderMarkdown(thoughtBuf) }); thoughtBuf = null }
  }

  for (const evt of events) {
    const p = evt.payload || {}
    switch (evt.type) {
      case 'user_message':
        flushText(); flushThought()
        result.push({ kind: 'user', text: p.text || '' })
        break
      case 'agent_text':
        flushThought()
        textBuf = (textBuf || '') + (p.text || '')
        break
      case 'agent_thought':
        flushText()
        thoughtBuf = (thoughtBuf || '') + (p.text || '')
        break
      case 'tool_start':
      case 'tool_update':
      case 'tool_end':
        flushText(); flushThought()
        result.push({ kind: 'tool', evt })
        break
      case 'error':
        flushText(); flushThought()
        result.push({ kind: 'error', message: p.message || 'agent error' })
        break
      case 'status':
        break
      case 'turn_end':
        flushText(); flushThought()
        break
      case 'usage':
        break
      default:
        flushText(); flushThought()
        result.push({ kind: 'meta', text: `${evt.type}: ${JSON.stringify(p).slice(0, 200)}` })
    }
  }
  flushText()
  flushThought()
  return result
}

function mergeToolEvents(items) {
  const merged = []
  const toolMap = new Map()

  for (const item of items) {
    if (item.kind === 'tool') {
      const id = item.evt.payload?.tool_call_id || item.evt.payload?.id || `seq-${item.evt.seq}`
      if (toolMap.has(id)) {
        const idx = toolMap.get(id)
        merged[idx] = item
      } else {
        toolMap.set(id, merged.length)
        merged.push(item)
      }
    } else {
      merged.push(item)
    }
  }
  return merged
}

function Transcript({ events }) {
  const items = useMemo(() => mergeToolEvents(coalesceEvents(events)), [events])

  if (items.length === 0) {
    return <p className="muted center">Send a message to start the agent.</p>
  }

  return items.map((item, i) => {
    switch (item.kind) {
      case 'user': return <UserBubble key={i} text={item.text} />
      case 'agent': return <AgentBubble key={i} html={item.html} />
      case 'thought': return <ThoughtBubble key={i} html={item.html} />
      case 'tool': return <ToolEvent key={i} evt={item.evt} />
      case 'error': return <ErrorBubble key={i} message={item.message} />
      case 'meta': return <MetaEvent key={i} text={item.text} />
      default: return null
    }
  })
}

export default function ChatScreen({ session, onBack, onOpenTerminal, onOpenDiff }) {
  const { events, connState, send, cancel } = useStream(session.id)
  const [input, setInput] = useState('')
  const [mode, setMode] = useState('jam')
  const transcriptRef = useRef(null)
  const autoScrollRef = useRef(true)

  const turnRunning = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].type === 'turn_end' || events[i].type === 'error') return false
      if (events[i].type === 'user_message') return true
    }
    return false
  }, [events])

  const statusLine = useMemo(() => {
    if (!turnRunning) return 'idle'
    let activity = 'thinking…'
    let steps = 0
    let startTs = null
    for (const evt of events) {
      if (evt.type === 'user_message') { startTs = Date.now(); steps = 0; activity = 'thinking…' }
      if (evt.type === 'tool_start') steps++
      if (evt.type === 'status') activity = evt.payload?.title || evt.payload?.text || 'working…'
    }
    return `${activity} · ${steps} step${steps === 1 ? '' : 's'}`
  }, [events, turnRunning])

  useEffect(() => {
    const el = transcriptRef.current
    if (el && autoScrollRef.current) {
      el.scrollTop = el.scrollHeight
    }
  }, [events])

  function handleScroll() {
    const el = transcriptRef.current
    if (!el) return
    autoScrollRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60
  }

  async function handleSend() {
    const text = input.trim()
    if (!text) return
    setInput('')
    try {
      await send(text)
    } catch (err) {
      setInput(text)
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="screen">
      <header className="appbar chat-header">
        <button className="icon-btn" onClick={onBack} aria-label="Back">←</button>
        <div className="chat-titles">
          <h1>{session.title || `${session.adapter} session`}</h1>
          <div className="status-line muted">{statusLine}</div>
        </div>
        <div className="mode-toggle" role="group">
          <button
            className={`mode-btn ${mode === 'jam' ? 'active' : ''}`}
            onClick={() => setMode('jam')}
          >Jam</button>
          <button
            className={`mode-btn ${mode === 'debug' ? 'active' : ''}`}
            onClick={() => setMode('debug')}
          >Debug</button>
        </div>
      </header>

      <ConnIndicator state={connState} />

      <main ref={transcriptRef} className="transcript" onScroll={handleScroll}>
        <Transcript events={events} mode={mode} />
      </main>

      <footer className="composer-area">
        <div className="chips-row">
          <button className="chip chip-action" onClick={onOpenTerminal}>🖥️ Terminal</button>
          <button className="chip chip-action" onClick={onOpenDiff}>📝 Diff</button>
        </div>
        <form className="composer" onSubmit={e => { e.preventDefault(); handleSend() }}>
          <textarea
            rows={1}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Message the agent…"
            disabled={connState !== 'live'}
          />
          {turnRunning ? (
            <button type="button" className="btn danger" onClick={cancel}>Stop</button>
          ) : (
            <button type="submit" className="btn primary" disabled={connState !== 'live'}>Send</button>
          )}
        </form>
      </footer>
    </div>
  )
}
