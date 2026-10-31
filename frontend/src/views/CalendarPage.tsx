import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import { useApp } from '../ctx'
import { useLive } from '../live'
import { Empty, Segmented, Sheet } from '../components/ui'
import { Icon } from '../components/icons'
import { useStaff } from '../lib/store'
import { fromLocalInput, ls, lsSet, toLocalInput } from '../lib/fmt'
import { EVENT_HUE, EVENT_KINDS, EVENT_LABEL, type Ev, type Row } from '../lib/types'

type View = 'week' | 'day' | 'month'
const H0 = 7, H1 = 20, ROW = 52 // grid runs 7 am to 8 pm
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const pad = (n: number) => String(n).padStart(2, '0')
const ymd = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
const addDays = (d: Date, n: number) => { const x = new Date(d); x.setDate(x.getDate() + n); return x }
const startOfWeek = (d: Date) => { const x = new Date(d.getFullYear(), d.getMonth(), d.getDate()); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x }
const hm = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
const errText = (x: unknown) => x instanceof ApiError && x.message && x.message !== 'error' && x.message !== 'network' ? x.message : ''

/** Lay out overlapping events side by side: returns [event, column, columns] */
function layout(evs: Ev[]): [Ev, number, number][] {
  const sorted = [...evs].sort((a, b) => a.starts_at.localeCompare(b.starts_at))
  const out: [Ev, number, number][] = []
  let group: { e: Ev; col: number }[] = []
  let groupEnd = 0
  const flush = () => { const n = Math.max(1, ...group.map((g) => g.col + 1)); group.forEach((g) => out.push([g.e, g.col, n])); group = [] }
  for (const e of sorted) {
    const s = +new Date(e.starts_at), en = +new Date(e.ends_at || e.starts_at) || s + 1800000
    if (group.length && s >= groupEnd) { flush(); groupEnd = 0 }
    const used = new Set(group.filter((g) => +new Date(g.e.ends_at || g.e.starts_at) > s).map((g) => g.col))
    let col = 0
    while (used.has(col)) col++
    group.push({ e, col })
    groupEnd = Math.max(groupEnd, en)
  }
  flush()
  return out
}

