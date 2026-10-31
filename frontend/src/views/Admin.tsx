import { FormEvent, useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { useApp } from '../ctx'
import { useLive } from '../live'
import { Head, Loading } from '../components/bits'
import { TelusTab } from './TelusTab'

type Tab = 'users' | 'audit' | 'import' | 'settings' | 'telus'

export function Admin() {
  const [tab, setTab] = useState<Tab>('users')
  const tabs: [Tab, string][] = [['users', 'Users'], ['audit', 'Audit log'], ['import', 'Import'], ['settings', 'Settings'], ['telus', 'TELUS']]
  return (
    <>
      <Head title="Admin" lead="Accounts, the audit log, data imports and settings. Only admins see this page." />
      <div className="seg tabs" role="group" aria-label="Admin sections" style={{ width: 'fit-content', maxWidth: '100%' }}>
        {tabs.map(([k, l]) => <button key={k} aria-pressed={tab === k} onClick={() => setTab(k)}>{l}</button>)}
      </div>
      {tab === 'users' && <Users />}
      {tab === 'audit' && <Audit />}
      {tab === 'import' && <Import />}
      {tab === 'settings' && <Settings />}
      {tab === 'telus' && <TelusTab />}
    </>
  )
}

type U = { id: number; username: string; display_name: string; title: string; role: string; is_admin: boolean; active: boolean; locked: boolean; last_login_at: string | null }

function Users() {
  const { me, toast, run, busy } = useApp()
  const users = useLive<{ items: U[] }>('/admin/users', ['users'])
  const [f, setF] = useState({ username: '', display_name: '', title: '', role: 'desk', is_admin: false })
  const [shown, setShown] = useState<{ name: string; password: string } | null>(null)
  async function add(e: FormEvent) {
    e.preventDefault()
    await run('uadd', async () => {
      const r = await api<{ item: U; password: string }>('/admin/users', { body: { ...f, title: f.title || undefined } })
      setShown({ name: r.item.display_name, password: r.password }); toast('User added')
      setF({ username: '', display_name: '', title: '', role: 'desk', is_admin: false }); users.reload()
    })
  }
  const patch = (u: U, body: object, msg: string) => run('u' + u.id, async () => { await api(`/admin/users/${u.id}`, { method: 'PATCH', body }); toast(msg); users.reload() })
  return (
    <>
      {shown && (
        <div className="okbox" role="status">
          One-time password for {shown.name}. It will not be shown again.
          <div className="mono-box" style={{ marginTop: 6, color: 'var(--ink)' }} data-testid="onetime">{shown.password}</div>
          <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
            <button className="btn small" onClick={() => { navigator.clipboard?.writeText(shown.password).then(() => toast('Copied')).catch(() => {}) }}>Copy</button>
            <button className="btn small" onClick={() => setShown(null)}>Hide</button>
          </div>
        </div>
      )}
      <section className="card">
        <h2>Users</h2>
        <Loading loading={users.loading && !users.data} error={users.error && !users.data} />
        <div className="tablewrap"><table>
          <thead><tr><th>Name</th><th>Username</th><th>Role</th><th>Admin</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody>{(users.data?.items || []).map((u) => (
            <tr key={u.id}>
              <td>{u.display_name}<div className="note" style={{ margin: 0 }}>{u.title}</div></td>
              <td className="mono">{u.username}</td>
              <td>{u.role}</td>
              <td>{u.is_admin ? <span className="tag acc">Admin</span> : ''}</td>
              <td>{u.active ? (u.locked ? <span className="tag warn">Locked</span> : <span className="tag ok">Active</span>) : <span className="tag">Inactive</span>}</td>
              <td style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {u.id !== me?.user.id && <button className="btn small" disabled={busy.has('u' + u.id)} onClick={() => patch(u, { active: !u.active }, u.active ? 'User deactivated' : 'User activated')}>{u.active ? 'Deactivate' : 'Activate'}</button>}
                <button className="btn small" disabled={busy.has('r' + u.id)} onClick={() => { if (!window.confirm(`Reset the password for ${u.display_name}?`)) return; run('r' + u.id, async () => { const r = await api<{ password: string }>(`/admin/users/${u.id}/reset-password`, { method: 'POST' }); setShown({ name: u.display_name, password: r.password }); toast('Password reset'); users.reload() }) }}>Reset password</button>
              </td>
            </tr>
          ))}</tbody></table></div>
      </section>
      <section className="card">
        <h2>Add a user</h2>
        <form onSubmit={add}>
          <div className="formrow">
            <label className="f">Username<input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} required maxLength={32} autoCapitalize="none" /></label>
            <label className="f">Name<input value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} required maxLength={120} /></label>
            <label className="f">Title<input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} maxLength={120} /></label>
            <label className="f">Role<select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}><option value="owner">Owner</option><option value="desk">Front desk</option><option value="nurse">Nurse</option></select></label>
            <label className="f" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}><input type="checkbox" checked={f.is_admin} onChange={(e) => setF({ ...f, is_admin: e.target.checked })} style={{ minHeight: 20, width: 20 }} />Admin</label>
            <button className="btn primary" type="submit" disabled={busy.has('uadd')}>Add user</button>
          </div>
        </form>
      </section>
    </>
  )
}

