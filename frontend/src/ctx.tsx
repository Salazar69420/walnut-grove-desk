import { createContext, useCallback, useContext, useEffect, useRef, useState, ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, Me, setCsrf, setHandlers } from './api/client'
import { startLive, stopLive, onEntity } from './live'

type Ctx = {
  me: Me | null
  ready: boolean
  can: (p: string) => boolean
  refreshMe: () => Promise<Me | null>
  setMe: (m: Me | null) => void
  toast: (t: string, undo?: () => void) => void
  toastText: string
  toastShow: boolean
  toastUndo: (() => void) | null
  dismissToast: () => void
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
  const [toastUndo, setToastUndo] = useState<(() => void) | null>(null)
  const [busy, setBusy] = useState<Set<string>>(new Set())
  // Light only: forget the old theme choice (the dark theme was removed)
  useEffect(() => { document.documentElement.removeAttribute('data-theme'); try { localStorage.removeItem('wgd-theme') } catch { /* */ } }, [])

  const toast = useCallback((t: string, undo?: () => void) => {
    setToastText(t); setToastShow(true); setToastUndo(undo ? () => undo : null)
    clearTimeout(th.current)
    th.current = window.setTimeout(() => setToastShow(false), undo ? 7000 : 3200)
  }, [])
  const dismissToast = useCallback(() => { clearTimeout(th.current); setToastShow(false) }, [])

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
    <C.Provider value={{ me, ready, can, refreshMe, setMe, toast, toastText, toastShow, toastUndo, dismissToast, busy, run }}>
      {children}
    </C.Provider>
  )
}
