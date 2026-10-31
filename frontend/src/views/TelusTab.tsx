import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import { useApp } from '../ctx'
import { Loading } from '../components/bits'

type Settings = { recordings_enabled: boolean; max_gb: number; keep_days: number; max_per_run_cap: number; updated_by: string | null; updated_at: string | null }
type Conn = { state: 'unknown' | 'ok' | 'failed'; code: string; checked_at: string | null; environment: string | null; write_capable_scopes: boolean; calls_last_7_days: number | null }
type Job = { id: string | null; mode: 'check' | 'recordings' | null; state: 'idle' | 'queued' | 'running' | 'done' | 'failed'; code: string; found: number; downloaded: number; already_had: number; skipped: number; failed: number; remaining: number; deleted_old: number }
type Store = { recordings_count: number; bytes: number; free_bytes: number; max_bytes: number }
type Status = { updated_at: string; connection: Conn; job: Job; storage: Store; rejected_requests: number }
type Resp = { settings: Settings; status: Status | null; bridge_ok: boolean; busy: boolean; messages: Record<string, string> }
type Form = { recordings_enabled: boolean; max_gb: number; keep_days: number; max_per_run_cap: number }

const LIMITS = { max_gb: [1, 50], keep_days: [1, 90], max_per_run_cap: [1, 1000] } as const
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, Math.round(Number.isFinite(v) ? v : lo)))

export function fmtBytes(n: number): string {
  if (!n || n < 0) return '0 MB'
  const gb = n / 1024 ** 3
  if (gb >= 1) return `${gb.toFixed(gb >= 100 ? 0 : 1)} GB`
  const mb = n / 1024 ** 2
  return `${mb.toFixed(mb >= 100 ? 0 : 1)} MB`
}

const when = (s: string | null) => (s ? new Date(s).toLocaleString('en-CA', { timeZone: 'America/Vancouver', hour12: false }) : 'Never')

