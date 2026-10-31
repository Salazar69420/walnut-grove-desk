import { useMemo, useState } from 'react'
import { api, ApiError } from '../api/client'
import { useApp } from '../ctx'
import { useLive } from '../live'
import { Empty, Modal, Segmented, Sheet, SkeletonRows, Time } from '../components/ui'
import { Icon } from '../components/icons'
import type { FeeRow, KCounts, KEntry } from '../lib/types'

type Tab = 'approved' | 'pending' | 'fees' | 'history'
type KList = { items: KEntry[]; counts: KCounts; categories: [string, string][] }
const errText = (x: unknown) => x instanceof ApiError && x.message && x.message !== 'error' && x.message !== 'network' ? x.message : ''
const money = (f: FeeRow) => f.price_dollars === null ? 'Ask the clinic' : `${f.price_prefix ? f.price_prefix + ' ' : ''}$${f.price_dollars.toFixed(2)}${f.unit ? ' ' + f.unit : ''}`

/** Word-level diff: marks words that are not in the other text. Texts are short, so the simple table is fine. */
export function diffWords(a: string, b: string): { left: [string, boolean][]; right: [string, boolean][] } {
  const x = a.split(/(\s+)/), y = b.split(/(\s+)/)
  const n = x.length, m = y.length
  const t: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) t[i][j] = x[i] === y[j] ? t[i + 1][j + 1] + 1 : Math.max(t[i + 1][j], t[i][j + 1])
  const left: [string, boolean][] = [], right: [string, boolean][] = []
  let i = 0, j = 0
  while (i < n && j < m) {
    if (x[i] === y[j]) { left.push([x[i], false]); right.push([y[j], false]); i++; j++ }
    else if (t[i + 1][j] >= t[i][j + 1]) { left.push([x[i], true]); i++ }
    else { right.push([y[j], true]); j++ }
  }
  while (i < n) left.push([x[i++], true])
  while (j < m) right.push([y[j++], true])
  return { left, right }
}
const Marked = ({ parts, cls }: { parts: [string, boolean][]; cls: string }) => <>{parts.map(([w, d], i) => d && w.trim() ? <mark key={i} className={cls}>{w}</mark> : <span key={i}>{w}</span>)}</>

