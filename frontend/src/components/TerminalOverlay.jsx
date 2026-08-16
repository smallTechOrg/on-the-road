import { useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { termWsUrl } from '../lib/api'

export default function TerminalOverlay({ sessionId, onClose }) {
  const termRef = useRef(null)
  const containerRef = useRef(null)
  const wsRef = useRef(null)
  const [mobileInput, setMobileInput] = useState('')

  useEffect(() => {
    const term = new Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
      theme: {
        background: '#0f172a',
        foreground: '#e2e8f0',
        cursor: '#38bdf8',
        selectionBackground: '#334155',
      },
      convertEol: true,
    })
    const fitAddon = new FitAddon()
    term.loadAddon(fitAddon)
    termRef.current = term

    if (containerRef.current) {
      term.open(containerRef.current)
      fitAddon.fit()
    }

    const ws = new WebSocket(termWsUrl(sessionId))
    wsRef.current = ws

    ws.onmessage = (ev) => {
      try {
        const frame = JSON.parse(ev.data)
        if (frame.type === 'output') {
          term.write(frame.data)
        }
      } catch {}
    }

    ws.onerror = () => {
      term.write('\r\n\x1b[31mConnection error.\x1b[0m\r\n')
    }

    ws.onclose = () => {
      term.write('\r\n\x1b[90m[terminal disconnected]\x1b[0m\r\n')
    }

    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'input', data }))
      }
    })

    const onResize = () => fitAddon.fit()
    window.addEventListener('resize', onResize)

    return () => {
      window.removeEventListener('resize', onResize)
      try { ws.close() } catch {}
      term.dispose()
    }
  }, [sessionId])

  function sendMobileInput() {
    if (!mobileInput) return
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'input', data: mobileInput + '\n' }))
    }
    setMobileInput('')
  }

  return (
    <div className="overlay">
      <div className="overlay-header">
        <h2>Terminal</h2>
        <button className="btn" onClick={onClose}>Close</button>
      </div>
      <div className="term-container" ref={containerRef} />
      <div className="term-input-bar">
        <input
          type="text"
          value={mobileInput}
          onChange={e => setMobileInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), sendMobileInput())}
          placeholder="Type command..."
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
        />
        <button className="btn primary" onClick={sendMobileInput}>Send</button>
      </div>
    </div>
  )
}
