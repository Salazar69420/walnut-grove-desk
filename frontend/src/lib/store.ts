import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import { onEntity } from '../live'
import type { Detail, Staff } from './types'

/* Staff list: loaded once and shared. */
let staffCache: Staff[] | null = null
let staffPromise: Promise<Staff[]> | null = null
export function loadStaff(): Promise<Staff[]> {
  if (staffCache) return Promise.resolve(staffCache)
  if (!staffPromise) {
    staffPromise = api<{ items: Staff[] }>('/staff', { quiet: true })
      .then((r) => { staffCache = r.items; return staffCache })
      .catch(() => { staffPromise = null; return [] as Staff[] })
  }
  return staffPromise
}
export function useStaff(): Staff[] {
  const [s, setS] = useState<Staff[]>(staffCache || [])
  useEffect(() => { let on = true; loadStaff().then((r) => { if (on) setS(r) }); return () => { on = false } }, [])
  return s
}

/* Contact detail: one fetch per open contact, shared by the page and the Guest Card.
   Every fetch is logged by the server, so live refreshes are debounced and skipped right after our own reload. */
type Entry = { data: Detail | null; error: number | null; loading: boolean; subs: Set<() => void>; lastLoad: number; seq: number; inflight: boolean }
const entries = new Map<number, Entry>()
function entry(id: number): Entry {
  let e = entries.get(id)
  if (!e) { e = { data: null, error: null, loading: true, subs: new Set(), lastLoad: 0, seq: 0, inflight: false }; entries.set(id, e) }
  return e
}
async function fetchDetail(id: number) {
  const e = entry(id)
  const my = ++e.seq
  e.inflight = true
  try {
    const d = await api<Detail>(`/contacts/${id}`, { quiet: true })
    if (my === e.seq) { e.data = d; e.error = null }
  } catch (x) {
    if (my === e.seq) e.error = x instanceof ApiError ? x.status : 0
  }
  e.loading = false
  e.inflight = false
  e.lastLoad = Date.now()
  e.subs.forEach((f) => f())
}

export function useContactDetail(id: number) {
  const [, force] = useState(0)
  const timer = useRef<number>()
  useEffect(() => {
    const e = entry(id)
    const f = () => force((n) => n + 1)
    e.subs.add(f)
    if (!e.data && !e.inflight) fetchDetail(id)
    const off = ['contacts', 'tasks', 'events_cal'].map((ent) => onEntity(ent, () => {
      clearTimeout(timer.current)
      timer.current = window.setTimeout(() => { if (Date.now() - entry(id).lastLoad > 1500) fetchDetail(id) }, 500)
    }))
    return () => { e.subs.delete(f); off.forEach((o) => o()); clearTimeout(timer.current); if (!e.subs.size) entries.delete(id) }
  }, [id])
  const e = entry(id)
  const reload = useCallback(() => fetchDetail(id), [id])
  /** Change the cached data right away (optimistic). Returns a function that restores the old value. */
  const patch = useCallback((fn: (d: Detail) => Detail) => {
    const en = entry(id)
    const before = en.data
    if (en.data) { en.data = fn(en.data); en.subs.forEach((f) => f()) }
    return () => { en.data = before; en.subs.forEach((f) => f()) }
  }, [id])
  return { data: e.data, error: e.error, loading: e.loading && !e.data, reload, patch }
}
