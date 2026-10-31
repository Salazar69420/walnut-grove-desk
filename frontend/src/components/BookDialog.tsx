import { useEffect, useMemo, useState } from 'react'
import { api, ApiError } from '../api/client'
import { useApp } from '../ctx'
import { Modal } from './ui'
import { firstName } from '../lib/fmt'
import type { Pet } from '../lib/types'

type VT = { id: number; name: string; minutes: number }
type Open = { date: string; minutes: number; hours: string[][]; closed: boolean; times: { start: string; label: string }[]; visit_types: VT[]; week: Record<string, string[][]> }
const KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
const pad = (n: number) => String(n).padStart(2, '0')
const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
const errText = (x: unknown) => x instanceof ApiError && x.message && x.message !== 'error' && x.message !== 'network' ? x.message : ''
const hm = (s: string) => { const [h, m] = s.split(':').map(Number); return `${h % 12 || 12}${m ? ':' + pad(m) : ''} ${h < 12 ? 'am' : 'pm'}` }

/** Book a confirmed appointment straight into the schedule: pick a day, then one of the open times inside the clinic's hours. */
export function BookDialog({ open, onClose, contact, pets }: { open: boolean; onClose: () => void; contact: { id: number; display_name: string; phone: string | null }; pets: Pet[] }) {
  const { toast } = useApp()
  const days = useMemo(() => Array.from({ length: 21 }, (_, i) => { const d = new Date(); d.setHours(12, 0, 0, 0); d.setDate(d.getDate() + i); return d }), [])
  const [day, setDay] = useState(iso(new Date()))
  const [vt, setVt] = useState<number | ''>('')
  const [pet, setPet] = useState<number | ''>('')
  const [data, setData] = useState<Open | null>(null)
  const [pick, setPick] = useState('')
  const [note, setNote] = useState('')
  const [notify, setNotify] = useState(!!contact.phone)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const active = pets.filter((p) => p.status === 'active')
  const minutes = data?.visit_types.find((v) => v.id === vt)?.minutes || 30

  useEffect(() => { if (open) { setPick(''); setErr(''); setNote(''); setPet(active.length === 1 ? active[0].id : '') } }, [open]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!open) return
    let off = false
    setPick('')
    api<Open>(`/calendar/open-times?date=${day}&minutes=${minutes}`, { quiet: true }).then((r) => { if (!off) setData(r) }).catch(() => { if (!off) setErr('Could not load the open times.') })
    return () => { off = true }
  }, [open, day, minutes])

  async function save() {
    if (!pick) return
    setBusy(true); setErr('')
    try {
      const r = await api<{ label: string; texted: string | null }>('/calendar/book', { method: 'POST', quiet: true, body: { contact_id: contact.id, pet_id: pet || null, visit_type_id: vt || null, starts_at: pick, minutes, note, notify } })
      toast(`Booked for ${r.label}. It is in the schedule.` + (r.texted ? (r.texted.startsWith('not sent') ? ` The text was ${r.texted}.` : ' A confirmation text went out.') : ''))
      onClose()
    } catch (x) {
      setErr(errText(x) || 'Not booked. Try again.')
      if (x instanceof ApiError && x.status === 409) api<Open>(`/calendar/open-times?date=${day}&minutes=${minutes}`, { quiet: true }).then(setData).catch(() => {})
    } finally { setBusy(false) }
  }

  const chosen = data?.times.find((t) => t.start === pick)
  return (
    <Modal open={open} onClose={onClose} title={`Book appointment for ${firstName(contact.display_name)}`}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!pick || busy} onClick={save}>{busy ? 'Booking...' : chosen ? `Confirm ${chosen.label}` : 'Pick a time'}</button></>}>
      <div className="bk">
        <div className="bk-row">
          <label className="fld"><span>Visit</span>
            <select value={vt} onChange={(e) => setVt(e.target.value ? Number(e.target.value) : '')}>
              <option value="">Appointment (30 min)</option>
              {data?.visit_types.map((v) => <option key={v.id} value={v.id}>{v.name} ({v.minutes} min)</option>)}
            </select>
          </label>
          <label className="fld"><span>Pet</span>
            <select value={pet} onChange={(e) => setPet(e.target.value ? Number(e.target.value) : '')}>
              <option value="">{active.length ? 'Not set' : 'No pet on file'}</option>
              {active.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
        </div>
        <div className="bk-days" role="listbox" aria-label="Day">
          {days.map((d) => {
            const k = iso(d), hrs = data?.week[KEYS[(d.getDay() + 6) % 7]] || []
            return (
              <button key={k} type="button" role="option" aria-selected={k === day} className={'bk-day' + (k === day ? ' on' : '') + (data && !hrs.length ? ' shut' : '')} onClick={() => setDay(k)}>
                <small>{d.toLocaleDateString([], { weekday: 'short' })}</small><b>{d.getDate()}</b><small>{d.toLocaleDateString([], { month: 'short' })}</small>
              </button>
            )
          })}
        </div>
        <div className="bk-sub">
          <span>{new Date(day + 'T12:00').toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })}</span>
          <span className="mute">{data ? (data.closed ? 'Closed' : 'Open ' + data.hours.map(([a, b]) => `${hm(a)} to ${hm(b)}`).join(', ')) : ''}</span>
          <input type="date" aria-label="Another date" value={day} min={iso(new Date())} onChange={(e) => e.target.value && setDay(e.target.value)} />
        </div>
        <div className="bk-times">
          {!data ? <p className="mute">Loading open times...</p>
            : data.closed ? <p className="mute">The clinic is closed that day.</p>
            : !data.times.length ? <p className="mute">No open times left that day. Try another day.</p>
            : data.times.map((t) => <button key={t.start} type="button" className={'bk-t' + (t.start === pick ? ' on' : '')} aria-pressed={t.start === pick} onClick={() => setPick(t.start)}>{t.label}</button>)}
        </div>
        <label className="fld"><span>Note (optional)</span><input value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} placeholder="For example: second vaccine, nervous with dogs" /></label>
        {contact.phone && <label className="ck"><input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} /> Text {firstName(contact.display_name)} a confirmation</label>}
        {err && <p className="err" role="alert">{err}</p>}
      </div>
    </Modal>
  )
}
