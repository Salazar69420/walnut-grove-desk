import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { useApp } from '../ctx'
import { useLive } from '../live'
import { Avatar, ConsentDot, Empty, KindBadge, Pop, SkeletonRows, Time, useMedia } from '../components/ui'
import { Icon } from '../components/icons'
import { AddContact } from '../components/AddContact'
import { useStaff } from '../lib/store'
import { ls, lsSet, shortDate } from '../lib/fmt'
import { CONSENT_LABEL, SOURCE_LABEL, type Row } from '../lib/types'

type ColKey = 'kind' | 'email' | 'phone' | 'consent' | 'owner' | 'pets' | 'visits' | 'last_visit' | 'last_message' | 'tasks' | 'ai' | 'source' | 'last_active' | 'created'
const COLS: { k: ColKey; label: string; sort?: string; w: number }[] = [
  { k: 'kind', label: 'Status', w: 96 },
  { k: 'email', label: 'Email', w: 210 },
  { k: 'phone', label: 'Phone', w: 140 },
  { k: 'consent', label: 'Consent', w: 104 },
  { k: 'owner', label: 'Owner', w: 130 },
  { k: 'pets', label: 'Pets', w: 140 },
  { k: 'visits', label: 'Visits', sort: 'visits', w: 80 },
  { k: 'last_visit', label: 'Last visit', sort: 'last_visit', w: 110 },
  { k: 'last_message', label: 'Last message', w: 280 },
  { k: 'tasks', label: 'Open tasks', w: 100 },
  { k: 'ai', label: 'AI', w: 70 },
  { k: 'source', label: 'Source', w: 110 },
  { k: 'last_active', label: 'Last active', sort: 'last_active', w: 120 },
  { k: 'created', label: 'Created', sort: 'created', w: 110 },
]
const DEFAULT_COLS: ColKey[] = ['kind', 'phone', 'consent', 'owner', 'last_message', 'visits', 'last_active']
const FILTER_KEYS = ['q', 'kind', 'owner', 'email_consent', 'sms_consent', 'species', 'visits_min', 'visits_max', 'last_visit_from', 'last_visit_to', 'has_open_task', 'ai', 'source'] as const
const CONSENTS: [string, string][] = [['full', 'Full'], ['transactional', 'Care messages only'], ['none', 'None'], ['unsubscribed', 'Unsubscribed']]

type View = { id: number; name: string; filters: Record<string, string>; columns: ColKey[] }
function normView(v: any): View {
  const parse = (x: any, d: any) => { if (typeof x === 'string') { try { return JSON.parse(x) } catch { return d } } return x ?? d }
  return { id: v.id, name: v.name, filters: parse(v.filters ?? v.filters_json, {}), columns: parse(v.columns ?? v.columns_json, DEFAULT_COLS) }
}