type AE = { id: number; ts: string; tool: string; actor_name: string; action: string; text: string; ip: string | null }
function Audit() {
  const [off, setOff] = useState(0)
  const a = useLive<{ items: AE[]; total: number; limit: number }>(`/admin/audit?limit=50&offset=${off}`, [])
  const d = a.data
  return (
    <section className="card">
      <div className="hd2"><h2>Audit log</h2><a className="exp" href="/api/export/audit.csv" download>Download CSV</a></div>
      <Loading loading={a.loading && !d} error={a.error && !d} />
      <div className="tablewrap"><table>
        <thead><tr><th>Time</th><th>Tool</th><th>Who</th><th>Action</th><th>What</th><th>IP</th></tr></thead>
        <tbody>{(d?.items || []).map((e) => (
          <tr key={e.id}><td className="mono" style={{ whiteSpace: 'nowrap' }}>{new Date(e.ts).toLocaleString('en-CA', { timeZone: 'America/Vancouver', hour12: false })}</td><td>{e.tool}</td><td>{e.actor_name}</td><td className="mono">{e.action}</td><td>{e.text}</td><td className="mono">{e.ip}</td></tr>
        ))}</tbody></table></div>
      {d && (
        <div className="pager">
          <button className="btn small" disabled={off === 0} onClick={() => setOff(Math.max(0, off - 50))}>Newer</button>
          <span className="note" style={{ margin: 0 }}>{d.total === 0 ? 0 : off + 1} to {Math.min(off + 50, d.total)} of {d.total}</span>
          <button className="btn small" disabled={off + 50 >= d.total} onClick={() => setOff(off + 50)}>Older</button>
        </div>
      )}
    </section>
  )
}

function Import() {
  const { toast, run, busy } = useApp()
  const [kind, setKind] = useState('calls')
  const [file, setFile] = useState<File | null>(null)
  const [res, setRes] = useState<{ accepted: number; rejected: { row: number; error: string }[]; kind: string } | null>(null)
  async function go(e: FormEvent) {
    e.preventDefault()
    if (!file) return
    await run('imp', async () => {
      const fd = new FormData(); fd.append('file', file)
      const r = await api<any>(`/admin/import/${kind}`, { method: 'POST', form: fd })
      setRes(r); toast('Import finished')
    })
  }
  return (
    <section className="card">
      <h2>Import</h2>
      <p className="sub">Upload a CSV or JSONL file, up to 10 MB. Rows with a problem are skipped and listed below.</p>
      <form className="formrow" onSubmit={go}>
        <label className="f">What is in the file<select value={kind} onChange={(e) => setKind(e.target.value)}>
          {['calls', 'appointments', 'clients', 'pets', 'lab_results', 'analytics'].map((k) => <option key={k} value={k}>{k.replace('_', ' ')}</option>)}</select></label>
        <label className="f">File<input type="file" accept=".csv,.jsonl,.json" onChange={(e) => setFile(e.target.files?.[0] || null)} /></label>
        <button className="btn primary" type="submit" disabled={!file || busy.has('imp')}>Upload</button>
      </form>
      {res && (
        <div style={{ marginTop: 12 }} data-testid="import-result">
          <div className="okbox">{res.accepted} rows accepted, {res.rejected.length} rejected.</div>
          {res.rejected.length > 0 && <ul>{res.rejected.map((r, i) => <li key={i}>Row {r.row}: {r.error}</li>)}</ul>}
        </div>
      )}
    </section>
  )
}

const RULE_FIELDS: [string, string][] = [['load_limit', 'Calls an hour per person that counts as stretched'], ['min_weekdays', 'Weekdays a pattern must repeat'], ['missed_min', 'Missed calls a week before suggesting texts'], ['recall_min', 'Overdue clients before suggesting a recall'], ['recall_batch', 'Recall drafts per batch'], ['results_age_days', 'Days before a result counts as old']]

