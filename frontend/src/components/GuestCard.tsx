import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import { useApp } from '../ctx'
import { useLive } from '../live'
import { Avatar, ConsentPill, Empty, KindBadge, Modal, Skeleton, Switch, Time } from './ui'
import { Icon } from './icons'
import { useContactDetail, useStaff } from '../lib/store'
import { ageOf, bytes, clock, dayLabel, exact, hueFor, shortDate, toLocalInput } from '../lib/fmt'
import { CONSENT_LABEL, REL_LABEL, SOURCE_LABEL, VISIT_LABEL, emojiFor, type Consent, type Detail, type Ev, type Pet, type Row, type Visit } from '../lib/types'

export type CardTab = 'contact' | 'pets' | 'calendar' | 'services'
const HOW = ['Said yes in person', 'Said yes on the phone', 'Signed a form', 'Ticked a box on our website', 'Replied YES by text', 'Other']
const SPECIES = ['dog', 'cat', 'rabbit', 'bird', 'reptile', 'small mammal', 'other']
const errText = (x: unknown) => x instanceof ApiError && x.message && x.message !== 'error' && x.message !== 'network' ? x.message : ''

export function GuestCard({ contactId, tab, onTab, showOpen }: { contactId: number; tab: CardTab; onTab: (t: CardTab) => void; showOpen?: boolean }) {
  const det = useContactDetail(contactId)
  const d = det.data
  const tabs: [CardTab, string][] = [['contact', 'Contact'], ['pets', 'Pets'], ['calendar', 'Calendar'], ['services', 'Services']]
  function arrow(e: React.KeyboardEvent) {
    const i = tabs.findIndex(([k]) => k === tab)
    if (e.key === 'ArrowRight') { e.preventDefault(); onTab(tabs[(i + 1) % tabs.length][0]) }
    if (e.key === 'ArrowLeft') { e.preventDefault(); onTab(tabs[(i + tabs.length - 1) % tabs.length][0]) }
  }
  return (
    <div className="gcard">
      <div className="gc-tabs" role="tablist" aria-label="Guest Card" onKeyDown={arrow}>
        {tabs.map(([k, l]) => <button key={k} role="tab" id={'gct-' + k} aria-selected={tab === k} aria-controls="gc-panel" tabIndex={tab === k ? 0 : -1} onClick={() => onTab(k)}>{l}</button>)}
      </div>
      <div className="gc-body" id="gc-panel" role="tabpanel" aria-labelledby={'gct-' + tab}>
        {det.loading && <div className="gc-sk"><Skeleton h={48} r={10} /><Skeleton /><Skeleton w="70%" /><Skeleton h={80} r={10} /></div>}
        {det.error && !d && <Empty icon="alert" title={det.error === 404 ? 'Contact not found' : 'Could not load this contact'} text={det.error === 404 ? 'They may have been erased.' : 'It will try again.'} />}
        {d && tab === 'contact' && <ContactTab d={d} id={contactId} det={det} showOpen={showOpen} />}
        {d && tab === 'pets' && <PetsTab d={d} id={contactId} det={det} />}
        {d && tab === 'calendar' && <CalendarTab id={contactId} d={d} />}
        {d && tab === 'services' && <ServicesTab />}
      </div>
    </div>
  )
}
type Det = ReturnType<typeof useContactDetail>