export function CalendarPage() {
  const { can, toast } = useApp()
  const staff = useStaff()
  const [sp, setSp] = useSearchParams()
  const view = (sp.get('view') as View) || (ls('wgd.cal.view') as View) || 'week'
  const anchor = sp.get('date') ? new Date(sp.get('date') + 'T12:00:00') : new Date()
  const [kinds, setKinds] = useState<Set<string>>(() => new Set(EVENT_KINDS.map(([k]) => k)))
  const [cls, setCls] = useState<'all' | 'patient' | 'inquiry'>('all')
  const [who, setWho] = useState('')
  const [q, setQ] = useState('')
  const [showCancelled, setShowCancelled] = useState(false)
  const [open, setOpen] = useState<Ev | null>(null)
  const [creating, setCreating] = useState<Date | null>(null)

  const range = useMemo(() => {
    if (view === 'day') return [new Date(anchor.getFullYear(), anchor.getMonth(), anchor.getDate()), addDays(anchor, 0)] as const
    if (view === 'week') { const s = startOfWeek(anchor); return [s, addDays(s, 6)] as const }
    const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1)
    const s = startOfWeek(first)
    return [s, addDays(s, 41)] as const
  }, [view, sp.get('date')]) // eslint-disable-line react-hooks/exhaustive-deps
  const list = useLive<{ items: Ev[] }>(`/calendar/events?from=${ymd(range[0])}&to=${ymd(range[1])}`, ['events_cal'])
  const all = list.data?.items || []
  const ql = q.trim().toLowerCase()
  const events = all.filter((e) =>
    kinds.has(e.kind) && (cls === 'all' || e.classification === cls) && (!who || String(e.staff?.id) === who) && (showCancelled || e.status !== 'cancelled') &&
    (!ql || [e.title, e.contact?.display_name, e.pet?.name].some((t) => (t || '').toLowerCase().includes(ql))))

  const go = useCallback((d: Date, v?: View) => setSp((p) => { const n = new URLSearchParams(p); n.set('date', ymd(d)); if (v) { n.set('view', v); lsSet('wgd.cal.view', v) } return n }, { replace: true }), [setSp])
  const step = (dir: number) => go(view === 'month' ? new Date(anchor.getFullYear(), anchor.getMonth() + dir, 1) : addDays(anchor, dir * (view === 'week' ? 7 : 1)))
  const title = view === 'month' ? anchor.toLocaleDateString([], { month: 'long', year: 'numeric' })
    : view === 'day' ? anchor.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })
    : `${range[0].toLocaleDateString([], { month: 'short', day: 'numeric' })} to ${range[1].toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' })}`
  const todayStr = ymd(new Date())
  const toggle = (k: string) => setKinds((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n })
  const byDay = (d: Date) => events.filter((e) => ymd(new Date(e.starts_at)) === ymd(d))
  const days = view === 'day' ? [range[0]] : Array.from({ length: 7 }, (_, i) => addDays(range[0], i))

  return (
    <div className="calp">
      <div className="hd2">
        <div><h1>Calendar</h1><p className="lead">Appointments, surgeries and calls. Imported ImproMed appointments are read only.</p></div>
        {can('calendar.edit') && <button className="btn primary" onClick={() => setCreating(new Date())}><Icon n="plus" size={16} /> Create</button>}
      </div>
      <section className="card cal-bar">
        <div className="cal-nav">
          <button className="btn small" onClick={() => go(new Date())}>Today</button>
          <button className="ibtn sm" aria-label="Previous" onClick={() => step(-1)}><Icon n="chevL" /></button>
          <button className="ibtn sm" aria-label="Next" onClick={() => step(1)}><Icon n="chevR" /></button>
          <h2 className="cal-title">{title}</h2>
          <span className="spacer" />
          <Segmented<View> label="View" value={view} onChange={(v) => go(anchor, v)} options={[['day', 'Day'], ['week', 'Week'], ['month', 'Month']]} />
        </div>
        <div className="cal-filters">
          <label className="searchf"><Icon n="search" size={16} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search events, clients, pets" aria-label="Search events" maxLength={80} /></label>
          <Segmented<'all' | 'patient' | 'inquiry'> label="Patients or inquiries" value={cls} onChange={setCls} options={[['all', 'All'], ['patient', 'Patients'], ['inquiry', 'Inquiries']]} />
          <select value={who} onChange={(e) => setWho(e.target.value)} aria-label="Staff"><option value="">Everyone</option>{staff.map((s) => <option key={s.id} value={s.id}>{s.display_name}</option>)}</select>
          <label className="ck"><input type="checkbox" checked={showCancelled} onChange={(e) => setShowCancelled(e.target.checked)} /> Show cancelled</label>
        </div>
        <div className="cal-legend" role="group" aria-label="Event types">
          {EVENT_KINDS.map(([k, l, h]) => (
            <button key={k} type="button" className={'lg' + (kinds.has(k) ? ' on' : '')} aria-pressed={kinds.has(k)} onClick={() => toggle(k)} style={{ ['--h' as any]: h }}><i />{l}</button>
          ))}
          <button type="button" className="link-a" onClick={() => setKinds(new Set(EVENT_KINDS.map(([k]) => k)))}>All</button>
          <button type="button" className="link-a" onClick={() => setKinds(new Set())}>None</button>
        </div>
      </section>

      <BookingHours />
      <section className="card cal-main">
        {list.error && !list.data ? <p className="sub pad">Could not load the calendar. It will try again.</p> : null}
        {view === 'month' ? (
          <div className="cal-month" role="grid" aria-label={title}>
            {DAYS.map((d) => <div key={d} className="cm-h" role="columnheader">{d}</div>)}
            {Array.from({ length: 42 }, (_, i) => addDays(range[0], i)).map((d) => {
              const es = byDay(d)
              return (
                <div key={ymd(d)} role="gridcell" className={'cm-c' + (d.getMonth() !== anchor.getMonth() ? ' out' : '') + (ymd(d) === todayStr ? ' today' : '')}>
                  <button className="cm-d" onClick={() => go(d, 'day')} aria-label={d.toDateString()}>{d.getDate()}</button>
                  {es.slice(0, 3).map((e) => <button key={e.id} className={'chip' + (e.status === 'cancelled' ? ' x' : '')} style={{ ['--h' as any]: EVENT_HUE[e.kind] ?? 190 }} onClick={() => setOpen(e)}>{hm(e.starts_at)} {e.title}</button>)}
                  {es.length > 3 && <button className="more" onClick={() => go(d, 'day')}>+{es.length - 3} more</button>}
                </div>
              )
            })}
          </div>
        ) : (
          <div className="cal-grid" style={{ ['--cols' as any]: days.length, ['--row' as any]: ROW + 'px' }}>
            <div className="cg-head"><span />{days.map((d) => (
              <button key={ymd(d)} className={'cg-day' + (ymd(d) === todayStr ? ' today' : '')} onClick={() => go(d, 'day')}><span>{DAYS[(d.getDay() + 6) % 7]}</span><b>{d.getDate()}</b></button>
            ))}</div>
            <div className="cg-body" style={{ height: (H1 - H0) * ROW }}>
              <div className="cg-hours">{Array.from({ length: H1 - H0 }, (_, i) => <span key={i} style={{ top: i * ROW }}>{new Date(2000, 0, 1, H0 + i).toLocaleTimeString([], { hour: 'numeric' })}</span>)}</div>
              {days.map((d) => (
                <div key={ymd(d)} className={'cg-col' + (ymd(d) === todayStr ? ' today' : '')} onDoubleClick={(e) => {
                  if (!can('calendar.edit')) return
                  const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
                  const mins = Math.round(((e.clientY - r.top) / ROW) * 4) * 15
                  setCreating(new Date(d.getFullYear(), d.getMonth(), d.getDate(), H0, mins))
                }}>
                  {layout(byDay(d)).map(([e, col, n]) => {
                    const s = new Date(e.starts_at), en = new Date(e.ends_at || e.starts_at)
                    const top = Math.max(0, ((s.getHours() + s.getMinutes() / 60) - H0) * ROW)
                    const h = Math.max(24, ((+en - +s) / 3600000) * ROW - 2)
                    if (s.getHours() < H0 || s.getHours() >= H1) return null
                    return (
                      <button key={e.id} className={'ev' + (e.status === 'cancelled' ? ' x' : '') + (e.status === 'done' ? ' done' : '') + (e.status === 'requested' ? ' req' : '')}
                        style={{ top, height: h, left: `calc(${(col / n) * 100}% + 2px)`, width: `calc(${100 / n}% - 4px)`, ['--h' as any]: EVENT_HUE[e.kind] ?? 190 }} onClick={() => setOpen(e)}>
                        <b className="ell">{e.title}</b><span className="ell">{hm(e.starts_at)}{e.contact ? ' · ' + e.contact.display_name : ''}</span>
                      </button>
                    )
                  })}
                </div>
              ))}
            </div>
          </div>
        )}
        {list.data && events.length === 0 && <Empty icon="cal" title="Nothing here" text={all.length ? 'No events match these filters.' : 'No events in this range.'} />}
      </section>

      <EventSheet ev={open} onClose={() => setOpen(null)} onChanged={() => { list.reload(); setOpen(null) }} canEdit={can('calendar.edit')} toast={toast} />
      <CreateSheet start={creating} onClose={() => setCreating(null)} onSaved={() => { setCreating(null); list.reload(); toast('Event added') }} staff={staff} />
    </div>
  )
}

