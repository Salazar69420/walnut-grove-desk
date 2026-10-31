import { useSyncExternalStore } from 'react'

const TZ = 'America/Vancouver'
const dayFmt = new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' })
const exactFmt = new Intl.DateTimeFormat('en-US', { timeZone: TZ, year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
const timeFmt = new Intl.DateTimeFormat('en-US', { timeZone: TZ, hour: 'numeric', minute: '2-digit' })
const mdFmt = new Intl.DateTimeFormat('en-US', { timeZone: TZ, month: 'short', day: 'numeric' })
const mdyFmt = new Intl.DateTimeFormat('en-US', { timeZone: TZ, year: 'numeric', month: 'short', day: 'numeric' })
const wdFmt = new Intl.DateTimeFormat('en-US', { timeZone: TZ, weekday: 'short', month: 'short', day: 'numeric' })

const ampm = (s: string) => s.replace(/[\s ]*(AM|PM)/, (_, x) => ' ' + x.toLowerCase())

/** Calendar day in the clinic time zone, YYYY-MM-DD. */
export function dayKey(iso: string | number | Date): string {
  return dayFmt.format(new Date(iso))
}
function dayNum(key: string) {
  const [y, m, d] = key.split('-').map(Number)
  return Math.round(Date.UTC(y, m - 1, d) / 86400000)
}
export function dayDiff(iso: string, now = Date.now()) {
  return dayNum(dayKey(iso)) - dayNum(dayKey(now))
}
export function exact(iso: string | null | undefined): string {
  if (!iso) return ''
  return ampm(exactFmt.format(new Date(iso)))
}
export function clock(iso: string | null | undefined): string {
  if (!iso) return ''
  return ampm(timeFmt.format(new Date(iso)))
}
export function shortDate(iso: string): string {
  const sameYear = dayKey(iso).slice(0, 4) === dayKey(Date.now()).slice(0, 4)
  return (sameYear ? mdFmt : mdyFmt).format(new Date(iso))
}
export function dayLabel(iso: string): string {
  const d = dayDiff(iso)
  if (d === 0) return 'Today'
  if (d === 1) return 'Tomorrow'
  if (d === -1) return 'Yesterday'
  const s = wdFmt.format(new Date(iso))
  return dayKey(iso).slice(0, 4) === dayKey(Date.now()).slice(0, 4) ? s : s + ', ' + dayKey(iso).slice(0, 4)
}

/** "3 min ago", "in 2 h", "Yesterday", "Oct 5". */
export function rel(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  const diff = now - t
  const abs = Math.abs(diff)
  const fut = diff < 0
  if (abs < 45000) return 'just now'
  const min = Math.round(abs / 60000)
  if (min < 60) return fut ? `in ${min} min` : `${min} min ago`
  const h = Math.round(abs / 3600000)
  if (h < 24 && dayDiff(iso, now) === 0) return fut ? `in ${h} h` : `${h} h ago`
  const dd = dayDiff(iso, now)
  if (dd === -1) return 'Yesterday'
  if (dd === 1) return 'Tomorrow'
  if (dd < 0 && dd > -7) return `${-dd} days ago`
  if (dd > 0 && dd < 7) return `in ${dd} days`
  return shortDate(iso)
}

let tick = Math.floor(Date.now() / 30000)
const tickSubs = new Set<() => void>()
let tickTimer: number | undefined
function subTick(fn: () => void) {
  tickSubs.add(fn)
  if (!tickTimer) tickTimer = window.setInterval(() => { tick = Math.floor(Date.now() / 30000); tickSubs.forEach((f) => f()) }, 30000)
  return () => {
    tickSubs.delete(fn)
    if (!tickSubs.size && tickTimer) { clearInterval(tickTimer); tickTimer = undefined }
  }
}
/** Re-render about every 30 seconds so relative times stay fresh. */
export function useNow(): number {
  useSyncExternalStore(subTick, () => tick)
  return Date.now()
}

export function ageOf(birth: string | null | undefined): string {
  if (!birth) return ''
  const b = new Date(birth + 'T12:00:00')
  if (isNaN(b.getTime())) return ''
  const now = new Date()
  let months = (now.getFullYear() - b.getFullYear()) * 12 + now.getMonth() - b.getMonth()
  if (now.getDate() < b.getDate()) months -= 1
  if (months < 0) return ''
  if (months < 1) return 'under 1 month'
  if (months < 24) return `${months} month${months === 1 ? '' : 's'}`
  const y = Math.floor(months / 12)
  return `${y} years`
}
export function bytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${Math.round(n / 102.4) / 10} KB`
  return `${Math.round(n / 104857.6) / 10} MB`
}
export function firstName(n: string) { return (n || '').split(/\s+/)[0] || n }
export function ls(key: string): string | null { try { return localStorage.getItem(key) } catch { return null } }
export function lsSet(key: string, v: string) { try { localStorage.setItem(key, v) } catch { /* */ } }

/** Date-time local input value (browser time) from an ISO string. */
export function toLocalInput(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}
export function fromLocalInput(v: string): string | null {
  if (!v) return null
  const d = new Date(v)
  return isNaN(d.getTime()) ? null : d.toISOString()
}
export const hueFor = (n: number) => (n * 67 + 23) % 360
