import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import { useApp } from '../ctx'
import { Empty, Sheet, SkeletonRows } from '../components/ui'

type Issue = { code: string; severity: 'high' | 'medium' | 'low'; title: string; fix: string; urls: string[]; count: number; detail: string }
type PageRow = { url: string; status: number; ms: number; bytes: number; title: string | null; description: string | null; h1: string[] | null; words: number | null; schema: string[] | null; canonical: string | null; imgs_no_alt: number | null }
type Audit = {
  id: number; status: string; base_url: string; finished_at: string | null; started_by: string; has_plan: boolean; ai_plan?: string
  summary: { pages?: number; ok_pages?: number; score?: number; avg_ms?: number; by_severity?: Record<string, number>; compare?: { new: string[]; fixed: string[]; worse: string[]; better: string[] }; site?: { sitemap_urls?: number; sitemap_hosts?: string[] } }
  issues?: Issue[]; pages?: PageRow[]
}
type View = { settings: { url: string; canonical: string; repo: string; studio: string }; running: boolean; latest: Audit | null; history: { id: number; finished_at: string; score: number }[]; can_run: boolean }

const err = (x: unknown, d: string) => (x instanceof ApiError && x.message && x.message !== 'error' ? x.message : d)
const path = (u: string) => { try { return new URL(u).pathname || '/' } catch { return u } }
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '')

function useWebsite() {
  const [v, setV] = useState<View | null>(null)
  const load = () => api<View>('/website', { quiet: true }).then(setV).catch(() => {})
  useEffect(() => { load() }, [])
  useEffect(() => { if (!v?.running) return; const t = window.setInterval(load, 4000); return () => clearInterval(t) }, [v?.running])
  return { v, load }
}

function RunButton({ v, load }: { v: View; load: () => void }) {
  const { toast } = useApp()
  if (!v.can_run) return null
  async function run() {
    try { await api('/website/audit', { method: 'POST', body: {}, quiet: true }); toast('Reading the website now. This takes under a minute.'); load() }
    catch (x) { toast(err(x, 'Could not start the audit.')) }
  }
  return <button className="btn primary" disabled={v.running} onClick={run}>{v.running ? 'Reading the site...' : v.latest ? 'Run the audit again' : 'Run the first audit'}</button>
}