function EventSheet({ ev, onClose, onChanged, canEdit, toast }: { ev: Ev | null; onClose: () => void; onChanged: () => void; canEdit: boolean; toast: (m: string) => void }) {
  async function setStatus(status: string) {
    if (!ev) return
    try { await api(`/calendar/events/${ev.id}`, { method: 'PATCH', body: { status }, quiet: true }); toast(status === 'cancelled' ? 'Event cancelled' : status === 'done' ? 'Marked done' : 'Event updated'); onChanged() }
    catch (x) { toast(errText(x) || 'Could not change that event.') }
  }
  return (
    <Sheet open={!!ev} onClose={onClose} title={ev?.title || ''}>
      {ev && (
        <div className="evd">
          <p><span className="kb" style={{ ['--h' as any]: EVENT_HUE[ev.kind] ?? 190 }}>{EVENT_LABEL[ev.kind] || ev.kind}</span> <span className="tag">{ev.status}</span> <span className="tag">{ev.source === 'import' ? 'From ImproMed' : 'Added here'}</span></p>
          <dl className="dl">
            <dt>When</dt><dd>{new Date(ev.starts_at).toLocaleString([], { weekday: 'long', month: 'long', day: 'numeric', hour: 'numeric', minute: '2-digit' })}{ev.ends_at ? ' to ' + hm(ev.ends_at) : ''}</dd>
            {ev.contact && <><dt>Client</dt><dd><Link to={`/contacts/${ev.contact.id}`}>{ev.contact.display_name}</Link></dd></>}
            {ev.pet && <><dt>Pet</dt><dd>{ev.pet.name}</dd></>}
            {ev.staff && <><dt>With</dt><dd>{ev.staff.display_name}</dd></>}
          </dl>
          {ev.source === 'import' && <p className="note">Imported appointments are read only. Change them in ImproMed.</p>}
          {canEdit && ev.status === 'requested' && <ConfirmBox ev={ev} onDone={onChanged} toast={toast} />}
          {canEdit && ev.source === 'app' && (
            <div className="rowbtn">
              {ev.status !== 'done' && ev.status !== 'cancelled' && <button className="btn okb" onClick={() => setStatus('done')}>Mark done</button>}
              {ev.status !== 'cancelled' && <button className="btn danger" onClick={() => setStatus('cancelled')}>Cancel event</button>}
              {ev.status === 'cancelled' && <button className="btn" onClick={() => setStatus('booked')}>Reopen</button>}
            </div>
          )}
        </div>
      )}
    </Sheet>
  )
}

