import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import { useApp } from '../ctx'
import { useLive } from '../live'
import { Empty, KindBadge, Modal, Segmented, Sheet, SkeletonRows, Time } from '../components/ui'
import { Icon } from '../components/icons'
import type { PCall, Row } from '../lib/types'

type Tab = 'log' | 'center'
const RESULTS: [string, string][] = [['answered', 'Answered'], ['booked', 'Booking made'], ['callback_task', 'Callback task raised'], ['voicemail', 'Voicemail'], ['missed', 'Missed'], ['emergency_routed', 'Emergency routed'], ['info_given', 'Information given']]
const mmss = (s: number) => s ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : '-'
const errText = (x: unknown) => x instanceof ApiError && x.message && x.message !== 'error' && x.message !== 'network' ? x.message : ''

export function Handler({ c }: { c: PCall }) {
  if (c.handled_by === 'ai') return <span className="tag ai"><Icon n="sparkle" size={12} /> AI assistant</span>
  if (c.handled_by === 'none') return <span className="tag warn">Nobody</span>
  return <span className="tag">{c.agent_label || 'Staff'}</span>
}

export function Phone() {
  const { can } = useApp()
  const [tab, setTab] = useState<Tab>('log')
  return (
    <div className="php">
      <div className="hd2">
        <div><h1>Phone</h1><p className="lead">Every call, who handled it (a person or the assistant), and how it ended.</p></div>
        {can('phone.export') && tab === 'log' && <a className="exp" href="/api/export/phone.csv" download>Download CSV</a>}
      </div>
      <Segmented<Tab> label="Phone sections" value={tab} onChange={setTab} options={[['log', 'Call log'], ['center', 'Call center']]} />
      {tab === 'log' ? <Log /> : <Center />}
    </div>
  )
}