/* ---------------------------------- Contact tab ---------------------------------- */
function ContactTab({ d, id, det, showOpen }: { d: Detail; id: number; det: Det; showOpen?: boolean }) {
  const { toast, can } = useApp()
  const staff = useStaff()
  const nav = useNavigate()
  const c = d.contact
  const [editName, setEditName] = useState(false)
  const [name, setName] = useState(c.display_name)
  const [erase, setErase] = useState(false)

  async function save(body: Record<string, unknown>, local: (x: Detail) => Detail, reloadAfter = false, okMsg?: string) {
    const undo = det.patch(local)
    try { await api(`/contacts/${id}`, { method: 'PATCH', body, quiet: true }); if (okMsg) toast(okMsg); if (reloadAfter) det.reload(); return true }
    catch (x) { undo(); toast(errText(x) || 'That change was not saved.'); return false }
  }
  const setC = (p: Partial<Detail['contact']>) => (x: Detail) => ({ ...x, contact: { ...x.contact, ...p } })

  return (
    <div className="gc-sec">
      <div className="gc-id">
        <Avatar name={c.display_name} initials={c.initials} hue={c.avatar_hue} size={52} />
        <div className="gc-idm">
          {editName ? (
            <form className="inl" onSubmit={async (e) => { e.preventDefault(); const n = name.trim(); if (n.length < 2) return; setEditName(false); if (n !== c.display_name) await save({ display_name: n }, setC({ display_name: n })) }}>
              <input data-autofocus value={name} onChange={(e) => setName(e.target.value)} maxLength={120} aria-label="Name" />
              <button className="btn primary small" type="submit">Save</button>
              <button className="btn small" type="button" onClick={() => { setEditName(false); setName(c.display_name) }}>Cancel</button>
            </form>
          ) : (
            <div className="gc-name"><h3>{c.display_name}</h3><button className="ibtn sm" onClick={() => { setName(c.display_name); setEditName(true) }} aria-label="Edit name"><Icon n="edit" size={14} /></button></div>
          )}
          <div className="gc-kind">
            <KindBadge kind={c.kind} />
            {can('contacts.edit') && <button className="link-a" onClick={() => save({ kind: c.kind === 'patient' ? 'inquiry' : 'patient' }, setC({ kind: c.kind === 'patient' ? 'inquiry' : 'patient' }), false, c.kind === 'patient' ? 'Marked as an inquiry' : 'Marked as a patient')}>{c.kind === 'patient' ? 'Switch to inquiry' : 'Switch to patient'}</button>}
            {showOpen && <Link to={`/contacts/${id}`} className="link-a">Open conversation</Link>}
          </div>
        </div>
      </div>

      <Channel kind="email" c={c} save={save} setC={setC} />
      <Channel kind="phone" c={c} save={save} setC={setC} />

      <Notes c={c} id={id} save={save} setC={setC} />

      <div className="gc-row2">
        <label className="f">Owner
          <select value={c.owner?.id ?? ''} onChange={(e) => { const v = e.target.value; const s = staff.find((x) => String(x.id) === v); save({ owner_id: v ? Number(v) : null }, setC({ owner: s ? { id: s.id, display_name: s.display_name } : null })) }}>
            <option value="">Unassigned</option>
            {staff.map((s) => <option key={s.id} value={s.id}>{s.display_name}</option>)}
          </select>
        </label>
        <label className="f">Source
          <select value={c.source} onChange={(e) => save({ source: e.target.value }, setC({ source: e.target.value }))}>
            {Object.entries(SOURCE_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </label>
      </div>

      <div className="gc-ai">
        <div><b>AI assistant</b><p className="mute">{c.ai_enabled ? 'May reply to this person. Your team sees every reply.' : 'Off. Only your team replies to this person.'}</p></div>
        <Switch checked={c.ai_enabled} label="AI assistant for this contact" onChange={(v) => save({ ai_enabled: v }, setC({ ai_enabled: v }), false, v ? 'AI assistant turned on' : 'AI assistant turned off')} />
      </div>

      <Links d={d} id={id} det={det} />

      <div className="gc-meta">
        <div><span className="mute">Created</span><Time iso={c.created_at} /></div>
        <div><span className="mute">Last active</span><Time iso={c.last_active_at} /></div>
      </div>

      <section className="gc-s">
        <h4>Documents</h4>
        {d.attachments.length === 0 ? <p className="mute">No documents yet. Files sent in messages show up here.</p> : (
          <ul className="docs">{d.attachments.map((a) => (
            <li key={a.id}><a href={`/api/attachments/${a.id}`} download><Icon n="clip" size={15} /><span className="ell">{a.filename}</span><small>{bytes(a.bytes)}</small></a></li>
          ))}</ul>
        )}
      </section>

      {can('admin.contacts_erase') && (
        <section className="gc-s danger">
          <h4>Admin</h4>
          <button className="btn danger" onClick={() => setErase(true)}><Icon n="trash" size={16} /> Erase contact</button>
          <p className="mute">Removes this person and all their messages, notes and files for good.</p>
          <EraseModal open={erase} onClose={() => setErase(false)} name={c.display_name} onDone={async () => {
            try { await api(`/admin/contacts/${id}/erase`, { method: 'POST' }); toast(`Erased ${c.display_name}`); setErase(false); nav('/contacts', { replace: true }) } catch { /* toast shown */ }
          }} />
        </section>
      )}
    </div>
  )
}

function EraseModal({ open, onClose, name, onDone }: { open: boolean; onClose: () => void; name: string; onDone: () => Promise<void> }) {
  const [t, setT] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (open) setT('') }, [open])
  const ok = t.trim().toLowerCase() === name.trim().toLowerCase()
  return (
    <Modal open={open} onClose={onClose} title={`Erase ${name}?`} footer={<><button className="btn" onClick={onClose}>Keep them</button><button className="btn danger solid" disabled={!ok || busy} onClick={async () => { setBusy(true); await onDone(); setBusy(false) }}>Erase for good</button></>}>
      <p>This deletes the contact, their pets, messages, notes, reminders and files. It cannot be undone.</p>
      <label className="f">Type their name to confirm<input data-autofocus value={t} onChange={(e) => setT(e.target.value)} placeholder={name} autoComplete="off" /></label>
    </Modal>
  )
}

