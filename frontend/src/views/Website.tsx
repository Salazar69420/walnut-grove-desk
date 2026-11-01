import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import { useApp } from '../ctx'
import { Empty, Sheet, SkeletonRows } from '../components/ui'

type Issue = { code: string; area?: 'google' | 'maps' | 'ai'; severity: 'high' | 'medium' | 'low'; title: string; fix: string; urls: string[]; count: number; detail: string }
type PageRow = { url: string; status: number; ms: number; bytes: number; title: string | null; description: string | null; h1: string[] | null; words: number | null; schema: string[] | null; canonical: string | null; imgs_no_alt: number | null }
type Audit = {
  id: number; status: string; base_url: string; finished_at: string | null; started_by: string; has_plan: boolean; ai_plan?: string
  summary: { areas?: Record<string, number>; pagespeed?: { scores?: Record<string, number>; lab?: Record<string, string>; field?: Record<string, string>; error?: string }; pages?: number; ok_pages?: number; score?: number; avg_ms?: number; by_severity?: Record<string, number>; compare?: { new: string[]; fixed: string[]; worse: string[]; better: string[] }; site?: { sitemap_urls?: number; sitemap_hosts?: string[] } }
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
      <BlogPosts siteUrl={v.settings.url} />
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
  const [area, setArea] = useState<'all' | 'google' | 'maps' | 'ai'>('all')
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
            {(['google', 'maps', 'ai'] as const).map((k) => s.areas?.[k] !== undefined && (
              <button key={k} className={'card pad web-score as-btn ' + ((s.areas![k] >= 80) ? 'good' : s.areas![k] >= 50 ? 'mid' : 'low') + (area === k ? ' sel' : '')} onClick={() => setArea(area === k ? 'all' : k)}>
                <b>{s.areas![k]}</b><span>{k === 'google' ? 'Google search' : k === 'maps' ? 'Google Maps and local' : 'AI search (ChatGPT, Perplexity, AI Overviews)'}</span></button>
            ))}
            {s.pagespeed?.scores && <div className="card pad"><b>{s.pagespeed.scores.performance ?? '?'} / {s.pagespeed.scores.seo ?? '?'}</b><span>Google PageSpeed (mobile): speed / SEO{s.pagespeed.lab?.lcp ? `, LCP ${s.pagespeed.lab.lcp}` : ''}</span></div>}
            <div className="card pad"><b>{s.ok_pages}/{s.pages}</b><span>pages load, average {((s.avg_ms || 0) / 1000).toFixed(1)} s</span></div>
          </div>
          <p className="mute">Last audit {when(a.finished_at)} by {a.started_by}.{(cmp.new.length + cmp.fixed.length + cmp.worse.length + cmp.better.length) > 0 && <> Since the one before: {cmp.fixed.length} fixed, {cmp.better.length} better, {cmp.new.length} new, {cmp.worse.length} worse.</>}{v.history.length > 1 && <> Scores so far: {v.history.slice().reverse().map((h) => h.score).join(' > ')}.</>}</p>
          <section className="card">
            <div className="pad rowbtn" style={{ justifyContent: 'space-between' }}><h3 style={{ margin: 0 }}>What to fix</h3>
              <div className="seg" role="tablist" aria-label="Area">{([['all', 'All'], ['google', 'Google'], ['maps', 'Maps'], ['ai', 'AI search']] as const).map(([k, l]) => <button key={k} role="tab" aria-selected={area === k} className={area === k ? 'on' : ''} onClick={() => setArea(k)}>{l}</button>)}</div></div>
            {(a.issues || []).filter((i) => area === 'all' || (i.area || 'google') === area).length === 0 ? <p className="pad">Nothing to fix here. Well done.</p> : (a.issues || []).filter((i) => area === 'all' || (i.area || 'google') === area).map((i) => (
              <div key={i.code} className={'web-issue sev-' + i.severity}>
                <button className="web-issue-h" onClick={() => setOpenIssue(openIssue === i.code ? null : i.code)} aria-expanded={openIssue === i.code}>
                  <span className={'tag sev ' + i.severity}>{i.severity}</span><span className="tag">{i.area === 'maps' ? 'Maps' : i.area === 'ai' ? 'AI search' : 'Google'}</span><b>{i.title}</b>{i.count > 0 && <span className="mute">{i.count} page{i.count === 1 ? '' : 's'}</span>}
                  {cmp.new.includes(i.code) && <span className="tag">new</span>}{cmp.worse.includes(i.code) && <span className="tag bad">worse</span>}{cmp.better.includes(i.code) && <span className="tag ok">better</span>}
                </button>
                {openIssue === i.code && <div className="web-issue-b">
                  <p><b>Fix:</b> {i.fix}</p>{i.detail && <p className="mute">{i.detail}</p>}
                  {i.urls.length > 0 && <ul>{i.urls.slice(0, 20).map((u) => <li key={u}><a href={u} target="_blank" rel="noreferrer">{path(u)}</a></li>)}{i.urls.length > 20 && <li className="mute">and {i.urls.length - 20} more</li>}</ul>}
                </div>}
              </div>
            ))}
          </section>
          <DeployAgent auditId={a.id} issues={a.issues || []} />
          <section className="card pad">
            <div className="rowbtn" style={{ justifyContent: 'space-between' }}><h3 style={{ margin: 0 }}>Fix plan from the AI</h3>{v.can_run && <button className="btn" disabled={busy} onClick={askPlan}>{busy ? 'Writing the plan (about a minute)...' : plan ? 'Write it again' : 'Write the fix plan'}</button>}</div>
            {plan ? <pre className="web-plan">{plan}</pre> : <p className="mute">The AI turns this audit into a prioritised list of changes with ready-to-paste code for the site (Next.js), using only facts on the site; unknown details are marked TODO.</p>}
          </section>
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- deployment agent
type Change = { id: number; status: string; mode: 'auto' | 'approve'; created_at: string; created_by: string; pr_url: string; preview_url: string; summary: string; error: string; merged_at: string | null; issues: string[]; diff?: string }
type DeployView = { settings: { repo: string; branch: string; mode: 'auto' | 'approve'; has_token: boolean; token_last4: string | null }; changes: Change[]; can_run: boolean }
const CH_LABEL: Record<string, string> = { planning: 'Writing the change...', building: 'Vercel is building a preview...', ready: 'Preview built, waiting for approval', merged: 'Deployed', failed: 'Failed', rejected: 'Rejected' }

export function DeployAgent({ auditId, issues }: { auditId: number; issues: Issue[] }) {
  const { toast, can } = useApp()
  const [d, setD] = useState<DeployView | null>(null)
  const [token, setToken] = useState('')
  const [pick, setPick] = useState<string[]>([])
  const [open, setOpen] = useState<Change | null>(null)
  const load = () => api<DeployView>('/website/deploy', { quiet: true }).then(setD).catch(() => {})
  useEffect(() => { load() }, [])
  const busy = !!d?.changes.some((c) => c.status === 'planning' || c.status === 'building')
  useEffect(() => { if (!busy) return; const t = window.setInterval(load, 8000); return () => clearInterval(t) }, [busy])
  if (!d) return null
  const s = d.settings
  async function save(body: Record<string, unknown>, msg: string) {
    try { const r = await api<{ settings: DeployView['settings'] }>('/website/deploy/settings', { method: 'PUT', body, quiet: true }); setD({ ...d!, settings: r.settings }); setToken(''); toast(msg) }
    catch (x) { toast(err(x, 'Not saved.')) }
  }
  async function run() {
    try { await api('/website/deploy', { method: 'POST', body: { audit_id: auditId, codes: pick }, quiet: true }); toast('The agent is writing the change. It opens a pull request when done.'); load() }
    catch (x) { toast(err(x, 'Could not start the agent.')) }
  }
  async function decide(c: Change, action: 'approve' | 'reject') {
    try { await api(`/website/deploy/${c.id}/${action}`, { method: 'POST', body: {}, quiet: true }); toast(action === 'approve' ? 'Deploying. The live site updates in a minute or two, then it is audited again.' : 'Rejected. The pull request was closed.'); setOpen(null); load() }
    catch (x) { toast(err(x, 'Not done.')) }
  }
  return (
    <section className="card pad">
      <div className="rowbtn" style={{ justifyContent: 'space-between' }}><h3 style={{ margin: 0 }}>Deployment agent</h3>
        {d.can_run && s.has_token && <div className="seg" role="radiogroup" aria-label="Mode">{([['approve', 'Approve each change'], ['auto', 'Fix and deploy automatically']] as const).map(([k, l]) =>
          <button key={k} role="radio" aria-checked={s.mode === k} className={s.mode === k ? 'on' : ''} disabled={!can('admin.settings')} onClick={() => save({ mode: k }, k === 'auto' ? 'Changes now deploy by themselves when the preview build passes.' : 'Each change now waits for your approval.')}>{l}</button>)}</div>}
      </div>
      <p className="mute">The agent edits the site's code on GitHub ({s.repo}), opens a pull request, and Vercel builds a preview. {s.mode === 'auto' ? 'In automatic mode it is merged as soon as the build passes.' : 'You read the change and click Approve and deploy.'} A failed build is never deployed; after deploying, the site is audited again.</p>
      {!s.has_token ? (can('admin.settings') ? (
        <div className="web-token">
          <p><b>Connect GitHub.</b> Make a fine-grained token on GitHub (Settings, Developer settings, Personal access tokens, Fine-grained): only the repository <code>{s.repo}</code>, permissions <b>Contents: Read and write</b>, <b>Pull requests: Read and write</b>, <b>Commit statuses: Read</b>. Paste it here; it is stored encrypted and never shown again.</p>
          <div className="rowbtn"><input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="github_pat_..." aria-label="GitHub token" autoComplete="off" /><button className="btn primary" disabled={token.length < 20} onClick={() => save({ token }, 'GitHub connected')}>Save token</button></div>
        </div>) : <p className="mute">An admin connects GitHub first.</p>) : d.can_run && (
        <>
          <p className="small mute">GitHub token ending {s.token_last4}. {can('admin.settings') && <button className="linkbtn" onClick={() => save({ clear_token: true }, 'Token removed')}>Remove</button>}</p>
          <details><summary>Choose which issues to fix ({pick.length ? pick.length : 'all'})</summary>
            {issues.map((i) => <label key={i.code} className="ck"><input type="checkbox" checked={pick.includes(i.code)} onChange={(e) => setPick(e.target.checked ? [...pick, i.code] : pick.filter((x) => x !== i.code))} /> {i.title}</label>)}
          </details>
          <div className="rowbtn"><button className="btn primary" disabled={busy} onClick={run}>{busy ? 'The agent is working...' : pick.length ? `Fix ${pick.length} issue${pick.length === 1 ? '' : 's'}` : 'Fix everything it can'}</button></div>
        </>
      )}
      {d.changes.length > 0 && <table className="tbl"><tbody>{d.changes.map((c) => (
        <tr key={c.id} className="click" onClick={() => api<{ item: Change }>(`/website/deploy/${c.id}`, { quiet: true }).then((r) => setOpen(r.item)).catch(() => {})}>
          <td><b>{CH_LABEL[c.status] || c.status}</b><div className="mute small">{when(c.created_at)} by {c.created_by}, {c.mode === 'auto' ? 'automatic' : 'needs approval'}</div></td>
          <td className="ell">{c.summary || c.error || ''}</td>
          <td>{c.pr_url && <a href={c.pr_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>Pull request</a>}</td>
        </tr>))}</tbody></table>}
      <Sheet open={!!open} onClose={() => setOpen(null)} title="Website change">
        {open && <div className="evd">
          <p><b>{CH_LABEL[open.status] || open.status}</b>{open.error && <> {open.error}</>}</p>
          {open.summary && <p>{open.summary}</p>}
          <p>{open.pr_url && <a href={open.pr_url} target="_blank" rel="noreferrer">Open the pull request on GitHub</a>}{open.preview_url && <> · <a href={open.preview_url} target="_blank" rel="noreferrer">Preview</a></>}</p>
          {d.can_run && (open.status === 'ready' || open.status === 'building') && <div className="rowbtn"><button className="btn primary" disabled={open.status !== 'ready'} onClick={() => decide(open, 'approve')}>{open.status === 'ready' ? 'Approve and deploy' : 'Waiting for the preview build...'}</button><button className="btn danger" onClick={() => decide(open, 'reject')}>Reject</button></div>}
          {open.diff && <pre className="web-plan">{open.diff}</pre>}
        </div>}
      </Sheet>
    </section>
  )
}

// ---------------------------------------------------------------- blog posts
type Post = { _id: string; title: string; slug: string; excerpt?: string; publishedAt?: string; category?: string; readTime?: string }
type BlogView = { cms: { project_id: string; dataset: string; has_token: boolean; ready: boolean; categories: string[] }; posts: Post[]; error: string | null; can_edit: boolean }

export function BlogPosts({ siteUrl }: { siteUrl: string }) {
  const { toast, can } = useApp()
  const [b, setB] = useState<BlogView | null>(null)
  const [adding, setAdding] = useState(false)
  const [cms, setCms] = useState({ project_id: '', dataset: 'production', token: '' })
  const empty = { title: '', excerpt: '', body: '', category: '', tags: '', image_alt: '' }
  const [f, setF] = useState(empty)
  const [img, setImg] = useState<File | null>(null)
  const [topic, setTopic] = useState('')
  const [busy, setBusy] = useState('')
  const load = () => api<BlogView>('/website/blog', { quiet: true }).then(setB).catch(() => {})
  useEffect(() => { load() }, [])
  if (!b) return null
  async function connect() {
    try { await api('/website/blog/settings', { method: 'PUT', quiet: true, body: { project_id: cms.project_id.trim() || undefined, dataset: cms.dataset.trim() || undefined, token: cms.token.trim() || undefined } }); toast('Website CMS connected'); load() }
    catch (x) { toast(err(x, 'Not saved.')) }
  }
  async function draft() {
    setBusy('draft')
    try { const r = await api<{ draft: { title: string; excerpt: string; body: string; category: string; tags: string[] } }>('/website/blog/draft', { body: { topic }, quiet: true }); setF({ ...f, ...r.draft, tags: r.draft.tags.join(', '), image_alt: f.image_alt }) }
    catch (x) { toast(err(x, 'No draft just now.')) }
    setBusy('')
  }
  async function publish() {
    setBusy('publish')
    const form = new FormData()
    Object.entries({ ...f, category: f.category || b!.cms.categories[0] }).forEach(([k, v]) => form.append(k, v))
    if (img) form.append('image', img)
    try { const r = await api<{ url: string }>('/website/blog', { form, quiet: true }); toast('Published. It appears on the site within a few minutes.'); setF(empty); setImg(null); setAdding(false); load(); void r }
    catch (x) { toast(err(x, 'Not published.')) }
    setBusy('')
  }
  async function remove(p: Post) {
    if (!window.confirm(`Remove "${p.title}" from the website? This cannot be undone here.`)) return
    try { await api(`/website/blog/${p._id}`, { method: 'DELETE', quiet: true }); toast('Removed from the website'); load() }
    catch (x) { toast(err(x, 'Not removed.')) }
  }
  return (
    <section className="card">
      <div className="pad rowbtn" style={{ justifyContent: 'space-between' }}><h3 style={{ margin: 0 }}>Blog posts</h3>{b.cms.ready && b.can_edit && <button className="btn primary" onClick={() => setAdding(!adding)}>{adding ? 'Close' : 'Add a post'}</button>}</div>
      {!b.cms.ready ? (
        <div className="pad">{can('admin.settings') ? <>
          <p>Connect the website's CMS (Sanity) to add and remove posts here. In <a href="https://www.sanity.io/manage" target="_blank" rel="noreferrer">sanity.io/manage</a>, open the project: copy the <b>Project ID</b>, and under API, Tokens, add a token with <b>Editor</b> access. It is stored encrypted.</p>
          <div className="bk-row"><label className="fld"><span>Project ID</span><input value={cms.project_id} onChange={(e) => setCms({ ...cms, project_id: e.target.value })} /></label><label className="fld"><span>Dataset</span><input value={cms.dataset} onChange={(e) => setCms({ ...cms, dataset: e.target.value })} /></label></div>
          <label className="fld"><span>Editor token</span><input type="password" value={cms.token} onChange={(e) => setCms({ ...cms, token: e.target.value })} autoComplete="off" /></label>
          <div className="rowbtn"><button className="btn primary" disabled={!cms.project_id || cms.token.length < 20} onClick={connect}>Connect</button></div>
        </> : <p className="mute">An admin connects the website's CMS first.</p>}</div>
      ) : (
        <>
          {b.error && <p className="pad err">{b.error}</p>}
          {adding && <div className="pad web-form">
            <div className="rowbtn"><input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="Topic for an AI draft, for example: getting your puppy ready for the first vet visit" aria-label="Topic" style={{ flex: 1 }} /><button className="btn" disabled={topic.length < 4 || busy === 'draft'} onClick={draft}>{busy === 'draft' ? 'Writing...' : 'Draft with AI'}</button></div>
            <label className="fld"><span>Title</span><input value={f.title} maxLength={120} onChange={(e) => setF({ ...f, title: e.target.value })} /></label>
            <label className="fld"><span>Summary (shown on cards and in Google)</span><textarea rows={2} value={f.excerpt} maxLength={260} onChange={(e) => setF({ ...f, excerpt: e.target.value })} /></label>
            <label className="fld"><span>Text (## for headings, - for bullet points)</span><textarea rows={14} value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} /></label>
            <div className="bk-row"><label className="fld"><span>Category</span><select value={f.category || b.cms.categories[0]} onChange={(e) => setF({ ...f, category: e.target.value })}>{b.cms.categories.map((c) => <option key={c}>{c}</option>)}</select></label>
              <label className="fld"><span>Tags (comma separated)</span><input value={f.tags} onChange={(e) => setF({ ...f, tags: e.target.value })} /></label></div>
            <div className="bk-row"><label className="fld"><span>Main image</span><input type="file" accept="image/jpeg,image/png,image/webp" onChange={(e) => setImg(e.target.files?.[0] || null)} /></label>
              <label className="fld"><span>Image description (alt text)</span><input value={f.image_alt} onChange={(e) => setF({ ...f, image_alt: e.target.value })} /></label></div>
            <p className="mute small">Read it through before publishing; it goes live on the website.</p>
            <div className="rowbtn"><button className="btn primary" disabled={busy === 'publish' || f.title.length < 5 || f.excerpt.length < 10 || f.body.length < 50} onClick={publish}>{busy === 'publish' ? 'Publishing...' : 'Publish to the website'}</button></div>
          </div>}
          <table className="tbl"><tbody>{b.posts.map((p) => (
            <tr key={p._id}><td><a href={`${siteUrl.replace(/\/$/, '')}/blog/${p.slug}`} target="_blank" rel="noreferrer"><b>{p.title}</b></a><div className="mute small">{p.category}{p.publishedAt ? `, ${new Date(p.publishedAt).toLocaleDateString()}` : ''}</div></td>
              <td className="num">{b.can_edit && <button className="btn small danger" onClick={() => remove(p)}>Remove</button>}</td></tr>
          ))}{!b.posts.length && !b.error && <tr><td className="mute">No posts yet.</td></tr>}</tbody></table>
        </>
      )}
    </section>
  )
}
