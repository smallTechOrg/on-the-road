import { useState, useEffect, useCallback } from 'react'
import { getToken, setToken as saveToken, clearToken, checkToken } from '../lib/api'

export function useAuth() {
  const [token, setTokenState] = useState(() => getToken())
  const [checking, setChecking] = useState(true)
  const [isAuthenticated, setIsAuthenticated] = useState(false)

  useEffect(() => {
    if (!token) {
      setChecking(false)
      setIsAuthenticated(false)
      return
    }
    let cancelled = false
    setChecking(true)
    checkToken()
      .then(() => { if (!cancelled) setIsAuthenticated(true) })
      .catch(() => {
        if (!cancelled) {
          clearToken()
          setTokenState('')
          setIsAuthenticated(false)
        }
      })
      .finally(() => { if (!cancelled) setChecking(false) })
    return () => { cancelled = true }
  }, [token])

  const login = useCallback(async (newToken) => {
    saveToken(newToken)
    setTokenState(newToken)
  }, [])

  const logout = useCallback(() => {
    clearToken()
    setTokenState('')
    setIsAuthenticated(false)
  }, [])

  return { token, isAuthenticated, checking, login, logout }
}