function Log() {
  const { can } = useApp()
  const [f, setF] = useState({ from: '', to: '', direction: '', handled_by: '', result: '', call_type: '', q: '' })
  const [page, setPage] = useState(1)
  const [open, setOpen] = useState<number | null>(null)
  const qs = new URLSearchParams({ page: String(page), per_page: '25' })
  Object.entries(f).forEach(([k, v]) => { if (v.trim()) qs.set(k, v.trim()) })
  const list = useLive<{ items: PCall[]; total: number; per_page: number }>('/phone/calls?' + qs.toString(), ['phone'])
  const set = (k: keyof typeof f, v: string) => { setF((x) => ({ ...x, [k]: v })); setPage(1) }
  const pages = list.data ? Math.max(1, Math.ceil(list.data.total / list.data.per_page)) : 1
  return (
    <>
      <section className="card pad ph-filters">
        <label className="searchf"><Icon n="search" size={16} /><input value={f.q} onChange={(e) => set('q', e.target.value)} placeholder="Search notes, people, numbers" aria-label="Search calls" maxLength={80} /></label>
        <label>From<input type="date" value={f.from} onChange={(e) => set('from', e.target.value)} /></label>
        <label>To<input type="date" value={f.to} onChange={(e) => set('to', e.target.value)} /></label>
        <select value={f.direction} onChange={(e) => set('direction', e.target.value)} aria-label="Direction"><option value="">In and out</option><option value="inbound">Inbound</option><option value="outbound">Outbound</option></select>
        <select value={f.handled_by} onChange={(e) => set('handled_by', e.target.value)} aria-label="Handled by"><option value="">Anyone</option><option value="human">People</option><option value="ai">AI assistant</option><option value="none">Nobody (missed)</option></select>
        <select value={f.result} onChange={(e) => set('result', e.target.value)} aria-label="Result"><option value="">Any result</option>{RESULTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        <select value={f.call_type} onChange={(e) => set('call_type', e.target.value)} aria-label="Caller type"><option value="">Everyone</option><option value="patient">Patients</option><option value="inquiry">Inquiries</option><option value="unknown">Unknown</option></select>
      </section>
      <section className="card">
        {list.loading && !list.data ? <SkeletonRows n={8} h={52} /> : !list.data?.items.length ? <Empty icon="phone" title="No calls match" text="Try widening the dates or clearing a filter." /> : (
          <div className="tablewrap"><table className="calls">
            <thead><tr><th>When</th><th>Direction</th><th>Contact</th><th>Handled by</th><th>Type</th><th>Length</th><th>Result</th><th>Note</th></tr></thead>
            <tbody>{list.data.items.map((c) => (
              <tr key={c.id} className="clk" tabIndex={0} onClick={() => setOpen(c.id)} onKeyDown={(e) => { if (e.key === 'Enter') setOpen(c.id) }}>
                <td><Time iso={c.started_at} /></td>
                <td><span className={'dir ' + c.direction}>{c.direction === 'inbound' ? 'In' : 'Out'}</span></td>
                <td>{c.contact ? <Link to={`/contacts/${c.contact.id}`} onClick={(e) => e.stopPropagation()}>{c.contact.display_name}</Link> : <span className="mute">{c.phone_display || 'Unknown'}</span>}</td>
                <td><Handler c={c} /></td>
                <td className="mute">{c.call_type === 'unknown' ? '-' : c.call_type === 'patient' ? 'Patient' : 'Inquiry'}</td>
                <td>{mmss(c.duration_seconds)}</td>
                <td><span className={'res r-' + c.result}>{c.result_label}</span></td>
                <td className="ell note-c">{c.short_note}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
        {list.data && list.data.total > 0 && (
          <div className="pager"><span className="mute">{list.data.total} calls</span><span className="spacer" />
            <button className="btn small" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button><span>Page {page} of {pages}</span><button className="btn small" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button></div>
        )}
      </section>
      <CallSheet id={open} onClose={() => setOpen(null)} canTranscript={can('phone.transcript')} canEdit={can('contacts.edit')} onSaved={() => list.reload()} />
    </>
  )
}

function CallSheet({ id, onClose, canTranscript, canEdit, onSaved }: { id: number | null; onClose: () => void; canTranscript: boolean; canEdit: boolean; onSaved: () => void }) {
  const { toast } = useApp()
  const [c, setC] = useState<PCall | null>(null)
  const [note, setNote] = useState('')
  const [cq, setCq] = useState('')
  const [hits, setHits] = useState<Row[]>([])
  const [tr, setTr] = useState<string | null>(null)
  useEffect(() => {
    setC(null); setTr(null); setCq(''); setHits([])
    if (id) api<{ item: PCall }>(`/phone/calls/${id}`, { quiet: true }).then((r) => { setC(r.item); setNote(r.item.short_note) }).catch(() => {})
  }, [id])
  useEffect(() => {
    if (cq.trim().length < 2) { setHits([]); return }
    const t = window.setTimeout(() => api<{ items: Row[] }>('/contacts?per_page=10&q=' + encodeURIComponent(cq.trim()), { quiet: true }).then((r) => setHits(r.items)).catch(() => {}), 250)
    return () => clearTimeout(t)
  }, [cq])
  async function patch(body: Record<string, unknown>, msg: string) {
    if (!c) return
    try { const r = await api<{ item: PCall }>(`/phone/calls/${c.id}`, { method: 'PATCH', body, quiet: true }); setC(r.item); toast(msg); onSaved(); setHits([]); setCq('') }
    catch (x) { toast(errText(x) || 'Not saved.') }
  }
  async function openTranscript() {
    if (!c) return
    try { const r = await api<{ item: PCall }>(`/phone/calls/${c.id}`, { quiet: true }); setTr(r.item.transcript || 'No transcript.') } catch { toast('Could not open the transcript.') }
  }
  return (
    <>
      <Sheet open={!!id} onClose={onClose} title="Call details">
        {!c ? <SkeletonRows n={4} h={40} /> : (
          <div className="evd">
            <p><Handler c={c} /> <span className="tag">{c.direction === 'inbound' ? 'Inbound' : 'Outbound'}</span> <span className="tag">{c.source === 'demo' ? 'Example call' : c.source}</span></p>
            <dl className="dl">
              <dt>When</dt><dd>{new Date(c.started_at).toLocaleString([], { weekday: 'long', month: 'long', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</dd>
              <dt>Length</dt><dd>{mmss(c.duration_seconds)}</dd>
              <dt>Result</dt><dd>{c.result_label}</dd>
              <dt>Contact</dt><dd>{c.contact ? <><Link to={`/contacts/${c.contact.id}`}>{c.contact.display_name}</Link> <KindBadge kind={c.contact.kind} /></> : <span className="mute">Not matched. {c.phone_display}</span>}</dd>
            </dl>
            {canEdit && <>
              <label className="form">Short note<input value={note} onChange={(e) => setNote(e.target.value)} maxLength={240} /></label>
              <div className="rowbtn"><button className="btn small" disabled={note === c.short_note} onClick={() => patch({ short_note: note }, 'Note saved')}>Save note</button></div>
              {!c.contact && <label className="form">Match to a contact<input value={cq} onChange={(e) => setCq(e.target.value)} placeholder="Search by name, phone or pet" /></label>}
              {hits.length > 0 && <div className="opts inline">{hits.map((h) => <button key={h.id} type="button" onClick={() => patch({ contact_id: h.id }, 'Matched')}>{h.display_name}<span className="mute"> {h.pet_names.join(', ')}</span></button>)}</div>}
            </>}
            {c.has_transcript && (canTranscript
              ? <button className="btn" onClick={openTranscript}><Icon n="eye" size={16} /> View transcript</button>
              : <p className="note">A transcript exists. Only the owner can open it, and each time it is opened it is written to the access log.</p>)}
          </div>
        )}
      </Sheet>
      <Modal open={tr !== null} onClose={() => setTr(null)} title="Transcript" footer={<button className="btn" onClick={() => setTr(null)}>Close</button>}>
        <pre className="transcript">{tr}</pre><p className="note">This view was recorded in the access log.</p>
      </Modal>
    </>
  )
}

type Sum = { days: number; total: number; inbound: number; outbound: number; missed: number; ai_handled: number; human_handled: number; ai_share_pct: number; avg_seconds: number; unmatched: number; by_hour: number[]; by_day: { date: string; human: number; ai: number; none: number }[]; by_result: Record<string, number> }

function Center() {
  const [days, setDays] = useState('7')
  const s = useLive<Sum>('/phone/summary?days=' + days, ['phone'])
  const d = s.data
  const maxH = d ? Math.max(1, ...d.by_hour) : 1
  const maxD = d ? Math.max(1, ...d.by_day.map((x) => x.human + x.ai + x.none)) : 1
  return (
    <>
      <section className="card pad"><Segmented<string> label="Range" value={days} onChange={setDays} options={[['7', '7 days'], ['14', '14 days'], ['30', '30 days']]} /></section>
      {!d ? <SkeletonRows n={3} h={80} /> : (
        <>
          <div className="stats">
            <div className="stat"><b>{d.total}</b><span>Calls</span></div>
            <div className="stat"><b>{d.ai_share_pct}%</b><span>Answered by the assistant</span></div>
            <div className="stat"><b>{d.missed}</b><span>Missed inbound</span></div>
            <div className="stat"><b>{mmss(d.avg_seconds)}</b><span>Average call</span></div>
            <div className="stat"><b>{d.unmatched}</b><span>Not matched to a contact</span></div>
          </div>
          <section className="card pad"><h3>Calls by day</h3>
            <div className="bars days" role="img" aria-label="Calls by day, split by people, assistant and missed">
              {d.by_day.map((x) => { const t = x.human + x.ai + x.none; return (
                <div key={x.date} className="bcol" title={`${x.date}: ${x.human} people, ${x.ai} assistant, ${x.none} missed`}>
                  <div className="stack" style={{ height: `${(t / maxD) * 100}%` }}><i className="h" style={{ flex: x.human }} /><i className="a" style={{ flex: x.ai }} /><i className="m" style={{ flex: x.none }} /></div>
                  <small>{new Date(x.date + 'T12:00:00').toLocaleDateString([], { weekday: 'short' })}</small>
                </div>) })}
            </div>
            <p className="legendr"><i className="h" /> People <i className="a" /> Assistant <i className="m" /> Missed</p>
          </section>
          <section className="card pad"><h3>Calls by hour</h3>
            <div className="bars hours" role="img" aria-label="Calls by hour of the day">
              {d.by_hour.map((n, h) => <div key={h} className="bcol" title={`${h}:00, ${n} calls`}><div className="stack" style={{ height: `${(n / maxH) * 100}%` }}><i className="h" style={{ flex: 1 }} /></div><small>{h % 3 === 0 ? h : ''}</small></div>)}
            </div>
          </section>
        </>
      )}
    </>
  )
}