function Settings() {
  const { me, setMe, refreshMe, toast, run, busy } = useApp()
  const st = useLive<{ settings: Record<string, any> }>('/admin/settings', [])
  const [clinic, setClinic] = useState('')
  const [banner, setBanner] = useState(false)
  const [rules, setRules] = useState<Record<string, number>>({})
  useEffect(() => {
    const s = st.data?.settings
    if (!s) return
    setClinic(s.clinic_name || ''); setBanner(!!s.banner_on)
    setRules({ load_limit: 12, min_weekdays: 3, missed_min: 20, recall_min: 50, recall_batch: 10, results_age_days: 2, ...(s.rules || {}) })
  }, [st.data])
  void me; void setMe
  async function save(e: FormEvent) {
    e.preventDefault()
    await run('sset', async () => {
      await api('/admin/settings', { method: 'PUT', body: { settings: { clinic_name: clinic, banner_on: banner, rules } } })
      toast('Saved'); st.reload(); refreshMe()
    })
  }
  const reset = () => {
    if (!window.confirm('Reset demo data? This replaces callbacks, results, messages, schedule and overnight items with the example set.')) return
    run('reset', async () => { await api('/admin/reset-demo', { method: 'POST' }); toast('Demo reset') })
  }
  return (
    <>
      <section className="card">
        <h2>Clinic</h2>
        <Loading loading={st.loading && !st.data} error={st.error && !st.data} />
        <form onSubmit={save}>
          <div className="formrow">
            <label className="f">Clinic name<input value={clinic} onChange={(e) => setClinic(e.target.value)} maxLength={120} required style={{ minWidth: 260 }} /></label>
            <div className="setrow" style={{ border: 0, padding: 0 }}>
              <span><b>Show the "Prototype, example data" label</b></span>
              <button type="button" className="tog" aria-pressed={banner} onClick={() => setBanner(!banner)} style={{ marginLeft: 12 }}>{banner ? 'On' : 'Off'}</button>
            </div>
          </div>
          <h2 style={{ marginTop: 16 }}>Rule thresholds</h2>
          <div className="cols2">
            {RULE_FIELDS.map(([k, l]) => (
              <label className="f" key={k}>{l}<input type="number" min={0} value={rules[k] ?? 0} onChange={(e) => setRules({ ...rules, [k]: Number(e.target.value) })} /></label>
            ))}
          </div>
          <div style={{ marginTop: 12 }}><button className="btn primary" type="submit" disabled={busy.has('sset')}>Save settings</button></div>
        </form>
      </section>
      <AiSection />
      <RetellSection />
      <section className="card">
        <h2>Reset demo data</h2>
        <p className="sub">Puts the example callbacks, results, messages, schedule and overnight items back to the start. Accounts are kept.</p>
        <button className="btn" onClick={reset} disabled={busy.has('reset')}>Reset demo data</button>
      </section>
    </>
  )
}

type Ai = { provider: string; base_url: string; model: string; key_set: boolean; key_last4: string | null; word_brief: boolean; emergency_check: boolean; allow_transcript_labeling: boolean; warning: string }
function AiSection() {
  const { toast, run, busy } = useApp()
  const ai = useLive<Ai>('/admin/settings/ai', [])
  const [f, setF] = useState<Ai | null>(null)
  const [key, setKey] = useState('')
  const [test, setTest] = useState<{ ok: boolean; detail: string } | null>(null)
  useEffect(() => { if (ai.data) setF(ai.data) }, [ai.data])
  if (!f) return <section className="card"><h2>AI provider</h2><Loading loading={ai.loading} error={ai.error} /></section>
  const DEF: Record<string, [string, string]> = { deepseek: ['https://api.deepseek.com', 'deepseek-chat'], openrouter: ['https://openrouter.ai/api/v1', ''] }
  const pick = (p: string) => setF({ ...f, provider: p, base_url: DEF[p] ? DEF[p][0] : f.base_url, model: DEF[p] && DEF[p][1] ? DEF[p][1] : f.model })
  const tog = (k: 'word_brief' | 'emergency_check' | 'allow_transcript_labeling', l: string) => (
    <div className="setrow"><b>{l}</b><button type="button" className="tog" aria-pressed={f[k]} onClick={() => setF({ ...f, [k]: !f[k] })}>{f[k] ? 'On' : 'Off'}</button></div>
  )
  async function save(e: FormEvent) {
    e.preventDefault()
    await run('aisave', async () => {
      const body: any = { provider: f!.provider, base_url: f!.base_url, model: f!.model, word_brief: f!.word_brief, emergency_check: f!.emergency_check, allow_transcript_labeling: f!.allow_transcript_labeling }
      if (key) body.api_key = key
      await api('/admin/settings/ai', { method: 'PUT', body })
      setKey(''); toast('Saved'); ai.reload()
    })
  }
  return (
    <section className="card">
      <h2>AI provider</h2>
      <div className="warnbox" role="note">{f.warning}</div>
      <form onSubmit={save}>
        <div className="formrow">
          <label className="f">Provider<select value={f.provider} onChange={(e) => pick(e.target.value)}><option value="off">Off</option><option value="deepseek">DeepSeek</option><option value="openrouter">OpenRouter</option></select></label>
          <label className="f">Base URL<input value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} style={{ minWidth: 260 }} /></label>
          <label className="f">Model<input value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} /></label>
          <label className="f">API key<input type="password" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="new-password" placeholder={f.key_set ? 'Type a new key to replace it' : 'Paste the key'} style={{ minWidth: 240 }} /></label>
        </div>
        <p className="note">{f.key_set ? `Key saved, ends in ${f.key_last4 ?? ''}` : 'No key saved yet.'} The key is write-only and is never shown again.</p>
        {tog('word_brief', 'Word the brief')}
        {tog('emergency_check', 'Second emergency check')}
        {tog('allow_transcript_labeling', 'Allow call transcripts to be sent for labeling')}
        <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
          <button className="btn primary" type="submit" disabled={busy.has('aisave')}>Save AI settings</button>
          <button className="btn" type="button" disabled={busy.has('aitest')} onClick={() => run('aitest', async () => { setTest(await api('/admin/settings/ai/test', { method: 'POST' })) })}>Test connection</button>
        </div>
        {test && <div className={test.ok ? 'okbox' : 'err'} role="status" style={{ marginTop: 10 }}>{test.ok ? 'Connection works. ' : 'Connection failed. '}{test.detail}</div>}
      </form>
    </section>
  )
}