function CreateSheet({ start, onClose, onSaved, staff }: { start: Date | null; onClose: () => void; onSaved: () => void; staff: { id: number; display_name: string }[] }) {
  const [kind, setKind] = useState('checkup')
  const [title, setTitle] = useState('')
  const [when, setWhen] = useState('')
  const [mins, setMins] = useState(30)
  const [staffId, setStaffId] = useState('')
  const [cq, setCq] = useState('')
  const [hits, setHits] = useState<Row[]>([])
  const [contact, setContact] = useState<Row | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => { if (start) { const d = new Date(start); d.setSeconds(0, 0); setWhen(toLocalInput(d)); setTitle(''); setKind('checkup'); setContact(null); setCq(''); setErr('') } }, [start])
  useEffect(() => {
    if (cq.trim().length < 2) { setHits([]); return }
    const t = window.setTimeout(() => api<{ items: Row[] }>('/contacts?per_page=10&q=' + encodeURIComponent(cq.trim()), { quiet: true }).then((r) => setHits(r.items)).catch(() => {}), 250)
    return () => clearTimeout(t)
  }, [cq])
  async function save() {
    const iso = fromLocalInput(when)
    if (!iso || !title.trim()) { setErr('Add a title and a start time.'); return }
    setBusy(true); setErr('')
    const end = new Date(new Date(iso).getTime() + mins * 60000).toISOString()
    try {
      await api('/calendar/events', { method: 'POST', body: { kind, title: title.trim(), starts_at: iso, ends_at: end, contact_id: contact?.id ?? null, staff_user_id: staffId ? Number(staffId) : null, classification: contact ? contact.kind : kind === 'staff' || kind === 'reminder' ? 'none' : 'inquiry' }, quiet: true })
      onSaved()
    } catch (x) { setErr(errText(x) || 'Could not save the event.') }
    setBusy(false)
  }
  return (
    <Sheet open={!!start} onClose={onClose} title="Create event" footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={busy} onClick={save}>Save event</button></>}>
      <div className="form">
        <label>Kind<select value={kind} onChange={(e) => setKind(e.target.value)}>{EVENT_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        <label>Title<input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="For example: Annual exam" data-autofocus /></label>
        <label>Starts<input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} /></label>
        <label>Length<select value={mins} onChange={(e) => setMins(Number(e.target.value))}>{[15, 30, 45, 60, 90, 120, 180].map((m) => <option key={m} value={m}>{m} minutes</option>)}</select></label>
        <label>With<select value={staffId} onChange={(e) => setStaffId(e.target.value)}><option value="">Nobody in particular</option>{staff.map((s) => <option key={s.id} value={s.id}>{s.display_name}</option>)}</select></label>
        <label>Client (optional)
          {contact ? <span className="pick">{contact.display_name} <button type="button" className="link-a" onClick={() => setContact(null)}>Change</button></span>
            : <input value={cq} onChange={(e) => setCq(e.target.value)} placeholder="Search by name, phone or pet" />}
        </label>
        {!contact && hits.length > 0 && <div className="opts inline">{hits.map((h) => <button key={h.id} type="button" onClick={() => { setContact(h); setHits([]) }}>{h.display_name}<span className="mute"> {h.pet_names.join(', ')}</span></button>)}</div>}
        {err && <p className="errt" role="alert">{err}</p>}
        <p className="note">This adds the event here only. Enter real bookings in ImproMed too.</p>
      </div>
    </Sheet>
  )
}