export function TelusTab() {
  const { toast, run, busy } = useApp()
  const [data, setData] = useState<Resp | null>(null)
  const [error, setError] = useState(false)
  const [form, setForm] = useState<Form | null>(null)
  const [dirty, setDirty] = useState(false)
  const [lookback, setLookback] = useState(7)
  const alive = useRef(true)
  const activeRef = useRef(false)

  const load = useCallback(async () => {
    try {
      const r = await api<Resp>('/admin/telus', { quiet: true })
      if (!alive.current) return
      setData(r); setError(false)
    } catch {
      if (alive.current) setError(true)
    }
  }, [])

  const job = data?.status?.job
  const active = !!data && (data.busy || job?.state === 'queued' || job?.state === 'running')
  activeRef.current = active

  // Poll every 3 seconds while a job is waiting or running, otherwise every 30. Pause while the tab is hidden.
  useEffect(() => {
    alive.current = true
    let timer: number | undefined
    const clear = () => { if (timer !== undefined) { window.clearTimeout(timer); timer = undefined } }
    const schedule = () => {
      clear()
      if (!alive.current || document.hidden) return
      timer = window.setTimeout(async () => { await load(); schedule() }, activeRef.current ? 3000 : 30000)
    }
    const onVis = () => { if (document.hidden) clear(); else { load().then(schedule) } }
    document.addEventListener('visibilitychange', onVis)
    load().then(schedule)
    return () => { alive.current = false; clear(); document.removeEventListener('visibilitychange', onVis) }
  }, [load])

  // keep the form in step with the server until the person starts typing
  useEffect(() => {
    const s = data?.settings
    if (!s || dirty) return
    setForm({ recordings_enabled: s.recordings_enabled, max_gb: s.max_gb, keep_days: s.keep_days, max_per_run_cap: s.max_per_run_cap })
  }, [data, dirty])

  if (!data || !form) {
    return <section className="card"><h2>TELUS</h2><Loading loading={!error} error={error} /></section>
  }

  const msgs = data.messages
  const st = data.status
  const off = !data.bridge_ok
  const edit = (patch: Partial<Form>) => { setForm({ ...form, ...patch }); setDirty(true) }
  const num = (k: keyof typeof LIMITS, label: string, hint: string) => (
    <label className="f">{label}
      <input type="number" min={LIMITS[k][0]} max={LIMITS[k][1]} value={form[k]} style={{ minHeight: 44, width: 150 }}
        onChange={(e) => edit({ [k]: e.target.value === '' ? 0 : Number(e.target.value) } as Partial<Form>)}
        onBlur={() => setForm((f) => (f ? { ...f, [k]: clamp(f[k], LIMITS[k][0], LIMITS[k][1]) } : f))} />
      <span className="note" style={{ margin: 0 }}>{hint}</span>
    </label>
  )

  const save = () => run('tsave', async () => {
    const body = {
      recordings_enabled: form.recordings_enabled,
      max_gb: clamp(form.max_gb, ...LIMITS.max_gb),
      keep_days: clamp(form.keep_days, ...LIMITS.keep_days),
      max_per_run_cap: clamp(form.max_per_run_cap, ...LIMITS.max_per_run_cap),
    }
    await api('/admin/telus/settings', { method: 'PUT', body })
    setDirty(false); toast('Saved'); await load()
  })

  const check = () => run('tcheck', async () => {
    await api('/admin/telus/check', { method: 'POST' })
    toast('Checking now'); await load()
  })

  const days = clamp(lookback, 1, 30)
  const perClick = data.settings.max_per_run_cap
  const download = () => {
    if (!window.confirm(`Download new recordings now? Up to ${perClick} recordings from the last ${days} day${days === 1 ? '' : 's'} will be saved in the private folder on the server. They will not be shown in this app.`)) return
    run('tdl', async () => {
      await api('/admin/telus/recordings', { method: 'POST', body: { days, max_items: perClick } })
      toast('Download started'); await load()
    })
  }

  const working = active || busy.has('tcheck') || busy.has('tdl')
  const saving = busy.has('tsave')
  const big = { minHeight: 44, minWidth: 180 }
  const conn = st?.connection

  return (
    <>
      {off && <div className="err" role="alert">The download helper is not set up on this server yet. The buttons below are turned off.</div>}

      <section className="card">
        <h2>Connection</h2>
        <p className="sub">Checks that the server can sign in to TELUS.</p>
        <div className="setrow">
          <b>State</b>
          <span>
            {(!conn || conn.state === 'unknown') && <span className="tag">Not checked yet</span>}
            {conn?.state === 'ok' && <span className="tag ok">Working</span>}
            {conn?.state === 'failed' && <span className="tag high">Not working</span>}
          </span>
        </div>
        {conn?.state === 'failed' && <div className="warnbox" role="status">{msgs[conn.code] || msgs.error}</div>}
        <div className="setrow"><b>Last checked</b><span>{when(conn?.checked_at ?? null)}</span></div>
        <div className="setrow"><b>Calls in the last 7 days</b><span>{conn?.calls_last_7_days ?? 'Not known'}</span></div>
        <div style={{ marginTop: 12 }}>
          <button className="btn primary" style={big} onClick={check} disabled={off || working}>Check connection</button>
        </div>
      </section>

      <section className="card">
        <h2>Recordings</h2>
        <div className="warnbox" role="note">Recordings are real client conversations. They are saved only in the private folder on the server and are never shown in this app.</div>
        <div className="setrow">
          <b>Allow downloading call recordings</b>
          <button type="button" className="tog" style={{ minHeight: 44, minWidth: 80 }} aria-pressed={form.recordings_enabled} onClick={() => edit({ recordings_enabled: !form.recordings_enabled })}>{form.recordings_enabled ? 'On' : 'Off'}</button>
        </div>
        <div className="formrow">
          {num('max_gb', 'Storage limit (GB)', '1 to 50')}
          {num('keep_days', 'Keep for (days)', '1 to 90')}
          {num('max_per_run_cap', 'Most per click', '1 to 1000')}
        </div>
        <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <button className="btn primary" style={big} onClick={save} disabled={off || saving || !dirty}>Save settings</button>
          {dirty && <span className="note" style={{ margin: 0 }}>You have changes that are not saved.</span>}
        </div>
        <div className="formrow" style={{ marginTop: 16, borderTop: '1px solid var(--line)', paddingTop: 12 }}>
          <label className="f">Look back (days)
            <input type="number" min={1} max={30} value={lookback} style={{ minHeight: 44, width: 150 }}
              onChange={(e) => setLookback(e.target.value === '' ? 0 : Number(e.target.value))}
              onBlur={() => setLookback(clamp(lookback, 1, 30))} />
            <span className="note" style={{ margin: 0 }}>1 to 30</span>
          </label>
          <button className="btn primary" style={big} onClick={download} disabled={off || working || dirty || !data.settings.recordings_enabled}>Download new recordings</button>
        </div>
        {!data.settings.recordings_enabled && <p className="note">Turn the switch on and save to allow downloads.</p>}
        {data.settings.recordings_enabled && dirty && <p className="note">Save your changes before downloading.</p>}
      </section>

      <section className="card">
        <h2>Last run</h2>
        {!st || !job || job.state === 'idle' ? (
          <p className="sub" style={{ margin: 0 }}>{job?.state === 'idle' ? 'Nothing is running.' : 'No run yet.'}</p>
        ) : (
          <>
            <div className="setrow">
              <b>{job.mode === 'check' ? 'Connection check' : job.mode === 'recordings' ? 'Recording download' : 'Job'}</b>
              <span>
                {job.state === 'queued' && <span className="tag acc">Waiting to start</span>}
                {job.state === 'running' && <span className="tag acc">Running</span>}
                {job.state === 'done' && <span className="tag ok">Finished</span>}
                {job.state === 'failed' && <span className="tag high">Did not finish</span>}
              </span>
            </div>
            {job.state === 'running' && job.mode === 'recordings' && (
              <p className="note" role="status" style={{ fontSize: 14 }}>
                Downloaded {job.downloaded} so far. {job.remaining} left to go.
              </p>
            )}
            {(job.state === 'done' || job.state === 'failed') && job.code !== 'ok' && (
              <div className={job.state === 'failed' ? 'warnbox' : 'okbox'} role="status">{msgs[job.code] || msgs.error}</div>
            )}
            {(job.state === 'done' || job.state === 'failed') && job.code === 'ok' && <div className="okbox" role="status">{msgs.ok}</div>}
            {job.mode === 'recordings' && (
              <>
                {([['Found', job.found], ['Downloaded', job.downloaded], ['Already had', job.already_had], ['Skipped', job.skipped], ['Failed', job.failed], ['Remaining', job.remaining], ['Deleted old', job.deleted_old]] as [string, number][]).map(([l, v]) => (
                  <div className="setrow" key={l}><b>{l}</b><span>{v}</span></div>
                ))}
              </>
            )}
          </>
        )}
      </section>

      <TextingPanel />
      <section className="card">
        <h2>Storage</h2>
        <div className="setrow"><b>Recordings saved</b><span>{st?.storage.recordings_count ?? 0}</span></div>
        <div className="setrow"><b>Space used</b><span>{fmtBytes(st?.storage.bytes ?? 0)}</span></div>
        <div className="setrow"><b>Free space on the server</b><span>{st ? fmtBytes(st.storage.free_bytes) : 'Not known'}</span></div>
        <div className="setrow"><b>Storage limit</b><span>{data.settings.max_gb} GB</span></div>
      </section>
      {error && <p className="note">Could not refresh just now. It will try again.</p>}
    </>
  )
}