/** Website content: every page of the clinic's site as visitors and search engines see it, read from the live site. */
export function WebsiteContent() {
  const { toast } = useApp()
  const { v, load } = useWebsite()
  const [open, setOpen] = useState<string | null>(null)
  const [page, setPage] = useState<any>(null)
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (!open) { setPage(null); return } api<{ item: any }>('/website/pages?url=' + encodeURIComponent(open), { quiet: true }).then((r) => setPage(r.item)).catch(() => setPage({ error: true })) }, [open])
  if (!v) return <div className="php"><SkeletonRows n={6} /></div>
  const pages = (v.latest?.pages || []).filter((p) => !q || (p.title || '').toLowerCase().includes(q.toLowerCase()) || p.url.toLowerCase().includes(q.toLowerCase()))
  async function learn() {
    if (!v?.latest) return
    setBusy(true)
    try { const r = await api<{ added: number }>(`/website/audits/${v.latest.id}/knowledge`, { method: 'POST', body: {}, quiet: true }); toast(r.added ? `${r.added} facts from the website are waiting in Knowledge & Fees for you to approve.` : 'Nothing new to suggest from the website.') }
    catch (x) { toast(err(x, 'Could not read the site just now.')) }
    setBusy(false)
  }
  return (
    <div className="php web">
      <div className="hd2">
        <div><h1>Website content</h1><p className="lead">Every page of <a href={v.settings.url} target="_blank" rel="noreferrer">{v.settings.url.replace('https://', '')}</a>, read from the live site. Edit the words in <a href={v.settings.studio} target="_blank" rel="noreferrer">Sanity Studio</a>; the code lives in <a href={v.settings.repo} target="_blank" rel="noreferrer">GitHub</a>.</p></div>
        <div className="rowbtn">{v.can_run && v.latest && <button className="btn" disabled={busy} onClick={learn}>{busy ? 'Reading...' : 'Suggest knowledge from the site'}</button>}<RunButton v={v} load={load} /></div>
      </div>
      {!v.latest ? <Empty icon="list" title={v.running ? 'Reading the website...' : 'The website has not been read yet'} text="The first audit reads every page; then they show here." />
        : (
          <section className="card">
            <div className="web-bar"><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search pages" aria-label="Search pages" /><span className="mute">{pages.length} pages, read {when(v.latest.finished_at)}</span></div>
            <table className="tbl">
              <thead><tr><th>Page</th><th>Title</th><th className="num">Words</th><th className="num">Load</th></tr></thead>
              <tbody>
                {pages.map((p) => (
                  <tr key={p.url} className="click" onClick={() => setOpen(p.url)}>
                    <td><b>{path(p.url)}</b>{p.status !== 200 && <span className="tag bad"> {p.status || 'no answer'}</span>}</td>
                    <td className="ell">{p.title || <span className="mute">No title</span>}</td>
                    <td className="num">{p.words ?? ''}</td>
                    <td className="num">{(p.ms / 1000).toFixed(1)} s</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}
      <Sheet open={!!open} onClose={() => setOpen(null)} title={open ? path(open) : ''}>
        {!page ? <SkeletonRows n={4} /> : page.error ? <p>Could not open that page.</p> : (
          <div className="evd">
            <p><a href={page.url} target="_blank" rel="noreferrer">Open the live page</a></p>
            <dl className="dl">
              <dt>Title</dt><dd>{page.title || <span className="mute">none</span>}</dd>
              <dt>Description</dt><dd>{page.description || <span className="mute">none</span>}</dd>
              <dt>Main heading</dt><dd>{(page.h1 || []).join(' / ') || <span className="mute">none</span>}</dd>
              <dt>Structured data</dt><dd>{(page.schema || []).join(', ') || <span className="mute">none</span>}</dd>
              <dt>Canonical</dt><dd className="ell">{page.canonical || <span className="mute">none</span>}</dd>
            </dl>
            <h3>What the page says</h3>
            <p className="web-text">{page.text || 'No text was found on this page.'}</p>
          </div>
        )}
      </Sheet>
    </div>
  )
}

/** SEO agent: audits the live site, compares with the last audit, and writes a fix plan with code for the site's Next.js code. */
export function WebsiteSeo() {
  const { toast } = useApp()
  const { v, load } = useWebsite()
  const [plan, setPlan] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [openIssue, setOpenIssue] = useState<string | null>(null)
  useEffect(() => { setPlan(v?.latest?.ai_plan || null) }, [v?.latest?.id, v?.latest?.ai_plan])
  if (!v) return <div className="php"><SkeletonRows n={6} /></div>
  const a = v.latest
  const s = a?.summary || {}
  const cmp = s.compare || { new: [], fixed: [], worse: [], better: [] }
  async function askPlan() {
    if (!a) return
    setBusy(true)
    try { const r = await api<{ ai_plan: string }>(`/website/audits/${a.id}/plan`, { method: 'POST', body: {}, quiet: true }); setPlan(r.ai_plan) }
    catch (x) { toast(err(x, 'The AI could not write the plan just now.')) }
    setBusy(false)
  }
  return (
    <div className="php web">
      <div className="hd2">
        <div><h1>SEO agent</h1><p className="lead">Checks how search engines see <a href={v.settings.url} target="_blank" rel="noreferrer">the clinic website</a>: crawling, indexing, canonicals, sitemap, titles, headings, images, structured data and speed. Each audit is kept, so you can see what got better.</p></div>
        <RunButton v={v} load={load} />
      </div>
      {!a ? <Empty icon="list" title={v.running ? 'Reading the website...' : 'No audit yet'} text={v.can_run ? 'Run the first audit to see the site score and what to fix.' : 'An owner runs the first audit.'} /> : (
        <>
          <div className="web-kpis">
            <div className={'card pad web-score ' + ((s.score ?? 0) >= 80 ? 'good' : (s.score ?? 0) >= 50 ? 'mid' : 'low')}><b>{s.score ?? '?'}</b><span>SEO score out of 100</span></div>
            <div className="card pad"><b>{s.ok_pages}/{s.pages}</b><span>pages load</span></div>
            <div className="card pad"><b>{((s.avg_ms || 0) / 1000).toFixed(1)} s</b><span>average page load (server)</span></div>
            <div className="card pad"><b>{s.by_severity?.high || 0} / {s.by_severity?.medium || 0} / {s.by_severity?.low || 0}</b><span>high / medium / low issues</span></div>
          </div>
          <p className="mute">Last audit {when(a.finished_at)} by {a.started_by}.{(cmp.new.length + cmp.fixed.length + cmp.worse.length + cmp.better.length) > 0 && <> Since the one before: {cmp.fixed.length} fixed, {cmp.better.length} better, {cmp.new.length} new, {cmp.worse.length} worse.</>}{v.history.length > 1 && <> Scores so far: {v.history.slice().reverse().map((h) => h.score).join(' > ')}.</>}</p>
          <section className="card">
            <h3 className="pad">What to fix</h3>
            {(a.issues || []).length === 0 ? <p className="pad">Nothing to fix. Well done.</p> : (a.issues || []).map((i) => (
              <div key={i.code} className={'web-issue sev-' + i.severity}>
                <button className="web-issue-h" onClick={() => setOpenIssue(openIssue === i.code ? null : i.code)} aria-expanded={openIssue === i.code}>
                  <span className={'tag sev ' + i.severity}>{i.severity}</span><b>{i.title}</b>{i.count > 0 && <span className="mute">{i.count} page{i.count === 1 ? '' : 's'}</span>}
                  {cmp.new.includes(i.code) && <span className="tag">new</span>}{cmp.worse.includes(i.code) && <span className="tag bad">worse</span>}{cmp.better.includes(i.code) && <span className="tag ok">better</span>}
                </button>
                {openIssue === i.code && <div className="web-issue-b">
                  <p><b>Fix:</b> {i.fix}</p>{i.detail && <p className="mute">{i.detail}</p>}
                  {i.urls.length > 0 && <ul>{i.urls.slice(0, 20).map((u) => <li key={u}><a href={u} target="_blank" rel="noreferrer">{path(u)}</a></li>)}{i.urls.length > 20 && <li className="mute">and {i.urls.length - 20} more</li>}</ul>}
                </div>}
              </div>
            ))}
          </section>
          <section className="card pad">
            <div className="rowbtn" style={{ justifyContent: 'space-between' }}><h3 style={{ margin: 0 }}>Fix plan from the AI</h3>{v.can_run && <button className="btn" disabled={busy} onClick={askPlan}>{busy ? 'Writing the plan (about a minute)...' : plan ? 'Write it again' : 'Write the fix plan'}</button>}</div>
            {plan ? <pre className="web-plan">{plan}</pre> : <p className="mute">The AI turns this audit into a prioritised list of changes with ready-to-paste code for the site (Next.js), using only facts on the site; unknown details are marked TODO.</p>}
          </section>
        </>
      )}
    </div>
  )
}