function ConfirmBox({ ev, onDone, toast }: { ev: Ev; onDone: () => void; toast: (m: string) => void }) {
  const [notify, setNotify] = useState(true)
  async function go() {
    try {
      const r = await api<{ texted: string | null }>(`/calendar/events/${ev.id}/confirm`, { method: 'POST', body: { notify }, quiet: true })
      toast(r.texted && r.texted.startsWith('not sent') ? `Confirmed. The text was ${r.texted}` : notify && ev.contact ? 'Confirmed and the client was texted' : 'Confirmed')
      onDone()
    } catch (x) { toast(errText(x) || 'Not confirmed.') }
  }
  return (
    <div className="pvw">
      <p><b>Requested by text.</b> Check the time works, confirm it here, and enter it in ImproMed.</p>
      {ev.contact && <label className="ck"><input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} /> Text {ev.contact.display_name} that it is confirmed</label>}
      <div className="rowbtn"><button className="btn primary" onClick={go}>Confirm booking</button></div>
    </div>
  )
}

type Hours = { enabled: boolean; slot_minutes: number; lead_minutes: number; horizon_days: number; closed_dates: string[]; days: Record<string, string[][]>; preview: { label: string }[] }
const DAYKEYS: [string, string][] = [['mon', 'Monday'], ['tue', 'Tuesday'], ['wed', 'Wednesday'], ['thu', 'Thursday'], ['fri', 'Friday'], ['sat', 'Saturday'], ['sun', 'Sunday']]