export function Contacts() {
  const { can, toast } = useApp()
  const nav = useNavigate()
  const [sp, setSp] = useSearchParams()
  const staff = useStaff()
  const phone = useMedia('(max-width: 780px)')
  const [cols, setColsState] = useState<ColKey[]>(() => { try { const v = JSON.parse(ls('wgd-contact-cols') || ''); if (Array.isArray(v)) return v } catch { /* */ } return DEFAULT_COLS })
  const setCols = (c: ColKey[]) => { setColsState(c); lsSet('wgd-contact-cols', JSON.stringify(c)) }
  const [qText, setQText] = useState(sp.get('q') || '')
  const [active, setActive] = useState(-1)
  const tbody = useRef<HTMLDivElement>(null)
  const search = useRef<HTMLInputElement>(null)
  const add = sp.get('add') === '1'

  const set = useCallback((patch: Record<string, string | null>) => {
    setSp((p) => {
      const n = new URLSearchParams(p)
      for (const [k, v] of Object.entries(patch)) { if (v === null || v === '') n.delete(k); else n.set(k, v) }
      if (!('page' in patch)) n.delete('page')
      return n
    }, { replace: true })
  }, [setSp])

  useEffect(() => { const t = window.setTimeout(() => { if ((sp.get('q') || '') !== qText.trim()) set({ q: qText.trim() }) }, 250); return () => clearTimeout(t) }, [qText]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (!sp.get('q') && qText && document.activeElement !== search.current) setQText('') }, [sp]) // eslint-disable-line react-hooks/exhaustive-deps

  const sort = sp.get('sort') || 'last_active'
  const dir = sp.get('dir') || (sort === 'name' ? 'asc' : 'desc')
  const page = Math.max(1, Number(sp.get('page') || 1))
  const per = [25, 50, 100].includes(Number(sp.get('per_page'))) ? Number(sp.get('per_page')) : 50
  const filterQs = useMemo(() => {
    const p = new URLSearchParams()
    FILTER_KEYS.forEach((k) => { const v = sp.get(k); if (v) p.set(k, v) })
    return p
  }, [sp])
  const qs = useMemo(() => { const p = new URLSearchParams(filterQs); p.set('sort', sort); p.set('dir', dir); p.set('page', String(page)); p.set('per_page', String(per)); return p.toString() }, [filterQs, sort, dir, page, per])
  const list = useLive<{ items: Row[]; total: number; page: number; per_page: number }>('/contacts?' + qs, ['contacts', 'messages', 'tasks'])
  const rows = list.data?.items || []
  const total = list.data?.total ?? 0
  const activeFilters = FILTER_KEYS.filter((k) => sp.get(k))
  const pages = Math.max(1, Math.ceil(total / per))

  useEffect(() => { setActive(-1) }, [qs])
  useEffect(() => { if (page > pages && list.data) set({ page: String(pages) }) }, [page, pages, list.data]) // eslint-disable-line react-hooks/exhaustive-deps

  function clearAll() { setQText(''); setSp((p) => { const n = new URLSearchParams(); ['sort', 'dir', 'per_page'].forEach((k) => { const v = p.get(k); if (v) n.set(k, v) }); return n }, { replace: true }) }
  function sortBy(s: string) {
    if (sort === s) set({ sort: s, dir: dir === 'asc' ? 'desc' : 'asc', page: null })
    else set({ sort: s, dir: s === 'name' ? 'asc' : 'desc', page: null })
  }
  function onKey(e: React.KeyboardEvent) {
    const t = e.target as HTMLElement
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(t.tagName)) return
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      const n = active < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, active + (e.key === 'ArrowDown' ? 1 : -1)))
      setActive(n)
      tbody.current?.querySelectorAll<HTMLElement>('[data-row]')[n]?.focus()
    } else if (e.key === 'Enter' && active >= 0 && rows[active] && t.tagName !== 'A' && t.tagName !== 'BUTTON') nav(`/contacts/${rows[active].id}`)
  }
  useEffect(() => {
    const f = (e: KeyboardEvent) => { if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target as HTMLElement).tagName)) { e.preventDefault(); search.current?.focus() } }
    document.addEventListener('keydown', f)
    return () => document.removeEventListener('keydown', f)
  }, [])

  const visible = COLS.filter((c) => cols.includes(c.k))
  const exportHref = '/api/export/contacts.csv' + (filterQs.toString() ? '?' + filterQs.toString() : '')
  const ownerLabel = (v: string) => v === 'me' ? 'Me' : v === 'none' ? 'Unassigned' : staff.find((s) => String(s.id) === v)?.display_name || 'Someone'
  const showCount = list.data ? `${total} ${total === 1 ? 'contact' : 'contacts'}` : ''

  return (
    <>
      <div className="hd2">
        <div><h1>Contacts</h1><p className="lead">Inquiries and patients. {showCount && <b className="cnt">{showCount}</b>}</p></div>
        <div className="homebar">
          {can('contacts.export') && <a className="btn" href={exportHref} download><Icon n="download" size={16} /> Export CSV</a>}
          {can('contacts.edit') && <button className="btn primary" onClick={() => set({ add: '1' })}><Icon n="plus" size={16} /> Add contact</button>}
        </div>
      </div>
      <section className="card ctable">
        <div className="fbar">
          <label className="searchf"><Icon n="search" size={16} /><input ref={search} value={qText} onChange={(e) => setQText(e.target.value)} placeholder="Search name, phone, email or pet" aria-label="Search contacts" maxLength={80} /></label>
          <div className="fbar-r">
            <ViewsMenu filters={Object.fromEntries(filterQs)} cols={cols} onApply={(v) => { setCols(v.columns.filter((c) => COLS.some((x) => x.k === c))); setQText(v.filters.q || ''); setSp((p) => { const n = new URLSearchParams(); ['sort', 'dir', 'per_page'].forEach((k) => { const x = p.get(k); if (x) n.set(k, x) }); Object.entries(v.filters).forEach(([k, x]) => { if (x) n.set(k, String(x)) }); return n }, { replace: true }) }} toast={toast} />
            <ColumnsMenu cols={cols} setCols={setCols} />
          </div>
        </div>
        <div className="chips2" role="group" aria-label="Filters">
          <Chip label="Status" value={sp.get('kind') ? (sp.get('kind') === 'patient' ? 'Patient' : 'Inquiry') : null} onClear={() => set({ kind: null })}>{(close) => <Opts cur={sp.get('kind')} opts={[['patient', 'Patient'], ['inquiry', 'Inquiry']]} pick={(v) => { set({ kind: v }); close() }} />}</Chip>
          <Chip label="Owner" value={sp.get('owner') ? ownerLabel(sp.get('owner')!) : null} onClear={() => set({ owner: null })}>{(close) => <Opts cur={sp.get('owner')} opts={[['me', 'Me'], ['none', 'Unassigned'], ...staff.map((s) => [String(s.id), s.display_name] as [string, string])]} pick={(v) => { set({ owner: v }); close() }} />}</Chip>
          <Chip label="Email consent" value={sp.get('email_consent') ? CONSENT_LABEL[sp.get('email_consent')!] : null} onClear={() => set({ email_consent: null })}>{(close) => <Opts cur={sp.get('email_consent')} opts={CONSENTS} pick={(v) => { set({ email_consent: v }); close() }} dots />}</Chip>
          <Chip label="Text consent" value={sp.get('sms_consent') ? CONSENT_LABEL[sp.get('sms_consent')!] : null} onClear={() => set({ sms_consent: null })}>{(close) => <Opts cur={sp.get('sms_consent')} opts={CONSENTS} pick={(v) => { set({ sms_consent: v }); close() }} dots />}</Chip>
          <Chip label="Pet type" value={sp.get('species')} onClear={() => set({ species: null })}>{(close) => <TextFilter initial={sp.get('species') || ''} placeholder="dog, cat, rabbit" quick={['dog', 'cat', 'rabbit', 'bird']} apply={(v) => { set({ species: v }); close() }} />}</Chip>
          <Chip label="Visits" value={sp.get('visits_min') || sp.get('visits_max') ? `${sp.get('visits_min') || '0'} to ${sp.get('visits_max') || 'any'}` : null} onClear={() => set({ visits_min: null, visits_max: null })}>{(close) => <RangeFilter type="number" a={sp.get('visits_min') || ''} b={sp.get('visits_max') || ''} la="At least" lb="At most" apply={(a, b) => { set({ visits_min: a, visits_max: b }); close() }} />}</Chip>
          <Chip label="Last visit" value={sp.get('last_visit_from') || sp.get('last_visit_to') ? `${sp.get('last_visit_from') ? shortDate(sp.get('last_visit_from')! + 'T12:00:00Z') : 'any'} to ${sp.get('last_visit_to') ? shortDate(sp.get('last_visit_to')! + 'T12:00:00Z') : 'now'}` : null} onClear={() => set({ last_visit_from: null, last_visit_to: null })}>{(close) => <RangeFilter type="date" a={sp.get('last_visit_from') || ''} b={sp.get('last_visit_to') || ''} la="From" lb="To" apply={(a, b) => { set({ last_visit_from: a, last_visit_to: b }); close() }} />}</Chip>
          <Chip label="Open task" value={sp.get('has_open_task') ? (sp.get('has_open_task') === 'true' ? 'Yes' : 'No') : null} onClear={() => set({ has_open_task: null })}>{(close) => <Opts cur={sp.get('has_open_task')} opts={[['true', 'Has an open task'], ['false', 'No open task']]} pick={(v) => { set({ has_open_task: v }); close() }} />}</Chip>
          <Chip label="AI" value={sp.get('ai') ? (sp.get('ai') === 'on' ? 'On' : 'Off') : null} onClear={() => set({ ai: null })}>{(close) => <Opts cur={sp.get('ai')} opts={[['on', 'AI assistant on'], ['off', 'AI assistant off']]} pick={(v) => { set({ ai: v }); close() }} />}</Chip>
          <Chip label="Source" value={sp.get('source') ? SOURCE_LABEL[sp.get('source')!] || sp.get('source') : null} onClear={() => set({ source: null })}>{(close) => <Opts cur={sp.get('source')} opts={Object.entries(SOURCE_LABEL)} pick={(v) => { set({ source: v }); close() }} />}</Chip>
          {activeFilters.length > 0 && <button className="link-a clear" onClick={clearAll}>Clear all</button>}
        </div>

        {list.loading && !list.data ? <SkeletonRows n={9} /> : list.error && !list.data ? <p className="sub pad">Could not load contacts. It will try again.</p> : rows.length === 0 ? (
          <Empty icon="people" title={activeFilters.length ? 'No one matches these filters' : 'No contacts yet'} text={activeFilters.length ? 'Try fewer filters, or clear them all.' : 'Add the first contact to get started.'}
            action={activeFilters.length ? <button className="btn" onClick={clearAll}>Clear all filters</button> : can('contacts.edit') ? <button className="btn primary" onClick={() => set({ add: '1' })}>Add contact</button> : undefined} />
        ) : phone ? (
          <div className="cards" ref={tbody}>{rows.map((r) => <ContactCard key={r.id} r={r} />)}</div>
        ) : (
          <div className="tscroll" onKeyDown={onKey} ref={tbody}>
            <table className="ctab" aria-label="Contacts" aria-rowcount={total}>
              <thead>
                <tr>
                  <th className="c-name" aria-sort={sort === 'name' ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}><SortBtn label="Name" on={sort === 'name'} dir={dir} click={() => sortBy('name')} /></th>
                  {visible.map((c) => <th key={c.k} style={{ minWidth: c.w }} aria-sort={c.sort && sort === c.sort ? (dir === 'asc' ? 'ascending' : 'descending') : undefined}>{c.sort ? <SortBtn label={c.label} on={sort === c.sort} dir={dir} click={() => sortBy(c.sort!)} /> : c.label}</th>)}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={r.id} data-row tabIndex={active === i || (active < 0 && i === 0) ? 0 : -1} className={active === i ? 'on' : ''} onClick={() => nav(`/contacts/${r.id}`)} onFocus={() => setActive(i)}>
                    <td className="c-name">
                      <Link to={`/contacts/${r.id}`} onClick={(e) => e.stopPropagation()} className="nm">
                        <Avatar name={r.display_name} initials={r.initials} hue={r.avatar_hue} size={34} />
                        <span className="nm-t"><b>{r.display_name}</b>{r.pet_names.length > 0 && <span className="mute">{r.pet_names.join(', ')}</span>}</span>
                        {r.unread > 0 && <span className="udot" aria-label={`${r.unread} unread`} />}
                      </Link>
                    </td>
                    {visible.map((c) => <td key={c.k}>{cell(c.k, r)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {list.data && rows.length > 0 && (
          <div className="pg">
            <span className="mute">{(page - 1) * per + 1} to {(page - 1) * per + rows.length} of {total}</span>
            <span className="spacer" />
            <label className="pg-per">Rows <select value={per} onChange={(e) => set({ per_page: e.target.value })} aria-label="Rows per page">{[25, 50, 100].map((n) => <option key={n}>{n}</option>)}</select></label>
            <button className="btn" disabled={page <= 1} onClick={() => set({ page: String(page - 1) })}>Previous</button>
            <span className="pg-n">Page {page} of {pages}</span>
            <button className="btn" disabled={page >= pages} onClick={() => set({ page: String(page + 1) })}>Next</button>
          </div>
        )}
      </section>
      <AddContact open={add} onClose={() => set({ add: null })} />
    </>
  )
}

function cell(k: ColKey, r: Row): ReactNode {
  switch (k) {
    case 'kind': return <KindBadge kind={r.kind} />
    case 'email': return r.email ? <span className="ell">{r.email}</span> : <span className="mute">None</span>
    case 'phone': return r.phone_display || r.phone || <span className="mute">None</span>
    case 'consent': return <span className="cds"><span className="cd"><ConsentDot value={r.email_consent} channel="Email" /><small>Email</small></span><span className="cd"><ConsentDot value={r.sms_consent} channel="Text" /><small>Text</small></span></span>
    case 'owner': return r.owner ? <span className="own"><Avatar name={r.owner.display_name} size={22} hue={(r.owner.id * 67 + 23) % 360} />{r.owner.display_name}</span> : <span className="mute">Unassigned</span>
    case 'pets': return r.pet_names.length ? <span className="ell">{r.pet_names.join(', ')}</span> : <span className="mute">None</span>
    case 'visits': return r.visit_count
    case 'last_visit': return r.last_visit_at ? <Time iso={r.last_visit_at} /> : <span className="mute">Never</span>
    case 'last_message': return r.last_message ? <span className="lm"><Icon n={r.last_message.channel === 'email' ? 'mail' : r.last_message.channel === 'call' ? 'phone' : 'chat'} size={13} /><span className="ell">{r.last_message.direction === 'out' ? 'You: ' : ''}{r.last_message.preview}</span></span> : <span className="mute">No messages</span>
    case 'tasks': return r.open_tasks > 0 ? <span className="tag warn">{r.open_tasks} open</span> : <span className="mute">None</span>
    case 'ai': return r.ai_enabled ? <span className="tag acc">On</span> : <span className="tag">Off</span>
    case 'source': return SOURCE_LABEL[r.source] || r.source
    case 'last_active': return <Time iso={r.last_active_at} />
    case 'created': return <Time iso={r.created_at} />
  }
}

function SortBtn({ label, on, dir, click }: { label: string; on: boolean; dir: string; click: () => void }) {
  return <button className={'sortb' + (on ? ' on' : '')} onClick={click}>{label}<span aria-hidden="true">{on ? (dir === 'asc' ? ' ▲' : ' ▼') : ''}</span><span className="sr">{on ? `, sorted ${dir === 'asc' ? 'ascending' : 'descending'}` : ', click to sort'}</span></button>
}

function ContactCard({ r }: { r: Row }) {
  return (
    <Link to={`/contacts/${r.id}`} className={'ccard' + (r.unread ? ' unread' : '')}>
      <Avatar name={r.display_name} initials={r.initials} hue={r.avatar_hue} size={44} />
      <div className="cc-m">
        <div className="cc-n"><b>{r.display_name}</b><KindBadge kind={r.kind} /></div>
        <div className="cc-p ell">{r.last_message ? `${r.last_message.direction === 'out' ? 'You: ' : ''}${r.last_message.preview}` : r.pet_names.length ? r.pet_names.join(', ') : 'No messages yet'}</div>
        <div className="cc-f"><ConsentDot value={r.email_consent} channel="Email" /><ConsentDot value={r.sms_consent} channel="Text" /><span className="mute">{r.owner ? r.owner.display_name : 'Unassigned'}</span>{r.open_tasks > 0 && <span className="tag warn">{r.open_tasks} task{r.open_tasks > 1 ? 's' : ''}</span>}</div>
      </div>
      <div className="cc-r"><Time iso={r.last_active_at} className="mute" />{r.unread > 0 && <span className="ubdg">{r.unread}</span>}</div>
    </Link>
  )
}

function Chip({ label, value, onClear, children }: { label: string; value: string | null; onClear: () => void; children: (close: () => void) => ReactNode }) {
  return (
    <Pop className="chipw" button={({ open, toggle }) => (
      <span className={'chip' + (value ? ' on' : '')}>
        <button className="chip-b" onClick={toggle} aria-expanded={open} aria-haspopup="dialog">{label}{value ? <>: <b>{value}</b></> : <Icon n="chevD" size={13} />}</button>
        {value && <button className="chip-x" onClick={onClear} aria-label={`Clear ${label} filter`}><Icon n="x" size={13} /></button>}
      </span>
    )}>{children}</Pop>
  )
}
function Opts({ opts, cur, pick, dots }: { opts: [string, string][]; cur: string | null; pick: (v: string) => void; dots?: boolean }) {
  return <div className="opts" role="listbox">{opts.map(([v, l]) => <button key={v} role="option" aria-selected={cur === v} className={cur === v ? 'on' : ''} onClick={() => pick(v)}>{dots && <i className={'cdot c-' + v} aria-hidden="true" />}{l}{cur === v && <Icon n="check" size={14} />}</button>)}</div>
}
function TextFilter({ initial, placeholder, quick, apply }: { initial: string; placeholder: string; quick: string[]; apply: (v: string) => void }) {
  const [v, setV] = useState(initial)
  return (
    <form className="pf" onSubmit={(e) => { e.preventDefault(); apply(v.trim()) }}>
      <input data-autofocus value={v} onChange={(e) => setV(e.target.value)} placeholder={placeholder} aria-label="Pet type" maxLength={40} />
      <div className="quick2">{quick.map((q) => <button type="button" key={q} onClick={() => apply(q)}>{q}</button>)}</div>
      <button className="btn primary small" type="submit">Apply</button>
    </form>
  )
}
function RangeFilter({ type, a, b, la, lb, apply }: { type: 'number' | 'date'; a: string; b: string; la: string; lb: string; apply: (a: string, b: string) => void }) {
  const [x, setX] = useState(a)
  const [y, setY] = useState(b)
  return (
    <form className="pf" onSubmit={(e) => { e.preventDefault(); apply(x, y) }}>
      <label className="f">{la}<input data-autofocus type={type} min={type === 'number' ? 0 : undefined} value={x} onChange={(e) => setX(e.target.value)} /></label>
      <label className="f">{lb}<input type={type} min={type === 'number' ? 0 : undefined} value={y} onChange={(e) => setY(e.target.value)} /></label>
      <button className="btn primary small" type="submit">Apply</button>
    </form>
  )
}

function ColumnsMenu({ cols, setCols }: { cols: ColKey[]; setCols: (c: ColKey[]) => void }) {
  return (
    <Pop align="right" button={({ open, toggle }) => <button className="btn" onClick={toggle} aria-expanded={open}><Icon n="columns" size={16} /> Columns</button>}>
      {() => (
        <div className="opts cols" role="group" aria-label="Choose columns">
          <label className="ck"><input type="checkbox" checked disabled /> Name</label>
          {COLS.map((c) => (
            <label key={c.k} className="ck"><input type="checkbox" checked={cols.includes(c.k)} onChange={(e) => setCols(e.target.checked ? COLS.filter((x) => x.k === c.k || cols.includes(x.k)).map((x) => x.k) : cols.filter((x) => x !== c.k))} /> {c.label}</label>
          ))}
          <button className="link-a" onClick={() => setCols(DEFAULT_COLS)}>Reset to default</button>
        </div>
      )}
    </Pop>
  )
}

function ViewsMenu({ filters, cols, onApply, toast }: { filters: Record<string, string>; cols: ColKey[]; onApply: (v: View) => void; toast: (t: string) => void }) {
  const v = useLive<{ items: any[] }>('/views?screen=contacts', [])
  const [name, setName] = useState('')
  const [ren, setRen] = useState<number | null>(null)
  const views = (v.data?.items || []).map(normView)
  const same = (a: Record<string, string>, b: Record<string, string>) => { const ka = Object.keys(a).filter((k) => a[k]), kb = Object.keys(b).filter((k) => b[k]); return ka.length === kb.length && ka.every((k) => String(a[k]) === String(b[k])) }
  const cur = views.find((x) => same(x.filters, filters))
  async function save(close: () => void, replaceId?: number) {
    const n = name.trim()
    if (!n) return
    try {
      const old = replaceId ? views.find((x) => x.id === replaceId) : undefined
      await api('/views', { body: { screen: 'contacts', name: n.slice(0, 60), filters: old ? old.filters : filters, columns: old ? old.columns : cols } })
      if (replaceId) await api(`/views/${replaceId}`, { method: 'DELETE' })
      toast(replaceId ? 'View renamed' : 'View saved'); setName(''); setRen(null); v.reload(); close()
    } catch { /* toast shown */ }
  }
  async function del(x: View) {
    try { await api(`/views/${x.id}`, { method: 'DELETE' }); toast(`Deleted view "${x.name}"`); v.reload() } catch { /* */ }
  }
  return (
    <Pop align="right" button={({ open, toggle }) => <button className="btn" onClick={toggle} aria-expanded={open}><Icon n="bookmark" size={16} /> {cur ? cur.name : 'Views'}<Icon n="chevD" size={13} /></button>}>
      {(close) => (
        <div className="opts views">
          {views.length === 0 && <p className="mute pad">No saved views yet. Set some filters, then save them here.</p>}
          {views.map((x) => (
            <div key={x.id} className="vrow">
              <button className={'vname' + (cur?.id === x.id ? ' on' : '')} onClick={() => { onApply(x); close() }}>{x.name}</button>
              <button className="ibtn sm" aria-label={`Rename ${x.name}`} onClick={() => { setRen(x.id); setName(x.name) }}><Icon n="edit" size={14} /></button>
              <button className="ibtn sm" aria-label={`Delete ${x.name}`} onClick={() => del(x)}><Icon n="trash" size={14} /></button>
            </div>
          ))}
          <form className="vsave" onSubmit={(e) => { e.preventDefault(); save(close, ren ?? undefined) }}>
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={60} placeholder={ren ? 'New name' : 'Name this view'} aria-label="View name" />
            <button className="btn primary small" type="submit" disabled={!name.trim()}>{ren ? 'Rename' : 'Save'}</button>
            {ren && <button type="button" className="btn small" onClick={() => { setRen(null); setName('') }}>Cancel</button>}
          </form>
          {!ren && <p className="note pad">Saves the current filters and columns.</p>}
        </div>
      )}
    </Pop>
  )
}