type SmsView = { mode: 'practice' | 'test_numbers_only' | 'live'; test_numbers: string[]; bridge_ok: boolean; sender_ready: boolean; live_phrase: string }

/** Texting mode: practice (nothing leaves the server), test numbers only, or live. The send helper enforces it again on its side. */
function TextingPanel() {
  const { toast, refreshMe } = useApp()
  const [v, setV] = useState<SmsView | null>(null)
  const [mode, setMode] = useState<SmsView['mode']>('practice')
  const [nums, setNums] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const load = () => api<SmsView>('/admin/sms', { quiet: true }).then((r) => { setV(r); setMode(r.mode); setNums(r.test_numbers.join(', ')) }).catch(() => {})
  useEffect(() => { load() }, [])
  async function save() {
    setBusy(true)
    try {
      const r = await api<SmsView>('/admin/sms', { method: 'PUT', body: { mode, test_numbers: nums.split(/[,\n]/).map((x) => x.trim()).filter(Boolean), confirm: confirm || null }, quiet: true })
      setV(r); setNums(r.test_numbers.join(', ')); setConfirm(''); toast('Texting mode saved'); refreshMe()
    } catch (x) { toast(x instanceof ApiError && x.message && x.message !== 'error' ? x.message : 'Not saved.') }
    setBusy(false)
  }
  if (!v) return null
  return (
    <section className="card">
      <h2>Texting</h2>
      <p className="note">Practice: nothing leaves the server. Test numbers only: only the numbers below get real texts, everyone else stays in practice. Live: anyone who has agreed to texts. Campaigns always stay in practice.</p>
      <div className="setrow"><b>Sending helper</b><span>{v.sender_ready ? 'Ready' : 'Not set up yet'}</span></div>
      <div className="setrow"><b>Mode</b>
        <select value={mode} onChange={(e) => setMode(e.target.value as SmsView['mode'])} aria-label="Texting mode">
          <option value="practice">Practice (nothing is sent)</option><option value="test_numbers_only">Test numbers only</option><option value="live">Live</option>
        </select></div>
      <label className="setrow"><b>Test numbers</b><input value={nums} onChange={(e) => setNums(e.target.value)} placeholder="604 555 0161" aria-label="Test numbers, separated by commas" /></label>
      {mode === 'live' && v.mode !== 'live' && <label className="setrow"><b>Type {v.live_phrase}</b><input value={confirm} onChange={(e) => setConfirm(e.target.value)} aria-label="Confirm live texting" /></label>}
      <div className="rowbtn"><button className="btn primary" disabled={busy} onClick={save}>Save texting mode</button></div>
    </section>
  )
}