type SaveFn = (body: Record<string, unknown>, local: (x: Detail) => Detail, reloadAfter?: boolean, okMsg?: string) => Promise<boolean>
function Channel({ kind, c, save, setC }: { kind: 'email' | 'phone'; c: Detail['contact']; save: SaveFn; setC: (p: Partial<Detail['contact']>) => (x: Detail) => Detail }) {
  const { can } = useApp()
  const email = kind === 'email'
  const value = email ? c.email : c.phone_display || c.phone
  const consent = email ? c.email_consent : c.sms_consent
  const [edit, setEdit] = useState(false)
  const [v, setV] = useState('')
  const [cs, setCs] = useState<Consent>('none')
  const [how, setHow] = useState(c.consent_source && HOW.includes(c.consent_source) ? c.consent_source : HOW[0])
  const [busy, setBusy] = useState(false)
  function open() { setV(email ? c.email || '' : c.phone_display || c.phone || ''); setCs(consent); setEdit(true) }
  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    const body: Record<string, unknown> = {}
    const cur = email ? c.email || '' : c.phone_display || c.phone || ''
    if (v.trim() !== cur) body[kind] = v.trim() || null
    if (cs !== consent) { body[email ? 'email_consent' : 'sms_consent'] = cs; if (cs !== 'none' && cs !== 'unsubscribed') body.consent_source = how }
    if (Object.keys(body).length) {
      const local: Partial<Detail['contact']> = {}
      if (email) { if ('email' in body) local.email = (body.email as string) || null; if (cs !== consent) local.email_consent = cs }
      else if (cs !== consent) local.sms_consent = cs
      const ok = await save(body, setC(local), kind in body, 'Saved')
      if (ok) setEdit(false)
    } else setEdit(false)
    setBusy(false)
  }
  return (
    <div className="gc-ch">
      <div className="gc-ch-h"><span className="mute">{email ? 'Email' : 'Phone'}</span><ConsentPill value={consent} />{can('contacts.edit') && !edit && <button className="ibtn sm" onClick={open} aria-label={`Edit ${email ? 'email' : 'phone'} and consent`}><Icon n="edit" size={14} /></button>}</div>
      {!edit ? (
        <div className="gc-ch-v">{value ? <b>{value}</b> : <span className="mute">None on file</span>}</div>
      ) : (
        <form className="gc-edit" onSubmit={submit}>
          <input data-autofocus value={v} onChange={(e) => setV(e.target.value)} placeholder={email ? 'name@example.com' : '604 555 0123'} inputMode={email ? 'email' : 'tel'} aria-label={email ? 'Email address' : 'Phone number'} />
          <fieldset className="fs tight"><legend>Consent for {email ? 'email' : 'texts'}</legend>
            {(['full', 'transactional', 'none', 'unsubscribed'] as Consent[]).map((o) => (
              <label key={o} className={'rad sm' + (cs === o ? ' on' : '')}><input type="radio" name={'cs-' + kind} checked={cs === o} onChange={() => setCs(o)} /><span><i className={'cdot c-' + o} aria-hidden="true" /> {CONSENT_LABEL[o]}</span></label>
            ))}
          </fieldset>
          {(cs === 'full' || cs === 'transactional') && cs !== consent && <label className="f">How did they agree?<select value={how} onChange={(e) => setHow(e.target.value)}>{HOW.map((h) => <option key={h}>{h}</option>)}</select></label>}
          <div className="rowbtn"><button className="btn primary small" type="submit" disabled={busy}>Save</button><button className="btn small" type="button" onClick={() => setEdit(false)}>Cancel</button></div>
        </form>
      )}
      {!edit && c.consent_at && consent !== 'none' && <p className="mute small">{c.consent_source ? `${c.consent_source}. ` : ''}Recorded {exact(c.consent_at)}</p>}
    </div>
  )
}

