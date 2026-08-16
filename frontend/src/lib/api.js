const TOKEN_KEY = 'otr_token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  constructor(status, body) {
    const err = body?.error || {}
    super(err.message || `HTTP ${status}`)
    this.status = status
    this.code = err.code || (status === 401 ? 'unauthorized' : 'internal')
    this.network = false
  }
}

async function request(method, path, body) {
  let res
  try {
    res = await fetch(path, {
      method,
      headers: {
        Authorization: `Bearer ${getToken()}`,
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    const err = new ApiError(0, { error: { code: 'network', message: 'Server unreachable' } })
    err.network = true
    throw err
  }
  let json = null
  try { json = await res.json() } catch {}
  if (!res.ok) throw new ApiError(res.status, json)
  return json
}

export const checkToken = () => request('GET', '/api/me')
export const listSessions = async () => (await request('GET', '/api/sessions')).sessions
export const createSession = (opts) => request('POST', '/api/sessions', opts)
export const getSession = (id) => request('GET', `/api/sessions/${id}`)
export const sendMessage = (id, text) => request('POST', `/api/sessions/${id}/message`, { text })
export const cancelTurn = (id) => request('POST', `/api/sessions/${id}/cancel`)
export const fetchDiff = (id) => request('GET', `/api/sessions/${id}/files/diff`)

export function streamUrl(id, sinceSeq) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const q = new URLSearchParams({ token: getToken(), since_seq: String(sinceSeq) })
  return `${proto}//${location.host}/api/sessions/${id}/stream?${q}`
}

export function termWsUrl(id) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}/api/sessions/${id}/term?token=${encodeURIComponent(getToken())}`
}