export function Knowledge() {
  const { can, toast } = useApp()
  const decide = can('knowledge.decide')
  const [tab, setTab] = useState<Tab>('approved')
  const [cat, setCat] = useState('')
  const [q, setQ] = useState('')
  const [conflictsOnly, setConflictsOnly] = useState(false)
  const [sel, setSel] = useState<Set<number>>(new Set())
  const [suggest, setSuggest] = useState<{ replaces?: KEntry } | null>(null)
  const [editing, setEditing] = useState<number | null>(null)
  const [draft, setDraft] = useState('')
  const [declining, setDeclining] = useState<KEntry | null>(null)
  const [reason, setReason] = useState('')

  const status = tab === 'history' ? 'all' : tab === 'fees' ? 'approved' : tab
  const qs = new URLSearchParams({ status })
  if (cat) qs.set('category', cat)
  if (q.trim()) qs.set('q', q.trim())
  if (tab === 'pending' && conflictsOnly) qs.set('conflicts', 'true')
  const list = useLive<KList>(tab === 'fees' ? null : '/knowledge?' + qs.toString(), ['knowledge'])
  const counts = list.data?.counts
  const cats = list.data?.categories || []
  const items = (list.data?.items || []).filter((e) => tab !== 'history' || e.status === 'declined' || e.status === 'retired')
  const allSel = items.length > 0 && items.every((e) => sel.has(e.id))

  async function act(path: string, body?: unknown, msg?: string) {
    try { await api(path, { method: 'POST', body: body ?? {}, quiet: true }); if (msg) toast(msg); list.reload(); return true }
    catch (x) { toast(errText(x) || 'That did not work. Try again.'); list.reload(); return false }
  }
  async function bulk(action: 'approve' | 'decline') {
    const ids = [...sel]
    try {
      const r = await api<{ done: number }>('/knowledge/bulk', { method: 'POST', body: { ids, action }, quiet: true })
      toast(`${r.done} ${action === 'approve' ? 'approved' : 'declined'}`)
      setSel(new Set()); list.reload()
    } catch (x) { toast(errText(x) || 'That did not work.') }
  }
  async function saveEdit(e: KEntry, approve: boolean) {
    try {
      await api(`/knowledge/${e.id}`, { method: 'PATCH', body: { body: draft }, quiet: true })
      if (approve) await api(`/knowledge/${e.id}/approve`, { method: 'POST', body: {}, quiet: true })
      toast(approve ? 'Edited and approved' : 'Saved'); setEditing(null); list.reload()
    } catch (x) { toast(errText(x) || 'Could not save that.') }
  }

  return (
    <div className="knp">
      <div className="hd2">
        <div><h1>Knowledge &amp; Fees</h1><p className="lead">What the assistant may say. It only uses approved knowledge. Anything new it learns waits in Pending until a person approves it.</p></div>
        <button className="btn primary" onClick={() => setSuggest({})}><Icon n="plus" size={16} /> Suggest knowledge</button>
      </div>
      <section className="card pad">
        <div className="kn-tabs">
          <Segmented<Tab> label="Section" value={tab} onChange={(t) => { setTab(t); setSel(new Set()) }}
            options={[['approved', 'Approved'], ['pending', `Pending${counts ? ' ' + counts.pending : ''}`], ['fees', 'Fees'], ['history', 'Declined and retired']]} />
          {tab !== 'fees' && <label className="searchf kn-search"><Icon n="search" size={16} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search knowledge" aria-label="Search knowledge" maxLength={80} /></label>}
        </div>
        {tab !== 'fees' && (
          <div className="kn-cats" role="group" aria-label="Category">
            <button className={'chipb' + (!cat ? ' on' : '')} aria-pressed={!cat} onClick={() => setCat('')}>All</button>
            {cats.map(([k, l]) => {
              const n = tab === 'pending' ? counts?.pending_by_category[k] : tab === 'approved' ? counts?.approved_by_category[k] : undefined
              return <button key={k} className={'chipb' + (cat === k ? ' on' : '')} aria-pressed={cat === k} onClick={() => setCat(cat === k ? '' : k)}>{l}{n ? ` ${n}` : ''}</button>
            })}
          </div>
        )}
      </section>

      {tab === 'fees' ? <Fees /> : (
        <section className="kn-list">
          {tab === 'pending' && (
            <div className="card pad kn-pbar">
              <p className="mute">This knowledge is not used by your assistant until it is approved.{!decide && ' Only the owner can approve or decline.'}</p>
              <div className="kn-actions">
                <label className="ck"><input type="checkbox" checked={allSel} onChange={(e) => setSel(e.target.checked ? new Set(items.map((i) => i.id)) : new Set())} /> Select all {items.length}</label>
                <label className="ck"><input type="checkbox" checked={conflictsOnly} onChange={(e) => setConflictsOnly(e.target.checked)} /> Conflicting edits only{counts ? ` (${counts.conflicts})` : ''}</label>
                <span className="spacer" />
                {decide && <><button className="btn okb small" disabled={!sel.size} onClick={() => bulk('approve')}>Approve {sel.size || ''}</button><button className="btn danger small" disabled={!sel.size} onClick={() => bulk('decline')}>Decline {sel.size || ''}</button></>}
              </div>
            </div>
          )}
          {list.loading && !list.data ? <SkeletonRows n={5} h={96} /> : items.length === 0 ? (
            <Empty icon="sparkle" title={tab === 'pending' ? 'Nothing is waiting' : 'No knowledge here yet'} text={tab === 'pending' ? 'When the assistant learns something new from a call or chat, it appears here for approval.' : 'Try another category, or suggest an entry.'} />
          ) : items.map((e) => {
            const d = e.previous_body ? diffWords(e.previous_body, e.body) : null
            return (
              <article key={e.id} className={'card kn-card' + (e.conflicts ? ' conflict' : '')}>
                <header>
                  {tab === 'pending' && decide && <input type="checkbox" aria-label={`Select ${e.title}`} checked={sel.has(e.id)} onChange={(ev) => setSel((s) => { const n = new Set(s); if (ev.target.checked) n.add(e.id); else n.delete(e.id); return n })} />}
                  <div className="kn-t"><h3>{e.title}</h3>
                    <p className="mute small">{e.category_label}{e.status === 'approved' ? ` · version ${e.version}` : ''}
                      {e.status !== 'approved' && <> · <b className={'st-' + e.status}>{e.status}</b></>}</p></div>
                  <div className="kn-tags">
                    {e.proposed_by === 'ai' && <span className="tag ai">AI suggested</span>}
                    {e.proposed_by === 'import' && <span className="tag">From call notes</span>}
                    {e.conflicts && <span className="tag warn">Changes approved knowledge</span>}
                    {e.confidence !== null && <span className="tag" title="How sure the assistant was">{e.confidence}% sure</span>}
                  </div>
                </header>
                {editing === e.id ? (
                  <div className="kn-edit"><textarea value={draft} onChange={(ev) => setDraft(ev.target.value)} rows={5} maxLength={4000} aria-label="Edit text" />
                    <div className="rowbtn"><button className="btn small" onClick={() => setEditing(null)}>Cancel</button><button className="btn small" onClick={() => saveEdit(e, false)}>Save</button>{decide && <button className="btn okb small" onClick={() => saveEdit(e, true)}>Save and approve</button>}</div></div>
                ) : d ? (
                  <div className="kn-diff"><div><h4>Previous</h4><p><Marked parts={d.left} cls="del" /></p></div><div><h4>New</h4><p><Marked parts={d.right} cls="ins" /></p></div></div>
                ) : <p className="kn-body">{e.body}</p>}
                {(e.source_ref || e.source_quote) && <p className="kn-src small mute"><Icon n={e.source_kind === 'call' ? 'phone' : 'chat'} size={13} /> Learned from {e.source_ref || e.source_kind}{e.source_quote ? `: "${e.source_quote}"` : ''}</p>}
                {e.status === 'declined' && e.decline_reason && <p className="small mute">Declined{e.decider ? ' by ' + e.decider : ''}: {e.decline_reason}</p>}
                <footer>
                  <span className="mute small">{e.proposer ? `Suggested by ${e.proposer} ` : ''}<Time iso={e.proposed_at} />{e.decider && e.status !== 'pending' ? ` · decided by ${e.decider}` : ''}</span>
                  <span className="spacer" />
                  {e.status === 'pending' && editing !== e.id && <>
                    <button className="btn small" onClick={() => { setEditing(e.id); setDraft(e.body) }}>Edit</button>
                    {decide && <><button className="btn danger small" onClick={() => { setDeclining(e); setReason('') }}>Decline</button><button className="btn okb small" onClick={() => act(`/knowledge/${e.id}/approve`, {}, 'Approved')}>Approve</button></>}
                  </>}
                  {e.status === 'approved' && <><button className="btn small" onClick={() => setSuggest({ replaces: e })}>Suggest a change</button>{decide && <button className="btn small ghost" onClick={() => act(`/knowledge/${e.id}/retire`, {}, 'Retired')}>Retire</button>}</>}
                </footer>
              </article>
            )
          })}
        </section>
      )}

      <Suggest state={suggest} cats={cats} onClose={() => setSuggest(null)} onDone={() => { setSuggest(null); toast('Sent to Pending for approval'); setTab('pending'); list.reload() }} />
      <Modal open={!!declining} onClose={() => setDeclining(null)} title="Decline this entry?" footer={<><button className="btn" onClick={() => setDeclining(null)}>Keep it</button>
        <button className="btn danger solid" onClick={async () => { if (declining && await act(`/knowledge/${declining.id}/decline`, { reason }, 'Declined')) setDeclining(null) }}>Decline</button></>}>
        <p>The assistant will never use it. You can add a short reason.</p>
        <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={200} placeholder="Reason (optional)" aria-label="Reason" data-autofocus />
      </Modal>
    </div>
  )
}

function Suggest({ state, cats, onClose, onDone }: { state: { replaces?: KEntry } | null; cats: [string, string][]; onClose: () => void; onDone: () => void }) {
  const [cat, setCat] = useState('clinic_info')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const r = state?.replaces
  useMemo(() => { if (state) { setCat(r?.category || 'clinic_info'); setTitle(r?.title || ''); setBody(r?.body || ''); setErr('') } }, [state]) // eslint-disable-line react-hooks/exhaustive-deps
  async function save() {
    setBusy(true); setErr('')
    try { await api('/knowledge', { method: 'POST', body: { category: cat, title, body, replaces_id: r?.id ?? null }, quiet: true }); onDone() }
    catch (x) { setErr(errText(x) || 'Could not send that.') }
    setBusy(false)
  }
  return (
    <Sheet open={!!state} onClose={onClose} title={r ? 'Suggest a change' : 'Suggest knowledge'} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={busy} onClick={save}>Send for approval</button></>}>
      <div className="form">
        <label>Category<select value={cat} onChange={(e) => setCat(e.target.value)} disabled={!!r}>{cats.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        <label>Title<input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} disabled={!!r} data-autofocus /></label>
        <label>Text<textarea value={body} onChange={(e) => setBody(e.target.value)} rows={7} maxLength={4000} /></label>
        {err && <p className="errt" role="alert">{err}</p>}
        <p className="note">The assistant will not use this until the owner approves it. Do not put medical advice or doses here.</p>
      </div>
    </Sheet>
  )
}

