import { ChangeEvent, DragEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import { useApp } from '../ctx'
import { Icon } from './icons'
import { Pop } from './ui'
import type { TL } from './Timeline'
import { useStaff } from '../lib/store'
import { bytes, exact, firstName, fromLocalInput, toLocalInput } from '../lib/fmt'
import type { Row, TItem } from '../lib/types'

type Mode = 'sms' | 'email' | 'note'
type Att = { key: string; id?: number; name: string; bytes: number; state: 'up' | 'ok' | 'err' }
const OK_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'application/pdf']
const MAX = 5 * 1024 * 1024
const bad = (v: string) => v === 'none' || v === 'unsubscribed'

type Sug = { kind: 'answer' | 'emergency' | 'medical' | 'unknown'; draft: string; note: string; sources: { id: number; title: string; kind: string }[]; task: { id: number; title: string } | null }

function soon(h: number) { const d = new Date(Date.now() + h * 3600000); d.setSeconds(0, 0); return d }
function tomorrow9(days = 1) { const d = new Date(); d.setDate(d.getDate() + days); d.setHours(9, 0, 0, 0); return d }

export function Composer({ contact, tl, onEditConsent }: { contact: Row; tl: TL; onEditConsent: () => void }) {
  const { me, toast } = useApp()
  const staff = useStaff()
  const [mode, setMode] = useState<Mode>('sms')
  const [body, setBody] = useState('')
  const [subject, setSubject] = useState('')
  const [atts, setAtts] = useState<Att[]>([])
  const [sched, setSched] = useState('')
  const [drag, setDrag] = useState(false)
  const [sending, setSending] = useState(false)
  const [sug, setSug] = useState<Sug | null>(null)
  const [suggesting, setSuggesting] = useState(false)
  const [askText, setAskText] = useState<string | null>(null)
  const [mq, setMq] = useState<{ at: number; q: string } | null>(null)
  const [mi, setMi] = useState(0)
  const ta = useRef<HTMLTextAreaElement>(null)
  const file = useRef<HTMLInputElement>(null)
  const fn = firstName(contact.display_name)

  // pick a sensible starting tab for each person
  useEffect(() => {
    const lm = contact.last_message?.channel
    const smsOk = !!contact.phone && !bad(contact.sms_consent)
    const emOk = !!contact.email && !bad(contact.email_consent)
    setMode(lm === 'email' && emOk ? 'email' : smsOk ? 'sms' : emOk ? 'email' : 'sms')
    setBody(''); setSubject(''); setAtts([]); setSched('')
  }, [contact.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const chan = mode === 'sms' ? { has: !!contact.phone, consent: contact.sms_consent, thing: 'phone number', verb: 'text' } : { has: !!contact.email, consent: contact.email_consent, thing: 'email address', verb: 'email' }
  const blocked = mode !== 'note' && (!chan.has || bad(chan.consent))
  const why = !chan.has ? `${fn} has no ${chan.thing} on file, so you cannot ${chan.verb} them.` : chan.consent === 'unsubscribed' ? `${fn} unsubscribed from ${chan.verb === 'text' ? 'texts' : 'emails'}. You cannot ${chan.verb} them.` : chan.verb === 'text' ? `${fn} is not opted in to texts yet. When they text the clinic, they are asked to reply Y, and you can text them once they do.` : `${fn} has not agreed to emails. You cannot email them until they do.`
  const len = body.length
  const segs = Math.ceil(len / 160)
  const uploading = atts.some((a) => a.state === 'up')
  const tooBig = len > 2000
  const schedISO = fromLocalInput(sched)
  const empty = !body.trim()
  async function suggest(typed?: string) {
    setSuggesting(true)
    try {
      const r = await api<Sug>(`/contacts/${contact.id}/suggest-reply`, { method: 'POST', body: typed ? { text: typed } : {}, quiet: true })
      setSug(r); setAskText(null)
      if (!body.trim() && mode !== 'note') { setBody(r.draft); window.setTimeout(() => ta.current?.focus(), 0) }
    } catch (x) {
      if (x instanceof ApiError && x.status === 409 && /no message/i.test(x.message)) setAskText(typed ?? '')
      else toast(x instanceof ApiError && x.message && x.message !== 'error' ? x.message : 'Could not get a suggestion. Try again.')
    }
    setSuggesting(false)
  }
  const smsMode = me?.settings.sms_mode || 'practice'
  const realText = mode === 'sms' && !!contact.phone && (smsMode === 'live' || (smsMode === 'test_numbers_only' && (me?.settings.sms_test_numbers || []).includes(contact.phone)))
  const smsLine = realText ? `This text is really sent through TELUS to ${contact.phone_display || contact.phone}.`
    : smsMode === 'test_numbers_only' ? 'Test mode: only the test numbers get real texts. This one is practice.' : 'Practice mode: nothing is really sent.'
  const canSend = !blocked && !empty && !tooBig && !sending && !uploading && !(mode === 'email' && subject.length > 150)
  const hint = /Mac|iPhone|iPad/.test(navigator.platform) ? 'Cmd Enter' : 'Ctrl Enter'

  async function upload(files: File[]) {
    if (mode === 'note') { toast('Attachments go with texts and emails, not notes.'); return }
    for (const f of files) {
      const key = Math.random().toString(36).slice(2)
      if (!OK_TYPES.includes(f.type)) { toast(`${f.name} was not added. Use a JPEG, PNG, WEBP or PDF.`); continue }
      if (f.size > MAX) { toast(`${f.name} is over 5 MB.`); continue }
      setAtts((a) => [...a, { key, name: f.name, bytes: f.size, state: 'up' }])
      const form = new FormData()
      form.append('file', f)
      try {
        const r = await api<{ id: number }>(`/contacts/${contact.id}/attachments`, { form })
        setAtts((a) => a.map((x) => x.key === key ? { ...x, id: r.id, state: 'ok' } : x))
      } catch { setAtts((a) => a.filter((x) => x.key !== key)) }
    }
  }
  function onDrop(e: DragEvent) {
    e.preventDefault(); setDrag(false)
    if (e.dataTransfer.files.length) upload([...e.dataTransfer.files])
  }

  async function send() {
    if (!canSend) return
    const text = body.trim()
    const subj = subject.trim()
    const tmp: TItem = {
      id: 'tmp' + Date.now(), type: mode === 'note' ? 'note' : 'message', channel: mode === 'note' ? null : mode, direction: 'out',
      sender: { kind: 'staff', name: me?.user.display_name || 'You', initials: me?.user.initials || '' }, subject: mode === 'email' ? subj : null, body: text,
      at: new Date().toISOString(), delivery_state: null, scheduled_for: schedISO, attachments: atts.filter((a) => a.id).map((a) => ({ id: a.id!, filename: a.name, mime: '', bytes: a.bytes })), mentions: [], pending: true,
    }
    const keep = { body, subject, atts, sched }
    tl.setPending((p) => [...p, tmp])
    setBody(''); setSubject(''); setAtts([]); setSched(''); setSending(true)
    try {
      if (mode === 'note') {
        const ids = staff.filter((s) => new RegExp(`@(?:${[s.display_name, firstName(s.display_name)].map((x) => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})(?![\\p{L}])`, 'iu').test(text)).map((s) => s.id)
        await api(`/contacts/${contact.id}/notes`, { quiet: true, body: { body: text, mention_ids: ids } })
        toast(ids.length ? 'Note saved. Your colleagues were told.' : 'Note saved')
      } else {
        await api(`/contacts/${contact.id}/messages`, { quiet: true, body: {
          channel: mode, subject: mode === 'email' ? subj || undefined : undefined, body: text,
          scheduled_for: schedISO || undefined, attachment_ids: keep.atts.filter((a) => a.id).map((a) => a.id),
        } })
        toast(schedISO ? `Scheduled for ${exact(schedISO)} (practice, nothing is sent)` : mode === 'sms' ? 'Text logged. Practice mode, nothing was really sent.' : 'Email logged. Practice mode, nothing was really sent.')
      }
      await tl.refresh()
      tl.setPending((p) => p.filter((x) => x.id !== tmp.id))
    } catch (x) {
      tl.setPending((p) => p.filter((y) => y.id !== tmp.id))
      setBody(keep.body); setSubject(keep.subject); setAtts(keep.atts); setSched(keep.sched)
      toast(x instanceof ApiError && x.message && x.message !== 'error' && x.message !== 'network' ? x.message : 'That was not sent. Your message is still here.')
    }
    setSending(false)
    ta.current?.focus()
  }
  function key(e: KeyboardEvent) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); send(); return }
    if (mq && matches.length) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setMi((i) => (i + 1) % matches.length) }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setMi((i) => (i - 1 + matches.length) % matches.length) }
      else if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pickMention(matches[mi]) }
      else if (e.key === 'Escape') { e.preventDefault(); setMq(null) }
    }
  }
  function onBody(e: ChangeEvent<HTMLTextAreaElement>) {
    setBody(e.target.value)
    if (mode !== 'note') { setMq(null); return }
    const pos = e.target.selectionStart
    const m = /(^|\s)@([\p{L}'-]*)$/u.exec(e.target.value.slice(0, pos))
    if (m) { setMq({ at: pos - m[2].length - 1, q: m[2].toLowerCase() }); setMi(0) } else setMq(null)
  }
  const matches = useMemo(() => !mq ? [] : staff.filter((s) => s.id !== me?.user.id || true).filter((s) => !mq.q || s.display_name.toLowerCase().split(/\s+/).some((w) => w.startsWith(mq.q))).slice(0, 5), [mq, staff, me])
  function pickMention(s: { display_name: string }) {
    if (!mq) return
    const el = ta.current
    const end = el ? el.selectionStart : body.length
    const ins = '@' + firstName(s.display_name) + ' '
    const next = body.slice(0, mq.at) + ins + body.slice(end)
    setBody(next); setMq(null)
    requestAnimationFrame(() => { el?.focus(); const p = mq.at + ins.length; el?.setSelectionRange(p, p) })
  }

  return (
    <div className={'comp' + (drag ? ' drag' : '') + (mode === 'note' ? ' isnote' : '')} onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={(e) => { if (e.currentTarget === e.target) setDrag(false) }} onDrop={onDrop}>
      <div className="comp-top">
        <div className="seg2" role="tablist" aria-label="What to write">
          {([['sms', 'Text'], ['email', 'Email'], ['note', 'Internal note']] as [Mode, string][]).map(([k, l]) => (
            <button key={k} role="tab" aria-selected={mode === k} aria-pressed={mode === k} onClick={() => { setMode(k); setMq(null) }} className={k === 'note' ? 'note-tab' : ''}>{l}</button>
          ))}
        </div>
        <span className="spacer" />
        <ReminderPop contact={contact} />
      </div>
      {blocked && (
        <div className="warnbox2" role="alert"><Icon n="alert" size={16} /><span>{why}</span><button className="btn small" onClick={onEditConsent}>Check consent</button></div>
      )}
      {mode === 'email' && <input className="subj" value={subject} onChange={(e) => setSubject(e.target.value)} onKeyDown={key} placeholder="Subject" maxLength={150} aria-label="Email subject" disabled={blocked} />}
      <div className="ta-wrap">
        <textarea ref={ta} value={body} onChange={onBody} onKeyDown={key} disabled={blocked} rows={3}
          placeholder={mode === 'note' ? `Write a note for your team about ${fn}. Type @ to tell a colleague.` : mode === 'sms' ? `Text ${fn}` : `Write an email to ${fn}`}
          aria-label={mode === 'note' ? 'Internal note' : mode === 'sms' ? 'Text message' : 'Email message'} aria-invalid={tooBig} />
        {mq && matches.length > 0 && (
          <div className="mention-list" role="listbox" aria-label="Colleagues">
            {matches.map((s, i) => <button key={s.id} role="option" aria-selected={i === mi} className={i === mi ? 'on' : ''} onMouseDown={(e) => { e.preventDefault(); pickMention(s) }}><span className="av2 sm" style={{ ['--h' as any]: (s.id * 67 + 23) % 360 }}>{s.initials}</span>{s.display_name}<small>{s.role}</small></button>)}
          </div>
        )}
      </div>
      {askText !== null && (
        <div className="sugg ask"><label>What did {fn} say? Type it and the assistant will draft a reply.
          <textarea value={askText} onChange={(e) => setAskText(e.target.value)} rows={2} maxLength={1500} placeholder={`For example: "Do you take walk-ins on Saturday?"`} /></label>
          <div className="rowbtn"><button className="btn small" onClick={() => setAskText(null)}>Cancel</button><button className="btn small primary" disabled={askText.trim().length < 2 || suggesting} onClick={() => suggest(askText.trim())}>Suggest a reply</button></div></div>
      )}
      {sug && (
        <div className={'sugg k-' + sug.kind} role="status">
          <div className="sg-h"><Icon n="sparkle" size={15} /><b>{sug.kind === 'emergency' ? 'Possible emergency' : sug.kind === 'medical' ? 'Medical question, no advice given' : sug.kind === 'unknown' ? 'No approved answer yet' : 'Suggested reply'}</b><span className="spacer" />
            <button className="ibtn sm" aria-label="Dismiss the suggestion" onClick={() => setSug(null)}><Icon n="x" size={14} /></button></div>
          <p>{sug.note}</p>
          {sug.sources.length > 0 && <p className="small mute">From approved knowledge: {sug.sources.map((x) => x.title).join(', ')}</p>}
          {sug.task && <p className="small"><b>Task raised:</b> {sug.task.title}</p>}
          {body.trim() !== sug.draft && <div className="rowbtn"><button className="btn small" onClick={() => { setBody(sug.draft); ta.current?.focus() }}>Use this draft</button></div>}
          <p className="small mute">A draft only. Nothing is sent until you press Send.</p>
        </div>
      )}
      {(atts.length > 0 || schedISO) && (
        <div className="comp-chips">
          {atts.map((a) => (
            <span key={a.key} className={'achip ' + a.state}><Icon n="clip" size={14} /><span className="ell">{a.name}</span><small>{a.state === 'up' ? 'Uploading' : bytes(a.bytes)}</small><button onClick={() => setAtts(atts.filter((x) => x.key !== a.key))} aria-label={`Remove ${a.name}`}><Icon n="x" size={12} /></button></span>
          ))}
          {schedISO && <span className="achip sched"><Icon n="clock" size={14} />Sends {exact(schedISO)}<button onClick={() => setSched('')} aria-label="Cancel scheduled send"><Icon n="x" size={12} /></button></span>}
        </div>
      )}
      <div className="comp-bar">
        {mode !== 'note' && (
          <>
            <input ref={file} type="file" hidden multiple accept="image/jpeg,image/png,image/webp,application/pdf" onChange={(e) => { if (e.target.files) upload([...e.target.files]); e.target.value = '' }} />
            <button className="btn ghost" onClick={() => file.current?.click()} disabled={blocked}><Icon n="clip" size={16} /> Attach</button>
            <SchedulePop value={sched} onChange={setSched} disabled={blocked} />
            <button className="btn ghost" onClick={() => suggest()} disabled={suggesting || !contact.ai_enabled} title={contact.ai_enabled ? 'Draft a reply from approved clinic knowledge' : 'Turn on the AI assistant for this person to get suggestions'}><Icon n="sparkle" size={16} /> {suggesting ? 'Thinking' : 'Suggest reply'}</button>
          </>
        )}
        <span className="spacer" />
        <span className={'counter' + (tooBig ? ' over' : '')} aria-live="off">{mode === 'sms' ? `${len}/2000 · ${segs} text${segs === 1 ? '' : 's'}` : `${len}/2000`}</span>
        <button className="btn primary send-b" onClick={send} disabled={!canSend} title={`${hint} to send`}>
          {mode === 'note' ? 'Add note' : schedISO ? 'Schedule' : 'Send'} <kbd>{hint}</kbd>
        </button>
      </div>
      <p className={'practice' + (realText ? ' real' : '')}>{mode === 'note' ? 'Only your team can see notes. Type @ to tell a colleague.' : mode === 'email' ? 'Practice mode: emails are not really sent.' : smsLine}</p>
      {drag && <div className="dropv"><Icon n="clip" size={22} /> Drop files to attach them</div>}
    </div>
  )
}

function SchedulePop({ value, onChange, disabled }: { value: string; onChange: (v: string) => void; disabled?: boolean }) {
  const [v, setV] = useState(value)
  const [err, setErr] = useState('')
  const min = toLocalInput(new Date(Date.now() + 60000))
  return (
    <Pop className="up" button={({ open, toggle }) => <button className="btn ghost" onClick={() => { setV(value || toLocalInput(soon(1))); toggle() }} disabled={disabled} aria-expanded={open}><Icon n="clock" size={16} /> Schedule</button>}>
      {(close) => (
        <form className="pf wide" onSubmit={(e) => { e.preventDefault(); const d = fromLocalInput(v); if (!d || new Date(d).getTime() < Date.now() + 30000) { setErr('Pick a time in the future.'); return } onChange(v); close() }}>
          <b>Send later</b>
          <div className="quick2">
            <button type="button" onClick={() => setV(toLocalInput(soon(1)))}>In 1 hour</button>
            <button type="button" onClick={() => setV(toLocalInput(tomorrow9()))}>Tomorrow 9 am</button>
            <button type="button" onClick={() => setV(toLocalInput(tomorrow9(3)))}>In 3 days</button>
          </div>
          <label className="f">Date and time<input type="datetime-local" min={min} value={v} onChange={(e) => { setV(e.target.value); setErr('') }} data-autofocus /></label>
          {err && <p className="errt">{err}</p>}
          <div className="rowbtn"><button className="btn primary small" type="submit">Set time</button>{value && <button type="button" className="btn small" onClick={() => { onChange(''); close() }}>Send now instead</button>}</div>
        </form>
      )}
    </Pop>
  )
}

function ReminderPop({ contact }: { contact: Row }) {
  const { toast, me } = useApp()
  const [v, setV] = useState('')
  const [t, setT] = useState('')
  const [busy, setBusy] = useState(false)
  return (
    <Pop align="right" className="up" button={({ open, toggle }) => <button className="btn ghost" onClick={() => { setV(toLocalInput(tomorrow9())); setT(`Follow up with ${contact.display_name}`); toggle() }} aria-expanded={open}><Icon n="bell" size={16} /> Add reminder</button>}>
      {(close) => (
        <form className="pf wide" onSubmit={async (e) => {
          e.preventDefault()
          const d = fromLocalInput(v)
          if (!d || !t.trim()) return
          setBusy(true)
          try { await api(`/contacts/${contact.id}/reminders`, { body: { due_at: d, text: t.trim().slice(0, 200), owner_id: me?.user.id } }); toast(`Reminder set for ${exact(d)}`); close() } catch { /* toast shown */ }
          setBusy(false)
        }}>
          <b>Remind me</b>
          <div className="quick2">
            <button type="button" onClick={() => setV(toLocalInput(soon(1)))}>In 1 hour</button>
            <button type="button" onClick={() => setV(toLocalInput(tomorrow9()))}>Tomorrow 9 am</button>
            <button type="button" onClick={() => setV(toLocalInput(tomorrow9(7)))}>In a week</button>
          </div>
          <label className="f">When<input type="datetime-local" value={v} onChange={(e) => setV(e.target.value)} data-autofocus /></label>
          <label className="f">What for<input value={t} onChange={(e) => setT(e.target.value)} maxLength={200} /></label>
          <button className="btn primary small" type="submit" disabled={busy || !v || !t.trim()}>Set reminder</button>
        </form>
      )}
    </Pop>
  )
}
