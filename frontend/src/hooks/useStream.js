import { useState, useEffect, useRef, useCallback } from 'react'
import { streamUrl, sendMessage, cancelTurn } from '../lib/api'

export function useStream(sessionId) {
  const [events, setEvents] = useState([])
  const [lastSeq, setLastSeq] = useState(0)
  const [connState, setConnState] = useState('idle')
  const wsRef = useRef(null)
  const backoffRef = useRef(1000)
  const reconnectRef = useRef(null)
  const closedRef = useRef(false)
  const lastSeqRef = useRef(0)

  useEffect(() => {
    if (!sessionId) return
    closedRef.current = false
    lastSeqRef.current = 0
    setEvents([])
    setLastSeq(0)
    setConnState('reconnecting')

    function connect() {
      if (closedRef.current) return
      const ws = new WebSocket(streamUrl(sessionId, lastSeqRef.current))
      wsRef.current = ws

      ws.onopen = () => {
        backoffRef.current = 1000
        setConnState('live')
      }

      ws.onmessage = (m) => {
        let evt
        try { evt = JSON.parse(m.data) } catch { return }
        if (evt.type === 'pong') return

        if (typeof evt.seq === 'number') {
          lastSeqRef.current = Math.max(lastSeqRef.current, evt.seq)
          setLastSeq(lastSeqRef.current)
        }
        setEvents(prev => {
          if (typeof evt.seq === 'number' && prev.some(e => e.seq === evt.seq)) return prev
          return [...prev, evt]
        })
      }

      ws.onclose = (e) => {
        if (closedRef.current) return
        if (e.code === 4401) {
          setConnState('unauthorized')
          return
        }
        setConnState('reconnecting')
        clearTimeout(reconnectRef.current)
        reconnectRef.current = setTimeout(connect, backoffRef.current)
        backoffRef.current = Math.min(backoffRef.current * 2, 30000)
      }

      ws.onerror = () => {
        try { ws.close() } catch {}
      }
    }

    connect()

    return () => {
      closedRef.current = true
      clearTimeout(reconnectRef.current)
      if (wsRef.current) {
        try { wsRef.current.close() } catch {}
      }
    }
  }, [sessionId])

  const send = useCallback(async (text) => {
    if (!sessionId) return
    await sendMessage(sessionId, text)
  }, [sessionId])

  const cancel = useCallback(async () => {
    if (!sessionId) return
    await cancelTurn(sessionId)
  }, [sessionId])

  return { events, lastSeq, connState, send, cancel }
}
