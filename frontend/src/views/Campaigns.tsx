import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import { useApp } from '../ctx'
import { useLive } from '../live'
import { Empty, Modal, Segmented, SkeletonRows, Time } from '../components/ui'
import { Icon } from '../components/icons'
import type { Camp, Preview } from '../lib/types'

const errText = (x: unknown) => x instanceof ApiError && x.message && x.message !== 'error' && x.message !== 'network' ? x.message : ''
const EMPTY = { kind: '', species: '', email_consent: '', sms_consent: '', visits_min: '', visits_max: '', last_visit_from: '', last_visit_to: '', source: '', has_open_task: '', ai: '', q: '' }
type Seg = typeof EMPTY
const toSegment = (s: Seg) => {
  const o: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(s)) {
    if (v === '') continue
    o[k] = k === 'visits_min' || k === 'visits_max' ? Number(v) : k === 'has_open_task' ? v === 'yes' : v
  }
  return o
}
const fromSegment = (o: Record<string, any>): Seg => {
  const s: Seg = { ...EMPTY }
  for (const k of Object.keys(EMPTY) as (keyof Seg)[]) if (o[k] !== undefined && o[k] !== null) s[k] = k === 'has_open_task' ? (o[k] ? 'yes' : 'no') : String(o[k])
  return s
}

export function Campaigns() {
  const { can } = useApp()
  const list = useLive<{ items: Camp[]; quiet_hours: boolean; merge_fields: string[] }>('/campaigns', ['campaigns'])
  const [building, setBuilding] = useState<Camp | 'new' | null>(null)
  const items = list.data?.items || []
  return (
    <div className="cmp">
      <div className="hd2">
        <div><h1>Campaigns</h1><p className="lead">Send one message to many people. Every campaign is checked against consent, needs the owner's approval, and runs in practice mode: nothing is really sent.</p></div>
        {can('campaigns.edit') && <button className="btn primary" onClick={() => setBuilding('new')}><Icon n="plus" size={16} /> Create campaign</button>}
      </div>
      <div className="practice" role="note"><Icon n="alert" size={16} /> Practice mode. Messages are written to the outbox as "would send". No text or email leaves the server.</div>
      {list.data?.quiet_hours && <p className="note">Quiet hours (9 pm to 8 am): campaigns cannot be run right now.</p>}
      {building && <Builder start={building === 'new' ? null : building} mergeFields={list.data?.merge_fields || []} onClose={() => setBuilding(null)} onSaved={() => { setBuilding(null); list.reload() }} />}
      <section className="cmp-list">
        {list.loading && !list.data ? <SkeletonRows n={3} h={90} /> : items.length === 0 ? <Empty icon="mail" title="No campaigns yet" text="Create one to reach a group of clients, for example a vaccine reminder." /> : items.map((c) => <CampCard key={c.id} c={c} onEdit={() => setBuilding(c)} onChanged={() => list.reload()} quiet={!!list.data?.quiet_hours} />)}
      </section>
    </div>
  )
}