/** The times the assistant may offer by text. Times already in the Calendar are skipped automatically. */
function BookingHours() {
  const { can, toast } = useApp()
  const edit = can('afterhours.edit')
  const [h, setH] = useState<Hours | null>(null)
  const [closed, setClosed] = useState('')
  const load = () => api<Hours>('/calendar/booking-hours', { quiet: true }).then((r) => { setH(r); setClosed(r.closed_dates.join(', ')) }).catch(() => {})
  useEffect(() => { load() }, [])
  if (!h) return null
  const setDay = (k: string, i: 0 | 1, v: string) => setH({ ...h, days: { ...h.days, [k]: [[...(h.days[k]?.[0] || ['09:00', '17:00'])].map((x, j) => (j === i ? v : x))] } })
  async function save() {
    try {
      const r = await api<Hours>('/calendar/booking-hours', { method: 'PUT', quiet: true, body: { enabled: h!.enabled, slot_minutes: h!.slot_minutes, lead_minutes: h!.lead_minutes, horizon_days: h!.horizon_days, days: h!.days, closed_dates: closed.split(',').map((x) => x.trim()).filter(Boolean) } })
      setH(r); toast('Bookable hours saved')
    } catch (x) { toast(errText(x) || 'Not saved.') }
  }
  return (
    <details className="card pad bookhours">
      <summary><b>Booking by text</b> <span className={'tag ' + (h.enabled ? 'ok' : '')}>{h.enabled ? 'On' : 'Off'}</span> <span className="mute small">The times the assistant may offer. Taken times in this calendar are skipped. Staff confirm every request.</span></summary>
      <fieldset disabled={!edit} className="bh">
        <label className="ck"><input type="checkbox" checked={h.enabled} onChange={(e) => setH({ ...h, enabled: e.target.checked })} /> Let the assistant offer times by text</label>
        {DAYKEYS.map(([k, l]) => {
          const r = h.days[k]?.[0]
          return (
            <div key={k} className="bh-row"><b>{l}</b>
              <label className="ck"><input type="checkbox" checked={!r} onChange={(e) => setH({ ...h, days: { ...h.days, [k]: e.target.checked ? [] : [['09:00', '17:00']] } })} /> Closed</label>
              {r && <><input type="time" value={r[0]} onChange={(e) => setDay(k, 0, e.target.value)} aria-label={`${l} first time`} /> to <input type="time" value={r[1]} onChange={(e) => setDay(k, 1, e.target.value)} aria-label={`${l} last end time`} /></>}
            </div>
          )
        })}
        <div className="bh-row"><b>Appointment every</b><select value={h.slot_minutes} onChange={(e) => setH({ ...h, slot_minutes: Number(e.target.value) })}>{[15, 20, 30, 45, 60].map((m) => <option key={m} value={m}>{m} minutes</option>)}</select></div>
        <div className="bh-row"><b>Not sooner than</b><select value={h.lead_minutes} onChange={(e) => setH({ ...h, lead_minutes: Number(e.target.value) })}>{[0, 60, 120, 240, 1440].map((m) => <option key={m} value={m}>{m === 0 ? 'any time' : m < 1440 ? `${m / 60} hour${m === 60 ? '' : 's'} ahead` : 'the next day'}</option>)}</select></div>
        <div className="bh-row"><b>Up to</b><select value={h.horizon_days} onChange={(e) => setH({ ...h, horizon_days: Number(e.target.value) })}>{[7, 14, 21, 30].map((d) => <option key={d} value={d}>{d} days ahead</option>)}</select></div>
        <label className="bh-row"><b>Closed dates</b><input value={closed} onChange={(e) => setClosed(e.target.value)} placeholder="2026-12-25, 2026-12-26" /></label>
        {edit && <div className="rowbtn"><button className="btn primary" onClick={save}>Save bookable hours</button></div>}
      </fieldset>
      <p className="small mute">Next open times: {h.preview.length ? h.preview.map((p) => p.label).join(' · ') : 'none'}</p>
    </details>
  )
}
