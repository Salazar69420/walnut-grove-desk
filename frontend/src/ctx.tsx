import { createContext, useCallback, useContext, useEffect, useRef, useState, ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, Me, setCsrf, setHandlers } from './api/client'
import { startLive, stopLive, onEntity } from './live'

type Theme = 'light' | 'dark' | 'auto'
type Ctx = {
  me: Me | null
  ready: boolean
  can: (p: string) => boolean
  refreshMe: () => Promise<Me | null>
  setMe: (m: Me | null) => void
  toast: (t: string) => void
  toastText: string
  toastShow: boolean
  theme: Theme
  setTheme: (t: Theme) => void
  busy: Set<string>
  run: <T>(key: string, fn: () => Promise<T>) => Promise<T | undefined>
}
const C = createContext<Ctx>(null as any)
export const useApp = () => useContext(C)

export function Provider({ children }: { children: ReactNode }) {
  const nav = useNavigate()
  const [me, setMeState] = useState<Me | null>(null)
  const [ready, setReady] = useState(false)
  const [toastText, setToastText] = useState('')
  const [toastShow, setToastShow] = useState(false)
  const th = useRef<number>()
  const [busy, setBusy] = useState<Set<string>>(new Set())
  const [theme, setThemeState] = useState<Theme>(() => {
    try { const t = localStorage.getItem('wgd-theme'); if (t === 'light' || t === 'dark' || t === 'auto') return t } catch { /* */ }
    return 'auto'
  })

  useEffect(() => {
    const el = document.documentElement
    if (theme === 'auto') el.removeAttribute('data-theme'); else el.setAttribute('data-theme', theme)
  }, [theme])
  const setTheme = (t: Theme) => { setThemeState(t); try { localStorage.setItem('wgd-theme', t) } catch { /* */ } }

  const toast = useCallback((t: string) => {
    setToastText(t); setToastShow(true)
    clearTimeout(th.current)
    th.current = window.setTimeout(() => setToastShow(false), 2800)
  }, [])

  const setMe = useCallback((m: Me | null) => {
    setMeState(m)
    if (m) setCsrf(m.csrf_token)
    if (!m) stopLive()
  }, [])

  const refreshMe = useCallback(async () => {
    try {
      const m = await api<Me>('/auth/me', { quiet: true })
      setMe(m)
      return m
    } catch {
      setMe(null)
      return null
    }
  }, [setMe])

  useEffect(() => {
    setHandlers({
      onToast: toast,
      onAuth: () => { setMe(null); nav('/login', { replace: true }) },
      onChangePw: () => nav('/change-password', { replace: true }),
    })
  }, [toast, nav, setMe])

  useEffect(() => { refreshMe().finally(() => setReady(true)) }, [refreshMe])
  // clinic name and the prototype banner come with /auth/me, so reload it when any admin changes settings
  useEffect(() => onEntity('settings', () => { refreshMe() }), [refreshMe])
  useEffect(() => { if (me && !me.user.must_change_password) startLive() }, [me])

  const can = useCallback((p: string) => !!me && me.perms.includes(p), [me])

  const run = useCallback(async <T,>(key: string, fn: () => Promise<T>) => {
    setBusy((b) => new Set(b).add(key))
    try { return await fn() } catch { return undefined } finally {
      setBusy((b) => { const n = new Set(b); n.delete(key); return n })
    }
  }, [])

  return (
    <C.Provider value={{ me, ready, can, refreshMe, setMe, toast, toastText, toastShow, theme, setTheme, busy, run }}>
      {children}
    </C.Provider>
  )
}
