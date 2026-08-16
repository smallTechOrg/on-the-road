import { useState, useEffect, useCallback } from 'react'
import * as api from './lib/api'
import TokenScreen from './components/TokenScreen'
import HomeScreen from './components/HomeScreen'
import ChatScreen from './components/ChatScreen'
import TerminalOverlay from './components/TerminalOverlay'
import DiffOverlay from './components/DiffOverlay'

export default function App() {
  const [screen, setScreen] = useState('loading')
  const [session, setSession] = useState(null)
  const [overlay, setOverlay] = useState(null)

  useEffect(() => {
    if (!api.getToken()) { setScreen('token'); return }
    api.checkToken()
      .then(() => setScreen('home'))
      .catch((e) => {
        if (e.network) setScreen('home')
        else { api.clearToken(); setScreen('token') }
      })
  }, [])

  const handleAuth = useCallback(() => setScreen('home'), [])
  const handleLogout = useCallback(() => {
    api.clearToken()
    setSession(null)
    setScreen('token')
  }, [])
  const handleOpenSession = useCallback((s) => {
    setSession(s)
    setScreen('chat')
  }, [])
  const handleBack = useCallback(() => {
    setSession(null)
    setScreen('home')
  }, [])

  if (screen === 'loading') return null

  return (
    <>
      {screen === 'token' && <TokenScreen onAuth={handleAuth} />}
      {screen === 'home' && (
        <HomeScreen onOpenSession={handleOpenSession} onLogout={handleLogout} />
      )}
      {screen === 'chat' && session && (
        <ChatScreen
          session={session}
          onBack={handleBack}
          onOpenTerminal={() => setOverlay('terminal')}
          onOpenDiff={() => setOverlay('diff')}
        />
      )}
      {overlay === 'terminal' && session && (
        <TerminalOverlay sessionId={session.id} onClose={() => setOverlay(null)} />
      )}
      {overlay === 'diff' && session && (
        <DiffOverlay sessionId={session.id} onClose={() => setOverlay(null)} />
      )}
    </>
  )
}