function CampCard({ c, onEdit, onChanged, quiet }: { c: Camp; onEdit: () => void; onChanged: () => void; quiet: boolean }) {
  const { can, toast } = useApp()
  const [more, setMore] = useState<Camp | null>(null)
  const [confirm, setConfirm] = useState(false)
  async function post(path: string, msg: string) {
    try { await api(`/campaigns/${c.id}/${path}`, { method: 'POST', body: {}, quiet: true }); toast(msg); onChanged(); return true } catch (x) { toast(errText(x) || 'That did not work.'); onChanged(); return false }
  }
  async function details() { try { setMore((await api<{ item: Camp }>(`/campaigns/${c.id}`, { quiet: true })).item) } catch { toast('Could not open that.') } }
  const k = c.counts
  const skipped = k.skipped ? Object.values(k.skipped).reduce((a, b) => a + b, 0) : 0
  return (
    <article className="card cmp-card">
      <header><div><h3>{c.name}</h3><p className="mute small">{c.channel === 'sms' ? 'Text message' : 'Email'} · {c.purpose === 'promo' ? 'Promotion (full consent only)' : 'Care message'} · by {c.created_by || 'someone'} <Time iso={c.created_at} /></p></div>
        <span className={'cst s-' + c.status}>{c.status_label}</span></header>
      <p className="cmp-body">{c.body}</p>
      {k.matched !== undefined && <p className="small"><b>{k.sent}</b> would be sent, <b>{skipped}</b> left out, of {k.matched} matched. {c.ran_at && <span className="mute">Ran <Time iso={c.ran_at} /></span>}</p>}
      <footer>
        <button className="btn small ghost" onClick={details}>Details</button><span className="spacer" />
        {(c.status === 'draft' || c.status === 'awaiting_approval') && can('campaigns.edit') && <button className="btn small" onClick={onEdit}>Edit</button>}
        {c.status === 'draft' && can('campaigns.edit') && <button className="btn small primary" onClick={() => post('submit', 'Sent for approval')}>Send for approval</button>}
        {c.status === 'awaiting_approval' && can('campaigns.approve') && <button className="btn small okb" onClick={() => post('approve', 'Approved')}>Approve</button>}
        {c.status === 'awaiting_approval' && !can('campaigns.approve') && <span className="mute small">Waiting for the owner</span>}
        {c.status === 'approved' && can('campaigns.approve') && <button className="btn small primary" disabled={quiet} onClick={() => setConfirm(true)}>Run in practice mode</button>}
        {['draft', 'awaiting_approval', 'approved'].includes(c.status) && can('campaigns.edit') && <button className="btn small ghost" onClick={() => post('cancel', 'Cancelled')}>Cancel</button>}
      </footer>
      <Modal open={confirm} onClose={() => setConfirm(false)} title="Run this campaign?" footer={<><button className="btn" onClick={() => setConfirm(false)}>Not yet</button><button className="btn primary" onClick={async () => { if (await post('run', 'Done in practice mode')) setConfirm(false) }}>Run in practice mode</button></>}>
        <p>This writes a "would send" message for each eligible person and shows it in their conversation. Nothing is really sent. Consent is checked again right now.</p>
      </Modal>
      <Modal open={!!more} onClose={() => setMore(null)} title={c.name} footer={<button className="btn" onClick={() => setMore(null)}>Close</button>}>
        {more && <><p className="cmp-body">{more.subject && <b>{more.subject}<br /></b>}{more.body}</p>
          {more.recipients?.length ? <div className="tablewrap"><table><thead><tr><th>Person</th><th>Result</th></tr></thead><tbody>{more.recipients.map((r) => <tr key={r.contact.id}><td>{r.contact.display_name}</td><td>{r.state === 'dry_run_sent' ? 'Would be sent' : <span className="mute">Left out: {r.skipped_label}</span>}</td></tr>)}</tbody></table></div> : <p className="mute">It has not been run yet.</p>}</>}
      </Modal>
    </article>
  )
}