function Notes({ c, id, save, setC }: { c: Detail['contact']; id: number; save: SaveFn; setC: (p: Partial<Detail['contact']>) => (x: Detail) => Detail }) {
  const server = c.preference_notes || ''
  const [txt, setTxt] = useState(server)
  const [state, setState] = useState<'idle' | 'dirty' | 'saving' | 'saved' | 'err'>('idle')
  const [hist, setHist] = useState(false)
  const timer = useRef<number>()
  const last = useRef(server)
  const dirty = useRef(false)
  useEffect(() => { setTxt(server); last.current = server; dirty.current = false; setState('idle') }, [id]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (!dirty.current) { setTxt(server); last.current = server } }, [server])
  useEffect(() => () => clearTimeout(timer.current), [])
  async function flush(v: string) {
    if (v === last.current) { dirty.current = false; setState('idle'); return }
    setState('saving')
    const ok = await save({ preference_notes: v }, setC({ preference_notes: v }))
    if (ok) { last.current = v; if (v === (document.getElementById('prefnotes') as HTMLTextAreaElement | null)?.value) dirty.current = false; setState('saved') } else setState('err')
  }
  return (
    <section className="gc-s">
      <div className="gc-sh"><h4>Preference notes</h4><button className="link-a" onClick={() => setHist(!hist)} aria-expanded={hist}>Edit history</button></div>
      <textarea id="prefnotes" value={txt} rows={3} maxLength={2000} placeholder="Add notes, like the best time to call or a favourite treat" aria-label="Preference notes"
        onChange={(e) => { setTxt(e.target.value); dirty.current = true; setState('dirty'); clearTimeout(timer.current); const v = e.target.value; timer.current = window.setTimeout(() => flush(v), 900) }}
        onBlur={() => { clearTimeout(timer.current); if (dirty.current) flush(txt) }} />
      <p className="mute small" aria-live="polite">{state === 'saving' ? 'Saving' : state === 'saved' ? 'Saved' : state === 'dirty' ? 'Typing. Saves by itself.' : state === 'err' ? 'Not saved. Edit again to retry.' : 'Saves by itself.'}</p>
      {hist && <NoteHistory id={id} version={server} />}
    </section>
  )
}
function NoteHistory({ id, version }: { id: number; version: string }) {
  const h = useLive<{ items: any[] }>(`/contacts/${id}/note-history`, [])
  useEffect(() => { h.reload() }, [version]) // eslint-disable-line react-hooks/exhaustive-deps
  const items = h.data?.items || []
  return (
    <div className="hist">
      {!h.data && <Skeleton />}
      {h.data && items.length === 0 && <p className="mute">No edits yet.</p>}
      {items.map((x, i) => (
        <div key={x.id ?? i} className="hist-i">
          <div className="mute small">{typeof x.edited_by === 'object' && x.edited_by ? x.edited_by.display_name : x.edited_by_name || x.edited_by || 'Someone'} · <Time iso={x.edited_at} /></div>
          {x.old_text ? <div className="old">{x.old_text}</div> : <div className="mute">(was empty)</div>}
          <div className="new">{x.new_text || <span className="mute">(cleared)</span>}</div>
        </div>
      ))}
    </div>
  )
}