function Fees() {
  const { can, toast } = useApp()
  const decide = can('knowledge.decide')
  const [status, setStatus] = useState<'approved' | 'pending' | 'retired'>('approved')
  const list = useLive<{ items: FeeRow[]; pending: number }>('/fees?status=' + status, ['fees', 'knowledge'])
  const [f, setF] = useState({ service: '', price: '', prefix: '', unit: '', note: '' })
  const [err, setErr] = useState('')
  async function add() {
    setErr('')
    try {
      await api('/fees', { method: 'POST', body: { service: f.service, price_dollars: f.price === '' ? null : Number(f.price), price_prefix: f.prefix, unit: f.unit, note: f.note }, quiet: true })
      setF({ service: '', price: '', prefix: '', unit: '', note: '' }); toast('Sent for approval'); setStatus('pending'); list.reload()
    } catch (x) { setErr(errText(x) || 'Could not add that fee.') }
  }
  async function go(id: number, what: 'approve' | 'retire') {
    try { await api(`/fees/${id}/${what}`, { method: 'POST', body: {}, quiet: true }); toast(what === 'approve' ? 'Fee approved' : 'Fee retired'); list.reload() } catch (x) { toast(errText(x) || 'Not saved.') }
  }
  return (
    <section className="kn-list">
      <div className="card pad">
        <Segmented<typeof status> label="Fee status" value={status} onChange={setStatus} options={[['approved', 'Approved'], ['pending', `Pending${list.data ? ' ' + list.data.pending : ''}`], ['retired', 'Retired']]} />
        <div className="tablewrap"><table className="feet">
          <thead><tr><th>Service</th><th>Price</th><th>Note</th><th>Effective</th><th /></tr></thead>
          <tbody>{(list.data?.items || []).map((x) => (
            <tr key={x.id}><td><b>{x.service}</b></td><td>{money(x)}</td><td className="mute">{x.note}</td>
              <td className="mute">{x.effective_from ? 'from ' + x.effective_from : ''}{x.effective_to ? ' to ' + x.effective_to : ''}</td>
              <td className="rowbtn">{decide && x.status === 'pending' && <button className="btn okb small" onClick={() => go(x.id, 'approve')}>Approve</button>}{decide && x.status === 'approved' && <button className="btn small ghost" onClick={() => go(x.id, 'retire')}>Retire</button>}</td></tr>
          ))}</tbody>
        </table></div>
        {list.data && list.data.items.length === 0 && <Empty icon="list" title="No fees in this list" />}
        <p className="note">Prices shown are examples or heard in calls until the clinic approves them. The assistant quotes only approved fees.</p>
      </div>
      <div className="card pad">
        <h3>Suggest a fee</h3>
        <div className="form two">
          <label>Service<input value={f.service} onChange={(e) => setF({ ...f, service: e.target.value })} maxLength={120} /></label>
          <label>Price in dollars<input inputMode="decimal" value={f.price} onChange={(e) => setF({ ...f, price: e.target.value })} placeholder="Leave empty for ask the clinic" /></label>
          <label>Prefix<select value={f.prefix} onChange={(e) => setF({ ...f, prefix: e.target.value })}><option value="">None</option><option value="from">from</option><option value="about">about</option></select></label>
          <label>Per<input value={f.unit} onChange={(e) => setF({ ...f, unit: e.target.value })} maxLength={40} placeholder="visit, night, vaccine" /></label>
        </div>
        <label className="form">Note<input value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} maxLength={300} /></label>
        {err && <p className="errt" role="alert">{err}</p>}
        <button className="btn primary" disabled={f.service.trim().length < 2} onClick={add}>Send for approval</button>
      </div>
    </section>
  )
}