type Rt = { enabled: boolean; dry_run: boolean; secret?: string; new_secret?: string; api_key_set?: boolean; key_set?: boolean; key_last4?: string | null; api_key_last4?: string | null; secret_set?: boolean }
function RetellSection() {
  const { toast, run, busy } = useApp()
  const rt = useLive<Rt>('/admin/settings/retell', [])
  const [f, setF] = useState({ enabled: false, dry_run: true })
  const [key, setKey] = useState('')
  const [secret, setSecret] = useState('')
  useEffect(() => { if (rt.data) setF({ enabled: !!rt.data.enabled, dry_run: rt.data.dry_run !== false }) }, [rt.data])
  const put = useCallback(async (body: object) => {
    const r = await api<Rt>('/admin/settings/retell', { method: 'PUT', body })
    return r
  }, [])
  const keySet = rt.data?.api_key_set ?? rt.data?.key_set
  return (
    <section className="card">
      <h2>Retell (phone assistant)</h2>
      <p className="sub">Off by default. In dry run, nothing is ever sent or called.</p>
      <Loading loading={rt.loading && !rt.data} error={rt.error && !rt.data} />
      <div className="setrow"><b>Enabled</b><button className="tog" aria-pressed={f.enabled} onClick={() => setF({ ...f, enabled: !f.enabled })}>{f.enabled ? 'On' : 'Off'}</button></div>
      <div className="setrow"><b>Dry run</b><button className="tog" aria-pressed={f.dry_run} onClick={() => setF({ ...f, dry_run: !f.dry_run })}>{f.dry_run ? 'On' : 'Off'}</button></div>
      <div className="formrow">
        <label className="f">Retell API key<input type="password" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="new-password" placeholder={keySet ? 'Type a new key to replace it' : 'Paste the key'} style={{ minWidth: 260 }} /></label>
      </div>
      <p className="note">{keySet ? `Key saved${(rt.data?.api_key_last4 || rt.data?.key_last4) ? ', ends in ' + (rt.data?.api_key_last4 || rt.data?.key_last4) : ''}.` : 'No key saved yet.'} The key is write-only.</p>
      {secret && (
        <div className="okbox" role="status">New secret. It is shown once, copy it now.
          <div className="mono-box" style={{ marginTop: 6, color: 'var(--ink)' }}>{secret}</div>
          <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
            <button className="btn small" onClick={() => { navigator.clipboard?.writeText(secret).then(() => toast('Copied')).catch(() => {}) }}>Copy</button>
            <button className="btn small" onClick={() => setSecret('')}>Hide</button>
          </div>
        </div>
      )}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
        <button className="btn primary" disabled={busy.has('rtsave')} onClick={() => run('rtsave', async () => { await put({ ...f, ...(key ? { retell_api_key: key } : {}) }); setKey(''); toast('Saved'); rt.reload() })}>Save Retell settings</button>
        <button className="btn" disabled={busy.has('rtsec')} onClick={() => run('rtsec', async () => {
          if (!window.confirm('Make a new secret? The old one stops working.')) return
          const r = await put({ ...f, regenerate_secret: true }); setSecret(r.secret || r.new_secret || ''); rt.reload()
        })}>Regenerate secret</button>
      </div>
    </section>
  )
}