/* ------------------------------ Associated contacts ------------------------------ */
function Links({ d, id, det }: { d: Detail; id: number; det: Det }) {
  const { toast, can } = useApp()
  const [adding, setAdding] = useState(false)
  async function remove(l: Detail['links'][number]) {
    const undo = det.patch((x) => ({ ...x, links: x.links.filter((y) => y.id !== l.id) }))
    try {
      await api(`/contact-links/${l.id}`, { method: 'DELETE', quiet: true })
      toast(`Removed ${l.contact.display_name}`, async () => {
        try { await api(`/contacts/${id}/links`, { body: { relationship: l.relationship, note: l.note || undefined, related_contact_id: l.contact.id }, quiet: true }); det.reload() } catch { toast('Could not bring the link back.') }
      })
    } catch { undo(); toast('Could not remove that link.') }
  }
  return (
    <section className="gc-s">
      <div className="gc-sh"><h4>Associated contacts <span className="count">{d.links.length}</span></h4>{can('contacts.edit') && !adding && <button className="btn small" onClick={() => setAdding(true)}><Icon n="plus" size={14} /> Add</button>}</div>
      {d.links.length === 0 && !adding && <p className="mute">No one linked yet. Link a friend, spouse or caregiver who deals with the clinic for this person.</p>}
      {d.links.map((l) => (
        <div className="lk" key={l.id}>
          <Link to={`/contacts/${l.contact.id}`} className="lk-l"><Avatar name={l.contact.display_name} initials={l.contact.initials} hue={l.contact.avatar_hue} size={32} /><span><b>{l.contact.display_name}</b><small>{REL_LABEL[l.relationship] || l.relationship}{l.note ? `. ${l.note}` : ''}</small></span></Link>
          {can('contacts.edit') && <button className="ibtn sm" onClick={() => remove(l)} aria-label={`Remove link to ${l.contact.display_name}`}><Icon n="x" size={14} /></button>}
        </div>
      ))}
      {adding && <LinkAdder id={id} exclude={[id, ...d.links.map((l) => l.contact.id)]} onDone={() => { setAdding(false); det.reload() }} onCancel={() => setAdding(false)} />}
    </section>
  )
}
function LinkAdder({ id, exclude, onDone, onCancel }: { id: number; exclude: number[]; onDone: () => void; onCancel: () => void }) {
  const { toast } = useApp()
  const [mode, setMode] = useState<'find' | 'new'>('find')
  const [q, setQ] = useState('')
  const [res, setRes] = useState<Row[]>([])
  const [pick, setPick] = useState<Row | null>(null)
  const [rel, setRel] = useState('friend')
  const [note, setNote] = useState('')
  const [nc, setNc] = useState({ display_name: '', phone: '', email: '', kind: 'inquiry' })
  const [busy, setBusy] = useState(false)
  const seq = useRef(0)
  useEffect(() => {
    if (q.trim().length < 2) { setRes([]); return }
    const my = ++seq.current
    const t = window.setTimeout(async () => { try { const r = await api<{ items: Row[] }>('/contacts?per_page=10&q=' + encodeURIComponent(q.trim()), { quiet: true }); if (my === seq.current) setRes(r.items.filter((x) => !exclude.includes(x.id)).slice(0, 6)) } catch { /* */ } }, 220)
    return () => clearTimeout(t)
  }, [q]) // eslint-disable-line react-hooks/exhaustive-deps
  async function go(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      if (mode === 'find' && pick) await api(`/contacts/${id}/links`, { quiet: true, body: { relationship: rel, note: note.trim() || undefined, related_contact_id: pick.id } })
      else if (mode === 'new' && nc.display_name.trim().length >= 2) await api(`/contacts/${id}/links`, { quiet: true, body: { relationship: rel, note: note.trim() || undefined, new_contact: { display_name: nc.display_name.trim(), kind: nc.kind, phone: nc.phone.trim() || undefined, email: nc.email.trim() || undefined } } })
      else { setBusy(false); return }
      toast('Linked'); onDone()
    } catch (x) { toast(errText(x) || 'Could not add that link.') }
    setBusy(false)
  }
  return (
    <form className="lk-add" onSubmit={go}>
      <div className="seg2" role="group" aria-label="Find or create"><button type="button" aria-pressed={mode === 'find'} onClick={() => setMode('find')}>Find existing</button><button type="button" aria-pressed={mode === 'new'} onClick={() => setMode('new')}>Create new</button></div>
      {mode === 'find' ? (
        <>
          {!pick ? (
            <>
              <input data-autofocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by name, phone or email" aria-label="Search contacts to link" />
              {res.map((r) => <button type="button" key={r.id} className="lk-res" onClick={() => setPick(r)}><Avatar name={r.display_name} initials={r.initials} hue={r.avatar_hue} size={28} /><span><b>{r.display_name}</b><small>{r.phone_display || r.email || ''}</small></span></button>)}
              {q.trim().length >= 2 && res.length === 0 && <p className="mute">No match. Try "Create new".</p>}
            </>
          ) : <div className="lk-res on"><Avatar name={pick.display_name} initials={pick.initials} hue={pick.avatar_hue} size={28} /><span><b>{pick.display_name}</b></span><button type="button" className="ibtn sm" onClick={() => setPick(null)} aria-label="Choose someone else"><Icon n="x" size={14} /></button></div>}
        </>
      ) : (
        <>
          <input data-autofocus value={nc.display_name} onChange={(e) => setNc({ ...nc, display_name: e.target.value })} placeholder="Name" maxLength={120} aria-label="Name" />
          <div className="two"><input value={nc.phone} onChange={(e) => setNc({ ...nc, phone: e.target.value })} placeholder="Phone" inputMode="tel" aria-label="Phone" /><input value={nc.email} onChange={(e) => setNc({ ...nc, email: e.target.value })} placeholder="Email" inputMode="email" aria-label="Email" /></div>
          <select value={nc.kind} onChange={(e) => setNc({ ...nc, kind: e.target.value })} aria-label="Status"><option value="inquiry">Inquiry</option><option value="patient">Patient</option></select>
          <p className="mute small">Their consent starts as none. Ask them, then record it on their page.</p>
        </>
      )}
      <select value={rel} onChange={(e) => setRel(e.target.value)} aria-label="Relationship">{Object.entries(REL_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
      <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" maxLength={200} aria-label="Note" />
      <div className="rowbtn"><button className="btn primary small" type="submit" disabled={busy || (mode === 'find' ? !pick : nc.display_name.trim().length < 2)}>Add link</button><button className="btn small" type="button" onClick={onCancel}>Cancel</button></div>
    </form>
  )
}

/* ------------------------------------- Pets ------------------------------------- */
function PetsTab({ d, id, det }: { d: Detail; id: number; det: Det }) {
  const { can } = useApp()
  const [adding, setAdding] = useState(false)
  const otherVisits = d.visits.filter((v) => !v.pet)
  return (
    <div className="gc-sec">
      <div className="gc-sh"><h4>Pets <span className="count">{d.pets.length}</span></h4>{can('contacts.edit') && !adding && <button className="btn small" onClick={() => setAdding(true)}><Icon n="plus" size={14} /> Add pet</button>}</div>
      {adding && <PetForm id={id} onDone={() => { setAdding(false); det.reload() }} onCancel={() => setAdding(false)} />}
      {d.pets.length === 0 && !adding && <Empty icon="people" title="No pets yet" text="Add a pet to keep their visits and details here." action={can('contacts.edit') ? <button className="btn" onClick={() => setAdding(true)}>Add pet</button> : undefined} />}
      {d.pets.map((p) => <PetCard key={p.id} p={p} visits={d.visits.filter((v) => v.pet?.id === p.id)} det={det} />)}
      {otherVisits.length > 0 && (
        <section className="gc-s"><h4>Other visits</h4>{otherVisits.map((v) => <VisitLine key={v.id} v={v} />)}</section>
      )}
    </div>
  )
}
function VisitLine({ v }: { v: Visit }) {
  return <div className="vline"><b>{VISIT_LABEL[v.kind] || v.kind}</b><span className="mute" title={exact(v.visited_at)}>{shortDate(v.visited_at)}</span>{v.summary && <p>{v.summary}</p>}</div>
}
function PetCard({ p, visits, det }: { p: Pet; visits: Visit[]; det: Det }) {
  const { can, toast } = useApp()
  const [edit, setEdit] = useState(false)
  const [all, setAll] = useState(false)
  const age = ageOf(p.birth_date)
  async function status(s: Pet['status']) {
    const undo = det.patch((x) => ({ ...x, pets: x.pets.map((y) => y.id === p.id ? { ...y, status: s } : y) }))
    try { await api(`/pets/${p.id}`, { method: 'PATCH', body: { status: s }, quiet: true }); toast(`${p.name} is now ${s}`) } catch { undo(); toast('Could not change that.') }
  }
  const shown = all ? visits : visits.slice(0, 3)
  if (edit) return <PetForm id={0} pet={p} onDone={() => { setEdit(false); det.reload() }} onCancel={() => setEdit(false)} />
  return (
    <section className={'pet' + (p.status !== 'active' ? ' off' : '')}>
      <div className="pet-h">
        <span className="pet-e" aria-hidden="true">{emojiFor(p.species)}</span>
        <div className="pet-m"><b>{p.name}</b><span className="mute">{[p.breed || p.species, p.sex].filter(Boolean).join(', ')}</span></div>
        {can('contacts.edit') && <button className="ibtn sm" onClick={() => setEdit(true)} aria-label={`Edit ${p.name}`}><Icon n="edit" size={14} /></button>}
      </div>
      <div className="pet-facts">
        {age && <span><small>Age</small>{age}</span>}
        {p.weight_kg != null && <span><small>Weight</small>{p.weight_kg} kg</span>}
        <span><small>Status</small>{can('contacts.edit') ? <select value={p.status} onChange={(e) => status(e.target.value as Pet['status'])} aria-label={`${p.name} status`}><option value="active">Active</option><option value="deceased">Deceased</option><option value="moved">Moved</option></select> : p.status}</span>
      </div>
      {p.notes && <p className="pet-n">{p.notes}</p>}
      <div className="pet-v">
        <h5>Visits</h5>
        {visits.length === 0 ? <p className="mute">No visits on record.</p> : shown.map((v) => <VisitLine key={v.id} v={v} />)}
        {visits.length > 3 && <button className="link-a" onClick={() => setAll(!all)}>{all ? 'Show fewer' : `Show all ${visits.length}`}</button>}
      </div>
    </section>
  )
}
function PetForm({ id, pet, onDone, onCancel }: { id: number; pet?: Pet; onDone: () => void; onCancel: () => void }) {
  const { toast } = useApp()
  const [f, setF] = useState({ name: pet?.name || '', species: pet?.species || 'dog', breed: pet?.breed || '', sex: pet?.sex || '', birth_date: pet?.birth_date || '', weight_kg: pet?.weight_kg != null ? String(pet.weight_kg) : '', notes: pet?.notes || '' })
  const [busy, setBusy] = useState(false)
  const set = (k: string, v: string) => setF((x) => ({ ...x, [k]: v }))
  async function go(e: FormEvent) {
    e.preventDefault()
    if (!f.name.trim()) return
    setBusy(true)
    const body: Record<string, unknown> = { name: f.name.trim(), species: f.species, breed: f.breed.trim() || null, sex: f.sex || null, birth_date: f.birth_date || null, weight_kg: f.weight_kg ? Number(f.weight_kg) : null, notes: f.notes.trim() || null }
    try {
      if (pet) await api(`/pets/${pet.id}`, { method: 'PATCH', body, quiet: true })
      else await api(`/contacts/${id}/pets`, { body, quiet: true })
      toast(pet ? 'Pet saved' : `Added ${f.name.trim()}`); onDone()
    } catch (x) { toast(errText(x) || 'Could not save the pet.') }
    setBusy(false)
  }
  return (
    <form className="pet-form" onSubmit={go}>
      <h4>{pet ? `Edit ${pet.name}` : 'Add a pet'}</h4>
      <label className="f">Name<input data-autofocus value={f.name} onChange={(e) => set('name', e.target.value)} maxLength={60} /></label>
      <div className="two">
        <label className="f">Type<select value={f.species} onChange={(e) => set('species', e.target.value)}>{[...new Set([...SPECIES, f.species])].map((s) => <option key={s}>{s}</option>)}</select></label>
        <label className="f">Breed<input value={f.breed} onChange={(e) => set('breed', e.target.value)} maxLength={60} /></label>
      </div>
      <div className="two">
        <label className="f">Sex<select value={f.sex} onChange={(e) => set('sex', e.target.value)}><option value="">Not sure</option>{[...new Set(['female', 'male', f.sex].filter(Boolean))].map((s) => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>)}</select></label>
        <label className="f">Birth date<input type="date" value={f.birth_date} onChange={(e) => set('birth_date', e.target.value)} /></label>
      </div>
      <label className="f">Weight (kg)<input type="number" min="0" step="0.1" value={f.weight_kg} onChange={(e) => set('weight_kg', e.target.value)} /></label>
      <label className="f">Notes<textarea rows={2} value={f.notes} onChange={(e) => set('notes', e.target.value)} maxLength={500} /></label>
      <div className="rowbtn"><button className="btn primary small" type="submit" disabled={busy || !f.name.trim()}>Save pet</button><button className="btn small" type="button" onClick={onCancel}>Cancel</button></div>
    </form>
  )
}

/* ----------------------------------- Calendar ----------------------------------- */
function CalendarTab({ id, d }: { id: number; d: Detail }) {
  const range = useMemo(() => { const a = new Date(); a.setFullYear(a.getFullYear() - 2); const b = new Date(); b.setFullYear(b.getFullYear() + 1); return `from=${a.toISOString().slice(0, 10)}&to=${b.toISOString().slice(0, 10)}` }, [])
  const ev = useLive<{ items: Ev[] }>(`/events?contact_id=${id}&${range}`, ['events_cal'])
  const all = ev.data?.items ?? (d.upcoming_events || [])
  const now = Date.now()
  const up = all.filter((e) => new Date(e.starts_at).getTime() >= now && e.status !== 'cancelled').sort((a, b) => a.starts_at.localeCompare(b.starts_at))
  const past = all.filter((e) => !up.includes(e)).sort((a, b) => b.starts_at.localeCompare(a.starts_at))
  if (ev.loading && !ev.data) return <div className="gc-sk"><Skeleton h={44} r={10} /><Skeleton h={44} r={10} /></div>
  return (
    <div className="gc-sec">
      <h4>Coming up</h4>
      {up.length === 0 ? <p className="mute">Nothing booked.</p> : up.map((e) => <EvLine key={e.id} e={e} />)}
      <h4>Before</h4>
      {past.length === 0 ? <p className="mute">No past events.</p> : past.slice(0, 20).map((e) => <EvLine key={e.id} e={e} />)}
    </div>
  )
}
function EvLine({ e }: { e: Ev }) {
  return (
    <div className={'evl k-' + e.kind + (e.status === 'cancelled' ? ' off' : '')}>
      <div className="evl-d"><b>{dayLabel(e.starts_at)}</b><span>{clock(e.starts_at)}</span></div>
      <div className="evl-m"><b>{e.title}</b><span className="mute">{e.kind_label}{e.pet ? `, ${e.pet.name}` : ''}{e.staff ? `, ${e.staff.display_name}` : ''}</span></div>
      {e.status !== 'booked' && <span className={'tag ' + (e.status === 'cancelled' ? 'high' : e.status === 'done' ? 'ok' : 'warn')}>{e.status === 'requested' ? 'Requested' : e.status === 'cancelled' ? 'Cancelled' : 'Done'}</span>}
    </div>
  )
}

/* ----------------------------------- Services ----------------------------------- */
type VT = { id: number; name: string; minutes: number; bookable?: boolean }
function ServicesTab() {
  const [items, setItems] = useState<VT[] | null>(null)
  const [none, setNone] = useState(false)
  useEffect(() => {
    let on = true
    ;(async () => {
      for (const p of ['/visit-types', '/afterhours/settings']) {
        try {
          const r = await api<any>(p, { quiet: true })
          const list: VT[] | undefined = Array.isArray(r) ? r : r.items || r.visit_types
          if (list && on) { setItems(list); return }
        } catch { /* try next */ }
      }
      if (on) setNone(true)
    })()
    return () => { on = false }
  }, [])
  return (
    <div className="gc-sec">
      <h4>Visit types</h4>
      <p className="mute">The visits the clinic offers and how long each takes. Fees come later.</p>
      {!items && !none && <><Skeleton h={40} r={10} /><Skeleton h={40} r={10} /></>}
      {none && <Empty icon="list" title="Visit types are not available to you" text="Ask the owner if you need them here." />}
      {items?.map((v) => <div key={v.id} className="svc"><b>{v.name}</b><span className="tag">{v.minutes} min</span></div>)}
    </div>
  )
}
