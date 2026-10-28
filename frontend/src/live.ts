import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api/client'

type Fn = () => void
const subs = new Map<string, Set<Fn>>()
let es: EventSource | null = null
let pollTimer: number | undefined
let lastId = 0
let started = false

function fire(entity: string) {
  subs.get(entity)?.forEach((f) => f())
  subs.get('*')?.forEach((f) => f())
}

async function poll() {
  try {
    const r = await api<{ items: any[]; last_id: number }>('/events?since=' + lastId + '&limit=50', { quiet: true })
    if (r.last_id > lastId) {
      const first = lastId === 0
      lastId = r.last_id
      if (!first) ['callbacks', 'results', 'drafts', 'shifts', 'suggestions', 'overnight', 'events', 'settings', 'hospitals', 'visit_types'].forEach(fire)
    }
  } catch { /* ignore */ }
}

function startPolling() {
  if (pollTimer) return
  pollTimer = window.setInterval(poll, 5000)
  poll()
}

/** Run fn whenever the server reports a change to this entity. Returns an unsubscribe function. */
export function onEntity(entity: string, fn: Fn): () => void {
  if (!subs.has(entity)) subs.set(entity, new Set())
  subs.get(entity)!.add(fn)
  return () => { subs.get(entity)?.delete(fn) }
}

export function startLive() {
  if (started) return
  started = true
  open()
}
function open() {
  try {
    es = new EventSource('/api/stream')
  } catch { startPolling(); return }
  es.addEventListener('change', (ev) => {
    try {
      const d = JSON.parse((ev as MessageEvent).data)
      if (d.event_id && d.event_id > lastId) lastId = d.event_id
      fire(d.entity)
    } catch { /* ignore */ }
  })
  es.onopen = () => {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = undefined }
  }
  es.onerror = () => {
    startPolling()
  }
}
export function stopLive() {
  es?.close(); es = null
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = undefined; started = false
}

/** Fetch data for a path and refetch when any of the entities change. */
export function useLive<T>(path: string | null, entities: string[], enabled = true) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const pathRef = useRef(path)
  pathRef.current = path
  const load = useCallback(async () => {
    const p = pathRef.current
    if (!p || !enabled) { setLoading(false); return }
    try {
      const r = await api<T>(p, { quiet: true })
      if (pathRef.current === p) { setData(r); setError(false) }
    } catch { setError(true) }
    setLoading(false)
  }, [enabled])
  useEffect(() => { setLoading(true); load() }, [path, load])
  useEffect(() => {
    const set = new Set(entities)
    const fn = () => load()
    set.forEach((e) => { if (!subs.has(e)) subs.set(e, new Set()); subs.get(e)!.add(fn) })
    return () => { set.forEach((e) => subs.get(e)?.delete(fn)) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entities.join(','), load])
  return { data, loading, error, reload: load, setData }
}
