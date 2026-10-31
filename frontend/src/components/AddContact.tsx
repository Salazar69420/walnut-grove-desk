import { FormEvent, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import { useApp } from '../ctx'
import { Avatar, KindBadge, Sheet } from './ui'
import { Icon } from './icons'
import { useStaff } from '../lib/store'
import { SOURCE_LABEL, type Row } from '../lib/types'

type PetIn = { name: string; species: string; breed: string }
const SPECIES = ['dog', 'cat', 'rabbit', 'bird', 'reptile', 'small mammal', 'other']
const HOW = ['Said yes in person', 'Said yes on the phone', 'Signed a form', 'Ticked a box on our website', 'Replied YES by text', 'Other']
const CHOICES: [string, string, string][] = [
  ['full', 'Yes, all messages', 'Reminders, news and offers.'],
  ['transactional', 'Care messages only', 'Appointment and care reminders. Nothing promotional.'],
  ['none', 'No, do not message', 'We will not text or email them.'],
]
const digits = (s: string) => s.replace(/\D/g, '')

export function AddContact({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated?: (id: number) => void }) {
  const { me, toast } = useApp()
  const nav = useNavigate()
  const staff = useStaff()
  const blank = { name: '', kind: 'inquiry', phone: '', email: '', ec: '', sc: '', how: HOW[0], source: 'phone', owner: '', notes: '' }
  const [f, setF] = useState(blank)
  const [pets, setPets] = useState<PetIn[]>([])
  const [dupe, setDupe] = useState<Row | null>(null)
  const [dupeId, setDupeId] = useState<number | null>(null)
  const [err, setErr] = useState('')
  const [tried, setTried] = useState(false)
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (open) { setF({ ...blank, owner: me ? String(me.user.id) : '' }); setPets([]); setDupe(null); setDupeId(null); setErr(''); setTried(false) } }, [open]) // eslint-disable-line react-hooks/exhaustive-deps
  const up = (k: keyof typeof blank, v: string) => setF((x) => ({ ...x, [k]: v }))
  const hasPhone = digits(f.phone).length > 0
  const hasEmail = f.email.trim().length > 0

  async function check(kind: 'phone' | 'email') {
    const v = kind === 'phone' ? f.phone.trim() : f.email.trim().toLowerCase()
    if ((kind === 'phone' && digits(v).length < 7) || (kind === 'email' && !v.includes('@'))) return
    try {
      const r = await api<{ items: Row[] }>('/contacts?per_page=10&q=' + encodeURIComponent(kind === 'phone' ? digits(v) : v), { quiet: true })
      const hit = r.items.find((x) => kind === 'phone' ? digits(x.phone || '').slice(-10) === digits(v).slice(-10) : (x.email || '').toLowerCase() === v)
      setDupe(hit || null)
    } catch { /* ignore */ }
  }
  async function submit(e: FormEvent) {
    e.preventDefault()
    setTried(true); setErr('')
    if (f.name.trim().length < 2) { setErr('Enter a name with at least 2 letters.'); return }
    if (hasPhone && !f.sc) { setErr('Choose whether we may text this person.'); return }
    if (hasEmail && !f.ec) { setErr('Choose whether we may email this person.'); return }
    if (pets.some((p) => !p.name.trim())) { setErr('Give each pet a name, or remove the empty row.'); return }
    setBusy(true)
    const consented = (hasPhone && f.sc !== 'none') || (hasEmail && f.ec !== 'none')
    try {
      const r = await api<{ contact?: { id: number }; item?: { id: number }; id?: number }>('/contacts', { quiet: true, body: {
        display_name: f.name.trim(), kind: f.kind,
        email: hasEmail ? f.email.trim() : undefined, phone: hasPhone ? f.phone.trim() : undefined,
        email_consent: hasEmail ? f.ec : 'none', sms_consent: hasPhone ? f.sc : 'none',
        consent_source: consented ? f.how : undefined, source: f.source,
        owner_id: f.owner ? Number(f.owner) : undefined, preference_notes: f.notes.trim() || undefined,
        pets: pets.filter((p) => p.name.trim()).map((p) => ({ name: p.name.trim(), species: p.species || 'dog', breed: p.breed.trim() || undefined })),
      } })
      const id = r.contact?.id ?? r.item?.id ?? r.id
      toast(`Added ${f.name.trim()}`)
      onClose()
      if (id) { onCreated?.(id); nav(`/contacts/${id}`) }
    } catch (x) {
      if (x instanceof ApiError && x.status === 409) {
        setErr(x.message || 'A contact with this phone or email already exists.')
        const id = x.data?.existing_id as number | undefined
        if (id) {
          setDupeId(id)
          if (!dupe) { try { const l = await api<{ items: Row[] }>('/contacts?per_page=10&q=' + encodeURIComponent(hasPhone ? digits(f.phone) : f.email.trim()), { quiet: true }); setDupe(l.items.find((r) => r.id === id) || null) } catch { /* */ } }
        }
      } else if (x instanceof ApiError) setErr(x.message && x.message !== 'error' ? x.message : 'Could not add this contact. Check the details and try again.')
      else setErr('Could not add this contact.')
    }
    setBusy(false)
  }
  const dupId = dupe?.id ?? dupeId
  return (
    <Sheet open={open} onClose={onClose} title="Add contact" footer={
      <>
        <button className="btn" type="button" onClick={onClose}>Cancel</button>
        <button className="btn primary" type="submit" form="addc" disabled={busy || !!dupe}>{busy ? 'Adding' : 'Add contact'}</button>
      </>
    }>
      <form id="addc" className="addc" onSubmit={submit} noValidate>
        {err && <div className="err" role="alert">{err}</div>}
        {dupId && (
          <div className="dupe" role="alert">
            <b>This person is already in Contacts.</b>
            {dupe ? <div className="dupe-c"><Avatar name={dupe.display_name} initials={dupe.initials} hue={dupe.avatar_hue} size={36} /><div><b>{dupe.display_name}</b> <KindBadge kind={dupe.kind} /><div className="mute">{dupe.phone_display || dupe.email}</div></div></div> : null}
            <button type="button" className="btn small" onClick={() => { onClose(); nav(`/contacts/${dupId}`) }}>Open the existing contact</button>
          </div>
        )}
        <label className="f">Name<input data-autofocus value={f.name} onChange={(e) => up('name', e.target.value)} maxLength={120} autoComplete="off" aria-invalid={tried && f.name.trim().length < 2} /></label>
        <fieldset className="fs"><legend>Is this person</legend>
          <div className="radios row2">
            {[['inquiry', 'An inquiry', 'Has not visited yet'], ['patient', 'A patient', 'Has visited before']].map(([v, l, s]) => (
              <label key={v} className={'rad' + (f.kind === v ? ' on' : '')}><input type="radio" name="kind" checked={f.kind === v} onChange={() => up('kind', v)} /><span><b>{l}</b><small>{s}</small></span></label>
            ))}
          </div>
        </fieldset>
        <div className="two">
          <label className="f">Phone<input value={f.phone} onChange={(e) => { up('phone', e.target.value); setDupe(null); setDupeId(null) }} onBlur={() => check('phone')} inputMode="tel" placeholder="604 555 0123" autoComplete="off" /></label>
          <label className="f">Email<input value={f.email} onChange={(e) => { up('email', e.target.value); setDupe(null); setDupeId(null) }} onBlur={() => check('email')} inputMode="email" placeholder="name@example.com" autoComplete="off" /></label>
        </div>

        <div className="consent">
          <h3>Do they agree to be messaged?</h3>
          <p className="mute">Canadian anti-spam law says we may only message people who agreed. Ask them, then record the answer here. You can change it later.</p>
          {!hasPhone && !hasEmail && <p className="mute"><i>Add a phone or email to record consent.</i></p>}
          {hasPhone && <Consent label="Text messages" value={f.sc} onChange={(v) => up('sc', v)} bad={tried && !f.sc} />}
          {hasEmail && <Consent label="Email" value={f.ec} onChange={(v) => up('ec', v)} bad={tried && !f.ec} />}
          {((hasPhone && f.sc && f.sc !== 'none') || (hasEmail && f.ec && f.ec !== 'none')) && (
            <label className="f">How did they agree?<select value={f.how} onChange={(e) => up('how', e.target.value)}>{HOW.map((h) => <option key={h}>{h}</option>)}</select></label>
          )}
        </div>

        <div className="two">
          <label className="f">How did they find us?<select value={f.source} onChange={(e) => up('source', e.target.value)}>{Object.entries(SOURCE_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
          <label className="f">Owner<select value={f.owner} onChange={(e) => up('owner', e.target.value)}><option value="">Unassigned</option>{staff.map((s) => <option key={s.id} value={s.id}>{s.display_name}</option>)}</select></label>
        </div>
        <div>
          <div className="pets-h"><b>Pets</b><button type="button" className="btn small" onClick={() => setPets([...pets, { name: '', species: 'dog', breed: '' }])}><Icon n="plus" size={14} /> Add a pet</button></div>
          {pets.map((p, i) => (
            <div className="petrow" key={i}>
              <input aria-label={`Pet ${i + 1} name`} placeholder="Name" value={p.name} onChange={(e) => setPets(pets.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} maxLength={60} />
              <select aria-label={`Pet ${i + 1} type`} value={p.species} onChange={(e) => setPets(pets.map((x, j) => j === i ? { ...x, species: e.target.value } : x))}>{SPECIES.map((s) => <option key={s}>{s}</option>)}</select>
              <input aria-label={`Pet ${i + 1} breed`} placeholder="Breed (optional)" value={p.breed} onChange={(e) => setPets(pets.map((x, j) => j === i ? { ...x, breed: e.target.value } : x))} maxLength={60} />
              <button type="button" className="ibtn" aria-label={`Remove pet ${i + 1}`} onClick={() => setPets(pets.filter((_, j) => j !== i))}><Icon n="x" size={16} /></button>
            </div>
          ))}
        </div>
        <label className="f">Notes (optional)<textarea value={f.notes} onChange={(e) => up('notes', e.target.value)} maxLength={2000} rows={3} placeholder="Anything the team should know" /></label>
      </form>
    </Sheet>
  )
}

function Consent({ label, value, onChange, bad }: { label: string; value: string; onChange: (v: string) => void; bad: boolean }) {
  return (
    <fieldset className={'fs' + (bad ? ' bad' : '')}><legend>{label}</legend>
      <div className="radios">
        {CHOICES.map(([v, l, s]) => (
          <label key={v} className={'rad' + (value === v ? ' on' : '')}><input type="radio" name={'c-' + label} checked={value === v} onChange={() => onChange(v)} /><span><b>{l}</b><small>{s}</small></span></label>
        ))}
      </div>
      {bad && <p className="errt">Please choose one.</p>}
    </fieldset>
  )
}