function Builder({ start, mergeFields, onClose, onSaved }: { start: Camp | null; mergeFields: string[]; onClose: () => void; onSaved: () => void }) {
  const { toast } = useApp()
  const [name, setName] = useState(start?.name || '')
  const [channel, setChannel] = useState<'sms' | 'email'>(start?.channel || 'sms')
  const [purpose, setPurpose] = useState<'care' | 'promo'>(start?.purpose || 'care')
  const [seg, setSeg] = useState<Seg>(start ? fromSegment(start.segment) : { ...EMPTY, kind: 'patient' })
  const [subject, setSubject] = useState(start?.subject || '')
  const [body, setBody] = useState(start?.body || 'Hi {first_name}, ')
  const [pv, setPv] = useState<Preview | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const area = useRef<HTMLTextAreaElement>(null)
  const sset = (k: keyof Seg, v: string) => setSeg((s) => ({ ...s, [k]: v }))

  useEffect(() => {
    const t = window.setTimeout(async () => {
      try { setPv(await api<Preview>('/campaigns/preview', { method: 'POST', body: { channel, purpose, segment: toSegment(seg), subject, body }, quiet: true })); setErr('') }
      catch (x) { setPv(null); setErr(errText(x) || '') }
    }, 350)
    return () => clearTimeout(t)
  }, [channel, purpose, seg, subject, body])

  function insert(f: string) {
    const el = area.current, tok = `{${f}}`
    if (!el) { setBody((b) => b + tok); return }
    const a = el.selectionStart ?? body.length, z = el.selectionEnd ?? body.length
    setBody(body.slice(0, a) + tok + body.slice(z))
    window.setTimeout(() => { el.focus(); el.setSelectionRange(a + tok.length, a + tok.length) }, 0)
  }
  async function save() {
    setBusy(true); setErr('')
    const payload = { name, channel, purpose, segment: toSegment(seg), subject: channel === 'email' ? subject : '', body }
    try {
      if (start) await api(`/campaigns/${start.id}`, { method: 'PATCH', body: payload, quiet: true })
      else await api('/campaigns', { method: 'POST', body: payload, quiet: true })
      toast('Saved as a draft'); onSaved()
    } catch (x) { setErr(errText(x) || 'Could not save the campaign.') }
    setBusy(false)
  }
  return (
    <section className="card pad cmp-builder" aria-label="Campaign builder">
      <header><h2>{start ? 'Edit campaign' : 'New campaign'}</h2><button className="ibtn" onClick={onClose} aria-label="Close builder"><Icon n="x" /></button></header>
      <div className="cb-grid">
        <div className="cb-col">
          <label>Name<input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} placeholder="For example: Vaccine reminder" data-autofocus /></label>
          <div className="two">
            <div><span className="lbl">Channel</span><Segmented<'sms' | 'email'> label="Channel" value={channel} onChange={setChannel} options={[['sms', 'Text'], ['email', 'Email']]} /></div>
            <div><span className="lbl">Kind of message</span><Segmented<'care' | 'promo'> label="Kind" value={purpose} onChange={setPurpose} options={[['care', 'Care'], ['promo', 'Promotion']]} /></div>
          </div>
          <p className="note">{purpose === 'care' ? 'Care: reminders and follow-ups for existing patients. People with care-only consent are included.' : 'Promotion: anything that sells or announces. Only people with full consent are included.'}</p>
          <h3>Who</h3>
          <div className="two">
            <label>Status<select value={seg.kind} onChange={(e) => sset('kind', e.target.value)}><option value="">Inquiries and patients</option><option value="patient">Patients</option><option value="inquiry">Inquiries</option></select></label>
            <label>Pet species<input value={seg.species} onChange={(e) => sset('species', e.target.value)} placeholder="dog, cat..." maxLength={40} /></label>
            <label>Visits at least<input type="number" min={0} value={seg.visits_min} onChange={(e) => sset('visits_min', e.target.value)} /></label>
            <label>Visits at most<input type="number" min={0} value={seg.visits_max} onChange={(e) => sset('visits_max', e.target.value)} /></label>
            <label>Last visit from<input type="date" value={seg.last_visit_from} onChange={(e) => sset('last_visit_from', e.target.value)} /></label>
            <label>Last visit to<input type="date" value={seg.last_visit_to} onChange={(e) => sset('last_visit_to', e.target.value)} /></label>
            <label>Came from<select value={seg.source} onChange={(e) => sset('source', e.target.value)}><option value="">Anywhere</option><option value="phone">Phone call</option><option value="walk_in">Walk-in</option><option value="website">Website</option><option value="referral">Referral</option><option value="after_hours">After hours</option></select></label>
            <label>Open task<select value={seg.has_open_task} onChange={(e) => sset('has_open_task', e.target.value)}><option value="">Either</option><option value="yes">Has an open task</option><option value="no">No open task</option></select></label>
          </div>
          <label>Search<input value={seg.q} onChange={(e) => sset('q', e.target.value)} placeholder="Name, email, phone or pet" maxLength={80} /></label>
        </div>
        <div className="cb-col">
          <h3>Message</h3>
          {channel === 'email' && <label>Subject<input value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={150} /></label>}
          <label>Text<textarea ref={area} value={body} onChange={(e) => setBody(e.target.value)} rows={6} maxLength={1600} /></label>
          <div className="quick2" aria-label="Insert a field">{mergeFields.map((f) => <button key={f} type="button" onClick={() => insert(f)}>{`{${f}}`}</button>)}</div>
          <p className="note">{channel === 'sms' ? '"Reply STOP to opt out." is added to every text.' : 'An unsubscribe line is added to every email.'}</p>
          <h3>Who would get it</h3>
          {pv ? (
            <div className="pvw">
              <p><b>{pv.eligible}</b> of {pv.matched} people would get this{pv.quiet_hours ? '. It cannot run during quiet hours.' : '.'}</p>
              {pv.skipped_list.length > 0 && <ul className="skipl">{pv.skipped_list.map((s) => <li key={s.reason}><b>{s.count}</b> left out: {s.label}</li>)}</ul>}
              {pv.samples.map((s) => <div key={s.contact.id} className="sample"><span className="mute small">To {s.contact.display_name}</span>{s.subject && <b>{s.subject}</b>}<p>{s.text}</p></div>)}
            </div>
          ) : <p className="mute">{err || 'Preview appears here.'}</p>}
        </div>
      </div>
      {err && pv && <p className="errt" role="alert">{err}</p>}
      <footer className="cb-foot"><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={busy || name.trim().length < 2 || body.trim().length < 2} onClick={save}>Save draft</button></footer>
    </section>
  )
}
