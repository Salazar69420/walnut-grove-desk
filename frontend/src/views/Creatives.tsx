import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import { useApp } from '../ctx'
import { Empty, Modal, SkeletonRows } from '../components/ui'

type Kind = 'image' | 'video'
type Fmt = { key: string; label: string; blurb: string }
type Opts = { quality?: string; aspect_ratio?: string; resolution?: string; duration?: number }
type Item = {
  id: number; kind: Kind; format: string; title: string; status: 'interview' | 'review' | 'generating' | 'done' | 'failed'; thinking: boolean
  question: { question: string; options: string[] } | null; answered: number; options: Opts; estimate_usd: number; cost_usd: number; error: string
  has_reference: boolean; share_url: string | null; media_url: string | null; caption: string; sms_text: string; created_at: string; done_at: string | null
  prompt?: string; plan?: { hook?: string; arc?: string; checks?: string; shots?: string }; qa?: { question: string; answer: string }[]
}
type Settings = { has_key: boolean; key_last4: string; monthly_budget_usd: number; spent_this_month_usd: number }
type View = { items: Item[]; formats: Record<Kind, Fmt[]>; settings: Settings; can_draft: boolean; can_make: boolean; can_settings: boolean }

const err = (x: unknown, d: string) => (x instanceof ApiError && x.message && x.message !== 'error' ? x.message : d)
const usd = (n: number) => `$${n < 1 ? n.toFixed(2) : n.toFixed(2).replace(/\.00$/, '')}`
const STATUS: Record<Item['status'], string> = { interview: 'Questions', review: 'Ready to make', generating: 'Being made', done: 'Done', failed: 'Failed' }

