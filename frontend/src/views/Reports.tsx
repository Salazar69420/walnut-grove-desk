import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import { Empty, SkeletonRows } from '../components/ui'
import { Icon } from '../components/icons'
import { ls, lsSet } from '../lib/fmt'

type Cat = { key: string; group: string; title: string; text: string }
type Section = { title: string; kind: 'table' | 'bars'; columns?: string[]; rows: (string | number)[][] }
type Rep = { key: string; title: string; text: string; range: { from: string; to: string }; stats: { label: string; value: string | number; hint: string }[]; sections: Section[] }
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

function favs(): string[] { try { return JSON.parse(ls('wgd.reports.fav') || '[]') } catch { return [] } }

export function Reports() {
  const [cat, setCat] = useState<Cat[] | null>(null)
  const [fav, setFav] = useState<string[]>(favs())
  useEffect(() => { api<{ items: Cat[] }>('/reports', { quiet: true }).then((r) => setCat(r.items)).catch(() => setCat([])) }, [])
  const toggle = (k: string) => setFav((f) => { const n = f.includes(k) ? f.filter((x) => x !== k) : [...f, k]; lsSet('wgd.reports.fav', JSON.stringify(n)); return n })
  const groups = [...new Set((cat || []).map((c) => c.group))]
  return (
    <div className="rpp">
      <div className="hd2"><div><h1>Reports</h1><p className="lead">Numbers from the shared record. Star a report to keep it at the top.</p></div></div>
      {!cat ? <SkeletonRows n={3} h={80} /> : (
        <>
          {fav.length > 0 && <section><h2 className="rp-g">My reports</h2><div className="rp-grid">{cat.filter((c) => fav.includes(c.key)).map((c) => <Tile key={c.key} c={c} on toggle={toggle} />)}</div></section>}
          {groups.map((g) => <section key={g}><h2 className="rp-g">{g}</h2><div className="rp-grid">{cat.filter((c) => c.group === g).map((c) => <Tile key={c.key} c={c} on={fav.includes(c.key)} toggle={toggle} />)}</div></section>)}
        </>
      )}
    </div>
  )
}

function Tile({ c, on, toggle }: { c: Cat; on: boolean; toggle: (k: string) => void }) {
  return (
    <div className="card rp-tile">
      <button className={'star' + (on ? ' on' : '')} aria-pressed={on} aria-label={`${on ? 'Remove' : 'Add'} ${c.title} ${on ? 'from' : 'to'} my reports`} onClick={() => toggle(c.key)}><Icon n="bookmark" size={16} /></button>
      <Link to={`/reports/${c.key}`}><h3>{c.title}</h3><p className="mute">{c.text}</p></Link>
    </div>
  )
}

export function Report() {
  const { key } = useParams()
  const [from, setFrom] = useState(iso(new Date(Date.now() - 29 * 86400000)))
  const [to, setTo] = useState(iso(new Date()))
  const [rep, setRep] = useState<Rep | null>(null)
  const [err, setErr] = useState(false)
  useEffect(() => {
    setRep(null); setErr(false)
    api<Rep>(`/reports/${key}?from=${from}&to=${to}`, { quiet: true }).then(setRep).catch(() => setErr(true))
  }, [key, from, to])
  return (
    <div className="rpp">
      <p><Link to="/reports" className="link-a">All reports</Link></p>
      <div className="hd2"><div><h1>{rep?.title || 'Report'}</h1>{rep && <p className="lead">{rep.text}</p>}</div>
        <div className="rp-range"><label>From<input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label><label>To<input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label></div></div>
      {err ? <Empty icon="alert" title="This report could not load" text="Check the dates (a year at most, start before end) and try again." /> : !rep ? <SkeletonRows n={4} h={70} /> : (
        <>
          <div className="stats">{rep.stats.map((s) => <div key={s.label} className="stat"><b>{s.value}</b><span>{s.label}</span>{s.hint && <small>{s.hint}</small>}</div>)}</div>
          {rep.sections.map((s) => (
            <section key={s.title} className="card pad"><h3>{s.title}</h3>
              {s.rows.length === 0 ? <p className="mute">Nothing to show for these dates.</p> : s.kind === 'bars' ? <Bars rows={s.rows as [string, number][]} /> : (
                <div className="tablewrap"><table><thead><tr>{s.columns!.map((c) => <th key={c}>{c}</th>)}</tr></thead><tbody>{s.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j}>{v}</td>)}</tr>)}</tbody></table></div>
              )}
            </section>
          ))}
        </>
      )}
    </div>
  )
}

function Bars({ rows }: { rows: [string, number][] }) {
  const max = Math.max(1, ...rows.map((r) => r[1]))
  return <div className="hbars">{rows.map(([l, v]) => <div key={l} className="hb"><span className="hl">{l}</span><span className="ht"><i style={{ width: `${(v / max) * 100}%` }} /></span><b>{v}</b></div>)}</div>
}
