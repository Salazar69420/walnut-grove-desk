import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import { useApp } from '../ctx'
import { useLive } from '../live'
import { Avatar, Empty, KindBadge, Segmented, Sheet, SkeletonRows, Time, useMedia } from '../components/ui'
import { Icon } from '../components/icons'
import { GuestCard, type CardTab } from '../components/GuestCard'
import { Item } from '../components/Timeline'
import { useStaff } from '../lib/store'
import { exact, fromLocalInput, hueFor, toLocalInput } from '../lib/fmt'
import { TASK_KINDS, type Row, type Task, type TItem } from '../lib/types'

type Data = { items: Task[]; counts: { open: number; in_progress: number; mine: number; unassigned: number } }
const STATUS: [string, string][] = [['open', 'Open'], ['in_progress', 'In progress'], ['done', 'Done']]
const PRI: Record<string, string> = { high: 'High', normal: 'Normal', low: 'Low' }
const errText = (x: unknown) => x instanceof ApiError && x.message && x.message !== 'error' && x.message !== 'network' ? x.message : ''

export function Tasks() {
  const { can, toast } = useApp()
  const staff = useStaff()
  const [sp, setSp] = useSearchParams()
  const narrow = useMedia('(max-width: 780px)')
  const wide = useMedia('(min-width: 1281px)')
  const status = sp.get('status') || 'open'
  const cls = sp.get('class') || ''
  const kind = sp.get('kind') || ''
  const assigned = sp.get('assigned') || 'all'
  const q = sp.get('q') || ''
  const openId = Number(sp.get('open')) || 0
  const [qText, setQText] = useState(q)
  const [cardTab, setCardTab] = useState<CardTab>('contact')
  const [creating, setCreating] = useState(sp.get('new') === '1')
  const [cache, setCache] = useState<Task | null>(null)

  const set = useCallback((patch: Record<string, string | null>) => setSp((p) => {
    const n = new URLSearchParams(p)
    for (const [k, v] of Object.entries(patch)) { if (v === null || v === '') n.delete(k); else n.set(k, v) }
    return n
  }, { replace: true }), [setSp])
  useEffect(() => { const t = window.setTimeout(() => { if (qText.trim() !== q) set({ q: qText.trim() }) }, 250); return () => clearTimeout(t) }, [qText]) // eslint-disable-line react-hooks/exhaustive-deps

  const qs = new URLSearchParams({ status, assigned })
  if (cls) qs.set('classification', cls)
  if (kind) qs.set('kind', kind)
  if (q) qs.set('q', q)
  const list = useLive<Data>('/tasks?' + qs.toString(), ['tasks'])
  const items = list.data?.items || []
  const counts = list.data?.counts
  const fromList = items.find((t) => t.id === openId) || null
  const sel = fromList || (cache && cache.id === openId ? cache : null)
  useEffect(() => { if (fromList) setCache(fromList) }, [fromList])

  // a link like /tasks?open=7 for a task the filters hide: find it and show its tab
  const looked = useRef(0)
  useEffect(() => {
    if (!openId || !list.data || fromList || looked.current === openId) return
    looked.current = openId
    api<Data>('/tasks?status=all&assigned=all', { quiet: true }).then((r) => {
      const t = r.items.find((x) => x.id === openId)
      if (t) { setCache(t); set({ status: t.status, assigned: 'all', class: null, kind: null, q: null }); setQText('') }
    }).catch(() => {})
  }, [openId, list.data, fromList, set])

  function patchLocal(id: number, p: Partial<Task>) {
    setCache((c) => c && c.id === id ? { ...c, ...p } : c)
    list.setData((d) => d && ({ ...d, items: d.items.map((t) => t.id === id ? { ...t, ...p } : t).filter((t) => !('status' in p) || status === 'all' || status === 'active' || t.status === status) } as Data) as any)
  }
  // update with rollback; returns true when saved
  async function update(t: Task, body: Record<string, unknown>, local: Partial<Task>, msg?: string, undoBody?: Record<string, unknown>, undoLocal?: Partial<Task>) {
    const before = { ...t }
    patchLocal(t.id, local)
    try {
      await api(`/tasks/${t.id}`, { method: 'PATCH', body: { ...body, expected_status: t.status }, quiet: true })
      if (msg) toast(msg, undoBody ? async () => {
        patchLocal(t.id, undoLocal || {})
        try { await api(`/tasks/${t.id}`, { method: 'PATCH', body: { ...undoBody, expected_status: local.status || t.status }, quiet: true }); list.reload() } catch (x) { toast(errText(x) || 'Could not undo that.'); list.reload() }
      } : undefined)
      return true
    } catch (x) {
      patchLocal(t.id, before)
      toast(errText(x) || 'That change was not saved.')
      if (x instanceof ApiError && x.status === 409) list.reload()
      return false
    }
  }
  async function take(t: Task) {
    const me = staff.find(() => false)
    void me
    try { await api(`/tasks/${t.id}/take`, { method: 'POST', quiet: true }); toast('It is yours now'); list.reload() } catch (x) { toast(errText(x) || 'Could not take that task.'); list.reload() }
  }

  const showList = !narrow || !openId
  const showDetail = !narrow || !!openId
  const tabLabel = (k: string, l: string) => k === 'open' && counts ? `${l} ${counts.open}` : k === 'in_progress' && counts ? `${l} ${counts.in_progress}` : l

  return (
    <div className={'tasks3' + (openId && !narrow ? ' has' : '')}>
      {showList && (
        <section className="tpane tlist card" aria-label="Task list">
          <div className="tl-top">
            <div className="tl-h"><h1>Tasks</h1><span className="spacer" />{can('tasks.work') && <button className="btn primary small" onClick={() => setCreating(true)}><Icon n="plus" size={14} /> New</button>}</div>
            <div className="tstat" role="tablist" aria-label="Status">
              {STATUS.map(([k, l]) => <button key={k} role="tab" aria-selected={status === k} onClick={() => set({ status: k === 'open' ? null : k })}>{tabLabel(k, l)}</button>)}
            </div>
            <label className="searchf"><Icon n="search" size={16} /><input value={qText} onChange={(e) => setQText(e.target.value)} placeholder="Search tasks" aria-label="Search tasks" maxLength={80} /></label>
            <div className="tfil">
              <select value={assigned} onChange={(e) => set({ assigned: e.target.value === 'all' ? null : e.target.value })} aria-label="Assigned to">
                <option value="all">Everyone</option><option value="me">Me</option><option value="none">Unassigned</option>
                {staff.map((s) => <option key={s.id} value={s.id}>{s.display_name}</option>)}
              </select>
              <select value={cls} onChange={(e) => set({ class: e.target.value })} aria-label="Inquiry or patient"><option value="">Inquiry and patient</option><option value="inquiry">Inquiry</option><option value="patient">Patient</option></select>
              <select value={kind} onChange={(e) => set({ kind: e.target.value })} aria-label="Kind of task"><option value="">All kinds</option>{TASK_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
            </div>
          </div>
          <div className="tl-list">
            {list.loading && !list.data ? <SkeletonRows n={7} h={72} /> : list.error && !list.data ? <p className="sub pad">Could not load tasks. It will try again.</p> : items.length === 0 ? (
              <Empty icon="check" title={status === 'done' ? 'Nothing finished yet' : 'You are all caught up'} text={status === 'done' ? 'Finished tasks show up here.' : q || cls || kind || assigned !== 'all' ? 'No tasks match these filters.' : 'There are no tasks in this list. New ones appear here by themselves.'} />
            ) : items.map((t) => (
              <button key={t.id} className={'trow p-' + t.priority + (t.id === openId ? ' on' : '')} aria-current={t.id === openId ? 'true' : undefined} onClick={() => set({ open: String(t.id) })}>
                <div className="tr-m">
                  <b className="tr-t">{t.title}</b>
                  <div className="tr-s"><span className="tag">{t.kind_label}</span>{t.raised_by === 'ai' && <span className="tag acc">AI</span>}{t.priority === 'high' && <span className="tag high">High</span>}</div>
                  <div className="tr-p">{t.contact ? <><Avatar name={t.contact.display_name} initials={t.contact.initials} hue={t.contact.avatar_hue} size={20} />{t.contact.display_name}</> : <span className="mute">No person linked</span>}<span className="mute">· <Time iso={t.created_at} /></span></div>
                </div>
                <div className="tr-r">{t.assigned ? <Avatar name={t.assigned.display_name} initials={t.assigned.initials} hue={hueFor(t.assigned.id)} size={28} /> : <span className="tag warn">Open</span>}</div>
              </button>
            ))}
          </div>
        </section>
      )}
      {showDetail && (
        <section className="tpane tdet" aria-label="Task details">
          {!sel ? (
            <div className="card dempty"><Empty icon="tasks" title="Pick a task" text="Choose a task on the left to see why it was raised and who it is about." /></div>
          ) : (
            <>
              <Detail t={sel} onBack={narrow ? () => set({ open: null }) : undefined} staff={staff} update={update} take={take} can={can('tasks.work')} />
              {!wide && sel.contact && <div className="card gcwrap"><GuestCard contactId={sel.contact.id} tab={cardTab} onTab={setCardTab} showOpen /></div>}
            </>
          )}
        </section>
      )}
      {wide && showDetail && (
        <aside className="tpane tcard card" aria-label="Guest Card">
          {sel?.contact ? <GuestCard contactId={sel.contact.id} tab={cardTab} onTab={setCardTab} showOpen /> : <Empty icon="user" title="No person on this task" text={sel ? 'This task is not linked to a contact.' : 'The Guest Card shows here.'} />}
        </aside>
      )}
      <NewTask open={creating} onClose={() => { setCreating(false); if (sp.get('new')) set({ new: null }) }} onCreated={(id) => { list.reload(); set({ open: String(id), status: null }) }} />
    </div>
  )
}

function Detail({ t, onBack, staff, update, take, can }: { t: Task; onBack?: () => void; staff: ReturnType<typeof useStaff>; update: (t: Task, body: Record<string, unknown>, local: Partial<Task>, msg?: string, ub?: Record<string, unknown>, ul?: Partial<Task>) => Promise<boolean>; take: (t: Task) => void; can: boolean }) {
  const { me } = useApp()
  const mine = t.assigned?.id === me?.user.id
  const [due, setDue] = useState(t.due_at ? toLocalInput(new Date(t.due_at)) : '')
  useEffect(() => { setDue(t.due_at ? toLocalInput(new Date(t.due_at)) : '') }, [t.id, t.due_at])
  const assign = (v: string) => {
    const s = staff.find((x) => String(x.id) === v)
    update(t, { assigned_to: v ? Number(v) : null }, { assigned: s ? { id: s.id, display_name: s.display_name, initials: s.initials } : null }, s ? `Assigned to ${s.display_name}` : 'Now unassigned')
  }
  const raised = t.raised_by === 'ai' ? 'Raised by AI' : t.raised_by === 'rule' ? 'Raised by a rule' : null
  return (
    <div className="card tdetail">
      {onBack && <button className="btn small back" onClick={onBack}><Icon n="chevL" size={14} /> All tasks</button>}
      <div className={'td-h p-' + t.priority}>
        <div className="td-tags">
          <span className="tag">{t.kind_label}</span>
          <span className={'tag ' + (t.status === 'done' ? 'ok' : t.status === 'in_progress' ? 'acc' : 'warn')}>{t.status === 'in_progress' ? 'In progress' : t.status === 'done' ? 'Done' : 'Open'}</span>
          {t.priority === 'high' && <span className="tag high">High priority</span>}
          {raised && <span className="tag ai"><Icon n="sparkle" size={12} /> {raised}</span>}
          <span className="tag">{t.classification === 'patient' ? 'Patient' : 'Inquiry'}</span>
        </div>
        <h2>{t.title}</h2>
        <p className="mute small">Created <Time iso={t.created_at} />{t.due_at && <> · Due <time title={exact(t.due_at)}>{exact(t.due_at)}</time></>}</p>
      </div>
      <section className="td-s"><h4>Why this task</h4><p>{t.reason || 'No reason was given.'}</p></section>
      {t.contact && (
        <section className="td-s">
          <h4>Who it is about</h4>
          <Link className="td-p" to={`/contacts/${t.contact.id}`}><Avatar name={t.contact.display_name} initials={t.contact.initials} hue={t.contact.avatar_hue} size={40} /><span><b>{t.contact.display_name}</b> <KindBadge kind={t.contact.kind} /><small>Open the conversation</small></span><Icon n="chevR" size={16} /></Link>
        </section>
      )}
      {can && (
        <section className="td-s">
          <h4>What to do</h4>
          <div className="td-acts">
            {t.status !== 'done' && !mine && <button className="btn primary" onClick={() => take(t)}>Take it</button>}
            {t.status === 'open' && <button className="btn" onClick={() => update(t, { status: 'in_progress' }, { status: 'in_progress' }, 'Started', { status: 'open' }, { status: 'open' })}>Start</button>}
            {t.status !== 'done' && <button className="btn okb" onClick={() => update(t, { status: 'done' }, { status: 'done' }, 'Marked done', { status: t.status }, { status: t.status })}><Icon n="check" size={16} /> Done</button>}
            {t.status === 'done' && <button className="btn" onClick={() => update(t, { status: 'open' }, { status: 'open' }, 'Reopened')}>Reopen</button>}
          </div>
          <div className="td-form">
            <label className="f">Assigned to<select value={t.assigned?.id ?? ''} onChange={(e) => assign(e.target.value)}><option value="">Unassigned</option>{staff.map((s) => <option key={s.id} value={s.id}>{s.display_name}</option>)}</select></label>
            <label className="f">Priority<select value={t.priority} onChange={(e) => update(t, { priority: e.target.value }, { priority: e.target.value as Task['priority'] }, `Priority is now ${PRI[e.target.value].toLowerCase()}`)}>{Object.entries(PRI).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
            <label className="f">Due<input type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} onBlur={() => { const iso = fromLocalInput(due); if ((iso || null) !== (t.due_at ? new Date(t.due_at).toISOString() : null)) update(t, { due_at: iso }, { due_at: iso }, iso ? 'Due date set' : 'Due date cleared') }} /></label>
          </div>
        </section>
      )}
      {!can && <p className="mute">You can read this task, but you cannot change it.</p>}
      {t.contact && <Recent id={t.contact.id} name={t.contact.display_name} />}
    </div>
  )
}

function Recent({ id, name }: { id: number; name: string }) {
  const r = useLive<{ items: TItem[] }>(`/contacts/${id}/timeline?limit=5`, ['messages'])
  const items = [...(r.data?.items || [])].reverse()
  const stub = { id, display_name: name, initials: name.slice(0, 2).toUpperCase(), avatar_hue: hueFor(id) } as Row
  return (
    <section className="td-s">
      <div className="gc-sh"><h4>Recent activity</h4><Link className="link-a" to={`/contacts/${id}`}>Open conversation</Link></div>
      {!r.data ? <SkeletonRows n={2} h={44} /> : items.length === 0 ? <p className="mute">No messages yet.</p> : <div className="recent">{items.map((it) => <Item key={`${it.type}-${it.id}`} it={it} contact={stub} />)}</div>}
    </section>
  )
}

function NewTask({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: number) => void }) {
  const { toast } = useApp()
  const staff = useStaff()
  const blank = { title: '', kind: 'other', classification: 'inquiry', reason: '', assigned_to: '', priority: 'normal', due: '' }
  const [f, setF] = useState(blank)
  const [q, setQ] = useState('')
  const [res, setRes] = useState<Row[]>([])
  const [pick, setPick] = useState<Row | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const seq = useRef(0)
  useEffect(() => { if (open) { setF(blank); setQ(''); setRes([]); setPick(null); setErr('') } }, [open]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (q.trim().length < 2) { setRes([]); return }
    const my = ++seq.current
    const t = window.setTimeout(async () => { try { const r = await api<{ items: Row[] }>('/contacts?per_page=6&q=' + encodeURIComponent(q.trim()), { quiet: true }); if (my === seq.current) setRes(r.items) } catch { /* */ } }, 220)
    return () => clearTimeout(t)
  }, [q])
  const up = (k: string, v: string) => setF((x) => ({ ...x, [k]: v }))
  async function go(e: FormEvent) {
    e.preventDefault()
    if (f.title.trim().length < 2) { setErr('Give the task a short title.'); return }
    setBusy(true); setErr('')
    try {
      const r = await api<{ item?: { id: number }; id?: number }>('/tasks', { quiet: true, body: {
        kind: f.kind, classification: pick ? pick.kind : f.classification, title: f.title.trim(), reason: f.reason.trim() || undefined,
        contact_id: pick?.id, assigned_to: f.assigned_to ? Number(f.assigned_to) : undefined, priority: f.priority, due_at: fromLocalInput(f.due) || undefined } })
      toast('Task added'); onClose(); const id = r.item?.id ?? r.id; if (id) onCreated(id)
    } catch (x) { setErr(errText(x) || 'Could not add the task.') }
    setBusy(false)
  }
  return (
    <Sheet open={open} onClose={onClose} title="New task" footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" type="submit" form="newtask" disabled={busy}>Add task</button></>}>
      <form id="newtask" className="addc" onSubmit={go}>
        {err && <div className="err" role="alert">{err}</div>}
        <label className="f">What needs doing?<input data-autofocus value={f.title} onChange={(e) => up('title', e.target.value)} maxLength={150} /></label>
        <label className="f">Why (optional)<textarea rows={3} value={f.reason} onChange={(e) => up('reason', e.target.value)} maxLength={1000} /></label>
        <div className="two">
          <label className="f">Kind<select value={f.kind} onChange={(e) => up('kind', e.target.value)}>{TASK_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
          <label className="f">Priority<select value={f.priority} onChange={(e) => up('priority', e.target.value)}>{Object.entries(PRI).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        </div>
        <div className="f">Who is it about?
          {pick ? <div className="lk-res on"><Avatar name={pick.display_name} initials={pick.initials} hue={pick.avatar_hue} size={28} /><span><b>{pick.display_name}</b></span><button type="button" className="ibtn sm" onClick={() => setPick(null)} aria-label="Choose someone else"><Icon n="x" size={14} /></button></div> : (
            <>
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by name, phone or pet" aria-label="Search for a person" />
              {res.map((r) => <button type="button" key={r.id} className="lk-res" onClick={() => setPick(r)}><Avatar name={r.display_name} initials={r.initials} hue={r.avatar_hue} size={28} /><span><b>{r.display_name}</b><small>{r.phone_display || r.email || ''}</small></span></button>)}
            </>
          )}
        </div>
        {!pick && <div className="f">Is it for<Segmented label="Inquiry or patient" value={f.classification as 'inquiry' | 'patient'} onChange={(v) => up('classification', v)} options={[['inquiry', 'An inquiry'], ['patient', 'A patient']]} /></div>}
        <div className="two">
          <label className="f">Assign to<select value={f.assigned_to} onChange={(e) => up('assigned_to', e.target.value)}><option value="">Nobody yet</option>{staff.map((s) => <option key={s.id} value={s.id}>{s.display_name}</option>)}</select></label>
          <label className="f">Due (optional)<input type="datetime-local" value={f.due} onChange={(e) => up('due', e.target.value)} /></label>
        </div>
      </form>
    </Sheet>
  )
}