/** Creative studio: a short interview about one clinic idea, then an image or short video for social posts and campaigns. */
export function CreativeStudio() {
  const { toast } = useApp()
  const [v, setV] = useState<View | null>(null)
  const [kind, setKind] = useState<Kind>('image')
  const [open, setOpen] = useState<number | null>(null)
  const load = () => api<View>('/creatives', { quiet: true }).then(setV).catch(() => {})
  useEffect(() => { load() }, [])
  useEffect(() => {
    if (!v?.items.some((i) => i.status === 'generating')) return
    const t = window.setTimeout(load, 8000)
    return () => window.clearTimeout(t)
  }, [v])
  if (!v) return <div className="php"><SkeletonRows n={6} /></div>

  async function start(f: Fmt) {
    try { const r = await api<{ item: Item }>('/creatives', { body: { kind, format: f.key }, quiet: true }); setOpen(r.item.id); load() }
    catch (x) { toast(err(x, 'Could not start it.')) }
  }
  async function remove(i: Item) {
    if (!window.confirm(`Delete "${i.title}"? Its share link stops working.`)) return
    try { await api(`/creatives/${i.id}`, { method: 'DELETE', quiet: true }); load() } catch (x) { toast(err(x, 'Not deleted.')) }
  }
  const copy = (u: string) => navigator.clipboard?.writeText(u).then(() => toast('Link copied'), () => toast(u))

  return (
    <div className="php web cs">
      <div className="hd2">
        <div><h1>Creative studio</h1><p className="lead">Pick an idea, answer a few quick questions about it, and get a clinic image or short video with a caption and a campaign text, ready to post or attach to a campaign.</p></div>
        <span className="tag">{usd(v.settings.spent_this_month_usd)} of {usd(v.settings.monthly_budget_usd)} used this month</span>
      </div>

      {v.can_draft && <section className="card pad">
        <div className="rowbtn" style={{ justifyContent: 'space-between' }}>
          <h3 style={{ margin: 0 }}>Start something new</h3>
          <div className="seg"><button className={kind === 'image' ? 'on' : ''} onClick={() => setKind('image')}>Images</button><button className={kind === 'video' ? 'on' : ''} onClick={() => setKind('video')}>Short videos</button></div>
        </div>
        <div className="auto-ideas" style={{ marginTop: 12 }}>
          {v.formats[kind].map((f) => (
            <div key={f.key} className="auto-idea"><b>{f.label}</b><p>{f.blurb}</p><button className="btn small" onClick={() => start(f)}>Start</button></div>
          ))}
        </div>
      </section>}

      <section className="card">
        <h3 className="pad" style={{ marginBottom: 0 }}>Library</h3>
        {v.items.length === 0 ? <div className="pad"><Empty icon="play" title="Nothing made yet" text="Start with an idea above. The questions take about a minute." /></div> : (
          <div className="cs-grid pad">
            {v.items.map((i) => (
              <div key={i.id} className="cs-card">
                <button className="cs-media" onClick={() => setOpen(i.id)} aria-label={`Open ${i.title}`}>
                  {i.media_url ? (i.kind === 'video' ? <video src={i.media_url} muted playsInline preload="metadata" /> : <img src={i.media_url} alt={i.title} loading="lazy" />)
                    : <span className="cs-ph">{i.status === 'generating' ? 'Being made...' : i.kind === 'video' ? 'Video' : 'Image'}</span>}
                </button>
                <div className="cs-meta">
                  <b>{i.title}</b>
                  <small className={i.status === 'failed' ? 'bad' : 'mute'}>{STATUS[i.status]}{i.cost_usd ? ` · ${usd(i.cost_usd)}` : ''}</small>
                  <div className="rowbtn">
                    <button className="btn small" onClick={() => setOpen(i.id)}>{i.status === 'interview' ? 'Continue' : 'Open'}</button>
                    {i.share_url && <button className="btn small ghost" onClick={() => copy(i.share_url!)}>Copy link</button>}
                    {v.can_draft && <button className="btn small ghost" onClick={() => remove(i)}>Delete</button>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {v.can_settings && <StudioSettings s={v.settings} onSaved={load} />}
      {open !== null && <Studio id={open} v={v} onClose={() => { setOpen(null); load() }} onCopy={copy} />}
    </div>
  )
}

function Studio({ id, v, onClose, onCopy }: { id: number; v: View; onClose: () => void; onCopy: (u: string) => void }) {
  const { toast } = useApp()
  const [c, setC] = useState<Item | null>(null)
  const [free, setFree] = useState('')
  const [busy, setBusy] = useState(false)
  const file = useRef<HTMLInputElement>(null)
  const get = () => api<{ item: Item }>(`/creatives/${id}`, { quiet: true }).then((r) => { setC(r.item); return r.item }).catch(() => null)
  useEffect(() => { get() }, [id])
  useEffect(() => {
    if (!c || !(c.thinking || c.status === 'generating')) return
    const t = window.setTimeout(get, c.thinking ? 1500 : 6000)
    return () => window.clearTimeout(t)
  }, [c])

  async function post(path: string, body: unknown, fail: string) {
    setBusy(true)
    try { const r = await api<{ item: Item }>(`/creatives/${id}/${path}`, { body, quiet: true }); setC(r.item); return true }
    catch (x) { toast(err(x, fail)); return false }
    finally { setBusy(false) }
  }
  async function answer(a: string) { if (a.trim() && await post('answer', { answer: a.trim() }, 'That answer did not go through.')) setFree('') }
  async function save(p: Partial<Item>) {
    try { const r = await api<{ item: Item }>(`/creatives/${id}`, { method: 'PUT', body: p, quiet: true }); setC(r.item) } catch (x) { toast(err(x, 'Not saved.')) }
  }
  async function upload(f: File) {
    const form = new FormData(); form.append('file', f)
    try { const r = await api<{ item: Item }>(`/creatives/${id}/reference`, { form, quiet: true }); setC(r.item); toast('Photo added') } catch (x) { toast(err(x, 'The photo did not upload.')) }
  }
  async function make() {
    if (!c) return
    if (!window.confirm(`Make this ${c.kind} now? It costs about ${usd(c.estimate_usd)}.`)) return
    if (await post('generate', {}, 'It could not start.')) toast(c.kind === 'video' ? 'Making the video. It takes a few minutes; you can close this.' : 'Making the image. It takes about a minute.')
  }

  const footer = <button className="btn" onClick={onClose}>Close</button>
  if (!c) return <Modal open onClose={onClose} title="Creative studio" footer={footer}><SkeletonRows n={3} /></Modal>
  const fmt = v.formats[c.kind].find((f) => f.key === c.format)

  return (
    <Modal open onClose={onClose} title={`${c.title}${fmt && fmt.label !== c.title ? ` (${fmt.label})` : ''}`} footer={footer}>
      <div className="bk cs-studio">
        {c.error && <p className="bad small">{c.error}</p>}

        {c.status === 'interview' && <>
          {c.thinking ? <p className="mute">{c.answered ? 'Thinking about your answer...' : 'Getting the first question ready...'}</p> : c.question ? <>
            <p className="cs-q">{c.question.question}</p>
            <div className="cs-opts">{c.question.options.map((o) => <button key={o} className="btn" disabled={busy} onClick={() => answer(o)}>{o}</button>)}</div>
            <div className="rowbtn"><input value={free} maxLength={1000} onChange={(e) => setFree(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') answer(free) }} placeholder="Or say it in your own words" style={{ flex: 1 }} />
              <button className="btn primary" disabled={busy || !free.trim()} onClick={() => answer(free)}>Answer</button></div>
          </> : null}
          <p className="mute small">Question {Math.min(c.answered + 1, 6)} of at most 6. {c.answered > 0 && !c.thinking && <button className="linkbtn" disabled={busy} onClick={() => post('finish', {}, 'It could not start writing.')}>That is enough, write it now</button>}</p>
          {c.error && !c.thinking && !c.question && <button className="btn" onClick={() => post('finish', {}, 'It could not start writing.')}>Write it now</button>}
        </>}

        {c.status !== 'interview' && c.thinking && <p className="mute">Writing it up...</p>}

        {(c.status === 'generating' || c.status === 'done') && <div className="cs-out">
          {c.media_url ? (c.kind === 'video' ? <video src={c.media_url} controls playsInline /> : <img src={c.media_url} alt={c.title} />)
            : <p className="mute">{c.kind === 'video' ? 'The video is being made. Videos take a few minutes.' : 'The image is being made.'}</p>}
          {c.share_url && <div className="rowbtn"><input readOnly value={c.share_url} style={{ flex: 1 }} /><button className="btn small" onClick={() => onCopy(c.share_url!)}>Copy link</button>
            {c.media_url && <a className="btn small ghost" href={c.media_url} download>Download</a>}</div>}
        </div>}

        {(c.status === 'review' || c.status === 'failed' || c.status === 'done' || c.status === 'generating') && !c.thinking && <>
          {c.plan?.hook && <div className="cs-plan"><b>The idea</b><p>{c.plan.hook}{c.plan.arc ? `. ${c.plan.arc}` : ''}</p></div>}
          <Field label="Title" value={c.title} max={160} rows={1} onSave={(t) => save({ title: t })} />
          <Field label="Social caption" value={c.caption} max={1200} rows={4} onSave={(t) => save({ caption: t })} />
          <Field label="Campaign text (the link is added at the end)" value={c.sms_text} max={300} rows={2} onSave={(t) => save({ sms_text: t })} />
        </>}

        {(c.status === 'review' || c.status === 'failed') && !c.thinking && <>
          <details><summary>The {c.kind} description it will be made from</summary>
            <Field label="" value={c.prompt || ''} max={6000} rows={8} onSave={(t) => save({ prompt: t })} /></details>
          <div className="bk-row">
            {c.kind === 'image' ? <>
              <label className="fld"><span>Quality</span><select value={c.options.quality} onChange={(e) => save({ options: { ...c.options, quality: e.target.value } })}>
                <option value="low">Draft</option><option value="medium">Good</option><option value="high">Best</option></select></label>
              <label className="fld"><span>Shape</span><select value={c.options.aspect_ratio} onChange={(e) => save({ options: { ...c.options, aspect_ratio: e.target.value } })}>
                <option value="4:5">Instagram post (4:5)</option><option value="1:1">Square (1:1)</option><option value="9:16">Story (9:16)</option><option value="16:9">Wide (16:9)</option></select></label>
            </> : <>
              <label className="fld"><span>Size</span><select value={c.options.resolution} onChange={(e) => save({ options: { ...c.options, resolution: e.target.value } })}>
                <option value="480p">480p</option><option value="720p">720p</option><option value="1080p">1080p</option></select></label>
              <label className="fld"><span>Length</span><select value={c.options.duration} onChange={(e) => save({ options: { ...c.options, duration: Number(e.target.value) } })}>
                {[5, 8, 10, 12, 15].map((d) => <option key={d} value={d}>{d} seconds</option>)}</select></label>
            </>}
          </div>
          <div className="rowbtn">
            <input ref={file} type="file" accept="image/jpeg,image/png,image/webp" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = '' }} />
            <button className="btn small ghost" onClick={() => file.current?.click()}>{c.has_reference ? 'Change the photo' : 'Add a real clinic photo (optional)'}</button>
            <button className="btn small ghost" disabled={busy} onClick={() => post('finish', {}, 'It could not rewrite.')}>Write it again</button>
          </div>
          {c.qa && c.qa.length > 0 && <details><summary>Your answers</summary><ul className="auto-runs">{c.qa.map((q, n) => <li key={n}><b>{q.question}</b> {q.answer}</li>)}</ul></details>}
          <div className="cs-make">
            <span>About <b>{usd(c.estimate_usd)}</b> to make</span>
            {v.can_make ? (v.settings.has_key ? <button className="btn primary" disabled={busy} onClick={make}>Make it</button> : <span className="mute small">Add the WaveSpeed key in Studio settings first.</span>)
              : <span className="mute small">The owner makes it. It is saved here for them.</span>}
          </div>
        </>}
      </div>
    </Modal>
  )
}

function Field({ label, value, max, rows, onSave }: { label: string; value: string; max: number; rows: number; onSave: (t: string) => void }) {
  const [t, setT] = useState(value)
  useEffect(() => setT(value), [value])
  const body = rows === 1
    ? <input value={t} maxLength={max} onChange={(e) => setT(e.target.value)} onBlur={() => t !== value && onSave(t)} />
    : <textarea rows={rows} value={t} maxLength={max} onChange={(e) => setT(e.target.value)} onBlur={() => t !== value && onSave(t)} />
  return label ? <label className="fld"><span>{label}</span>{body}</label> : body
}

function StudioSettings({ s, onSaved }: { s: Settings; onSaved: () => void }) {
  const { toast } = useApp()
  const [open, setOpen] = useState(!s.has_key)
  const [key, setKey] = useState('')
  const [budget, setBudget] = useState(s.monthly_budget_usd)
  async function save(clear = false) {
    try {
      await api('/creatives/settings/studio', { method: 'PUT', quiet: true, body: { ...(key ? { wavespeed_key: key } : {}), ...(clear ? { clear_key: true } : {}), monthly_budget_usd: Number(budget) } })
      toast('Studio settings saved'); setKey(''); onSaved()
    } catch (x) { toast(err(x, 'Not saved.')) }
  }
  return (
    <section className="card pad">
      <div className="rowbtn" style={{ justifyContent: 'space-between' }}><h3 style={{ margin: 0 }}>Studio settings</h3><span className={'tag ' + (s.has_key ? 'ok' : '')}>{s.has_key ? `WaveSpeed key ending ${s.key_last4}` : 'No WaveSpeed key yet'}</span></div>
      {!open ? <button className="linkbtn" onClick={() => setOpen(true)}>Change the settings</button> : (
        <div className="bk">
          <p className="mute small">Images and videos are made by WaveSpeed (wavespeed.ai), paid by the clinic per item. The key is stored encrypted. Nothing is made once this month's budget is used up.</p>
          <div className="bk-row">
            <label className="fld"><span>WaveSpeed API key{s.has_key ? ' (leave empty to keep)' : ''}</span><input type="password" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" /></label>
            <label className="fld" style={{ maxWidth: 180 }}><span>Monthly budget (USD)</span><input type="number" min={0} max={2000} value={budget} onChange={(e) => setBudget(Number(e.target.value))} /></label>
          </div>
          <div className="rowbtn"><button className="btn primary" onClick={() => save()}>Save</button>{s.has_key && <button className="btn ghost" onClick={() => save(true)}>Remove the key</button>}</div>
        </div>
      )}
    </section>
  )
}
