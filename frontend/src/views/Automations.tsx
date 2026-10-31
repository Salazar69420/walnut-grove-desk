import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import { useApp } from '../ctx'
import { Empty, Modal, SkeletonRows, Switch } from '../components/ui'

type Trigger = { days?: string[]; time?: string; event?: string }
type Cfg = { report?: string; channel?: string; to?: string; text?: string; title?: string; priority?: string; assign_to?: string }
type Auto = { id: number; name: string; enabled: boolean; trigger_type: 'schedule' | 'event'; trigger: Trigger; action: string; config: Cfg; template: string; last_run_at: string | null; last_ok: boolean | null; last_result: string; run_count: number }
type Tpl = { key: string; name: string; why: string; trigger_type: 'schedule' | 'event'; trigger: Trigger; action: string; config: Cfg }
type View = {
  items: Auto[]; templates: Tpl[]; events: Record<string, string>; reports: Record<string, string>; days: string[]; staff: { id: number; name: string }[]
  text_numbers: string[]; texting_mode: string; email: { ready: boolean; host: string; port: number; username: string; from_addr: string; security: string; has_password: boolean }; can_edit: boolean
}
type Draft = { id?: number; name: string; trigger_type: 'schedule' | 'event'; trigger: Trigger; action: string; config: Cfg; template: string; enabled: boolean }

const ACTIONS: [string, string][] = [['report', 'Send a report'], ['chat', 'Post a message in Chats'], ['task', 'Create a task'], ['text_client', 'Text the client'], ['seo_audit', 'Check the website (SEO audit)']]
const DAY_LABEL: Record<string, string> = { mon: 'Mon', tue: 'Tue', wed: 'Wed', thu: 'Thu', fri: 'Fri', sat: 'Sat', sun: 'Sun' }
const err = (x: unknown, d: string) => (x instanceof ApiError && x.message && x.message !== 'error' ? x.message : d)
const hm = (t?: string) => { if (!t) return ''; const [h, m] = t.split(':').map(Number); return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h < 12 ? 'am' : 'pm'}` }
const phone = (n: string) => n.replace(/^\+1(\d{3})(\d{3})(\d{4})$/, '($1) $2-$3')

function sentence(a: { trigger_type: string; trigger: Trigger; action: string; config: Cfg }, v: View): string {
  const days = a.trigger.days || []
  const when = a.trigger_type === 'schedule'
    ? `${days.length === 7 ? 'Every day' : days.join(',') === 'mon,tue,wed,thu,fri' ? 'Weekdays' : days.join(',') === 'mon,tue,wed,thu,fri,sat' ? 'Monday to Saturday' : days.map((d) => DAY_LABEL[d]).join(', ')} at ${hm(a.trigger.time)}`
    : `When ${(v.events[a.trigger.event || ''] || 'something happens').replace(/^A /, 'a ').replace(/^An /, 'an ')}`
  const who = (to?: string) => !to || to === 'everyone' ? 'Everyone in Chats' : to === 'owner' ? 'the owner in Chats' : v.staff.find((s) => String(s.id) === String(to))?.name || to
  const c = a.config
  const what = a.action === 'report' ? `send the ${(v.reports[c.report || ''] || 'report').split(':')[0].toLowerCase()} ${c.channel === 'sms' ? `by text to ${phone(c.to || '')}` : c.channel === 'email' ? `by email to ${c.to}` : `to ${who(c.to)}`}`
    : a.action === 'chat' ? `post a message to ${who(c.to)}` : a.action === 'task' ? `create a task "${c.title}"` : a.action === 'text_client' ? 'text the client' : 'check the website'
  return `${when}, ${what}.`
}

/** Automations: simple "at this time / when this happens, do that" rules anyone on the team can read, and owners can set up. */
export function Automations() {
  const { toast } = useApp()
  const [v, setV] = useState<View | null>(null)
  const [edit, setEdit] = useState<Draft | null>(null)
  const [runs, setRuns] = useState<{ id: number; items: { at: string; ok: boolean; detail: string }[] } | null>(null)
  const [preview, setPreview] = useState<{ title: string; text: string } | null>(null)
  const load = () => api<View>('/automations', { quiet: true }).then(setV).catch(() => {})
  useEffect(() => { load() }, [])
  if (!v) return <div className="php"><SkeletonRows n={6} /></div>

  async function save(d: Draft) {
    const body = { name: d.name, trigger_type: d.trigger_type, trigger: d.trigger, action: d.action, config: d.config, template: d.template, enabled: d.enabled }
    try { await api(d.id ? `/automations/${d.id}` : '/automations', { method: d.id ? 'PUT' : 'POST', body, quiet: true }); toast(d.id ? 'Saved' : 'Automation is on'); setEdit(null); load() }
    catch (x) { toast(err(x, 'Not saved.')) }
  }
  async function toggle(a: Auto, on: boolean) { await save({ ...a, enabled: on }) }
  async function runNow(a: Auto) {
    try { const r = await api<{ ok: boolean; result: string }>(`/automations/${a.id}/run`, { method: 'POST', body: {}, quiet: true }); toast(r.ok ? `Done: ${r.result}` : r.result); load() }
    catch (x) { toast(err(x, 'Could not run it.')) }
  }
  async function remove(a: Auto) {
    if (!window.confirm(`Delete "${a.name}"?`)) return
    try { await api(`/automations/${a.id}`, { method: 'DELETE', quiet: true }); load() } catch (x) { toast(err(x, 'Not deleted.')) }
  }
  const fromTpl = (t: Tpl) => setEdit({ name: t.name, trigger_type: t.trigger_type, trigger: { ...t.trigger }, action: t.action, template: t.key, enabled: true,
    config: { ...t.config, to: t.config.channel === 'sms' && !t.config.to && t.key !== 'owner_brief_sms' ? v.text_numbers[0] || '' : t.config.to } })
  const used = new Set(v.items.map((a) => a.template))

  return (
    <div className="php web auto">
      <div className="hd2">
        <div><h1>Automations</h1><p className="lead">Let the app do the small daily jobs: at a set time, or when something happens, it sends a report, posts in Chats, raises a task or texts a client.</p></div>
        {v.can_edit && <button className="btn primary" onClick={() => setEdit({ name: '', trigger_type: 'schedule', trigger: { days: v.days.slice(0, 5), time: '08:00' }, action: 'report', config: { report: 'morning', channel: 'chat', to: 'everyone' }, template: '', enabled: true })}>New automation</button>}
      </div>

      <section className="card">
        <h3 className="pad" style={{ marginBottom: 0 }}>Your automations</h3>
        {v.items.length === 0 ? <p className="pad mute">None yet. Start from an idea below; it takes one click.</p> : v.items.map((a) => (
          <div key={a.id} className="auto-row">
            {v.can_edit ? <Switch checked={a.enabled} onChange={(on) => toggle(a, on)} label={`${a.name} on`} /> : <span className={'tag ' + (a.enabled ? 'ok' : '')}>{a.enabled ? 'On' : 'Off'}</span>}
            <div className="auto-m"><b>{a.name}</b><span>{sentence(a, v)}</span>
              <small className={a.last_ok === false ? 'bad' : 'mute'}>{a.last_run_at ? `Last ran ${new Date(a.last_run_at).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}: ${a.last_result}` : 'Has not run yet.'}</small></div>
            <div className="rowbtn">
              {v.can_edit && !(a.trigger_type === 'event' && (a.action === 'task' || a.action === 'text_client')) && <button className="btn small" onClick={() => runNow(a)}>Run now</button>}
              <button className="btn small ghost" onClick={() => api<{ items: { at: string; ok: boolean; detail: string }[] }>(`/automations/${a.id}/runs`, { quiet: true }).then((r) => setRuns({ id: a.id, items: r.items }))}>History</button>
              {v.can_edit && <button className="btn small ghost" onClick={() => setEdit({ ...a })}>Edit</button>}
              {v.can_edit && <button className="btn small ghost" onClick={() => remove(a)}>Delete</button>}
            </div>
          </div>
        ))}
      </section>

      <section className="card pad">
        <h3 style={{ marginTop: 0 }}>Ideas to start with</h3>
        <div className="auto-ideas">
          {v.templates.map((t) => (
            <div key={t.key} className={'auto-idea' + (used.has(t.key) ? ' used' : '')}>
              <b>{t.name}</b><p>{t.why}</p><small className="mute">{sentence(t, v)}</small>
              {v.can_edit && <button className="btn small" onClick={() => fromTpl(t)}>{used.has(t.key) ? 'Add another' : 'Use this'}</button>}
            </div>
          ))}
        </div>
      </section>

      {v.can_edit && <EmailSetup v={v} onSaved={load} />}

      {edit && <Editor v={v} d={edit} onClose={() => setEdit(null)} onSave={save} onPreview={(r) => api<{ title: string; text: string }>(`/automations/preview/${r}`, { quiet: true }).then(setPreview).catch(() => {})} />}
      <Modal open={!!preview} onClose={() => setPreview(null)} title={preview?.title || ''} footer={<button className="btn" onClick={() => setPreview(null)}>Close</button>}><pre className="web-plan">{preview?.text}</pre></Modal>
      <Modal open={!!runs} onClose={() => setRuns(null)} title="History" footer={<button className="btn" onClick={() => setRuns(null)}>Close</button>}>
        {runs && (runs.items.length ? <ul className="auto-runs">{runs.items.map((r, i) => <li key={i} className={r.ok ? '' : 'bad'}>{new Date(r.at).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}: {r.detail}</li>)}</ul> : <p className="mute">It has not run yet.</p>)}
      </Modal>
    </div>
  )
}

function Editor({ v, d: d0, onClose, onSave, onPreview }: { v: View; d: Draft; onClose: () => void; onSave: (d: Draft) => void; onPreview: (r: string) => void }) {
  const [d, setD] = useState<Draft>(d0)
  const c = d.config
  const setC = (p: Partial<Cfg>) => setD({ ...d, config: { ...c, ...p } })
  const recipients = <>
    <option value="everyone">Everyone (team chat)</option><option value="owner">The owner (Dr. Garg)</option>
    {v.staff.map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
  </>
  return (
    <Modal open onClose={onClose} title={d.id ? 'Edit automation' : 'New automation'} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" onClick={() => onSave(d)}>{d.id ? 'Save' : 'Turn it on'}</button></>}>
      <div className="bk auto-ed">
        <label className="fld"><span>Name</span><input value={d.name} maxLength={120} onChange={(e) => setD({ ...d, name: e.target.value })} placeholder="For example: Morning report by text" /></label>
        <div className="fld"><span>When</span>
          <div className="seg"><button className={d.trigger_type === 'schedule' ? 'on' : ''} onClick={() => setD({ ...d, trigger_type: 'schedule', trigger: { days: d.trigger.days || v.days.slice(0, 5), time: d.trigger.time || '08:00' } })}>At a set time</button>
            <button className={d.trigger_type === 'event' ? 'on' : ''} onClick={() => setD({ ...d, trigger_type: 'event', trigger: { event: d.trigger.event || 'missed_call' } })}>When something happens</button></div>
        </div>
        {d.trigger_type === 'schedule' ? (
          <div className="bk-row">
            <div className="auto-days" role="group" aria-label="Days">{v.days.map((day) => {
              const on = (d.trigger.days || []).includes(day)
              return <button key={day} type="button" aria-pressed={on} className={'bk-day' + (on ? ' on' : '')} onClick={() => setD({ ...d, trigger: { ...d.trigger, days: on ? (d.trigger.days || []).filter((x) => x !== day) : [...(d.trigger.days || []), day] } })}><b>{DAY_LABEL[day]}</b></button>
            })}</div>
            <label className="fld" style={{ maxWidth: 140 }}><span>Time</span><input type="time" value={d.trigger.time || '08:00'} onChange={(e) => setD({ ...d, trigger: { ...d.trigger, time: e.target.value } })} /></label>
          </div>
        ) : (
          <label className="fld"><span>What starts it</span><select value={d.trigger.event} onChange={(e) => setD({ ...d, trigger: { event: e.target.value } })}>{Object.entries(v.events).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        )}
        <label className="fld"><span>Do this</span><select value={d.action} onChange={(e) => setD({ ...d, action: e.target.value, config: e.target.value === 'report' ? { report: 'morning', channel: 'chat', to: 'everyone' } : e.target.value === 'chat' ? { to: 'everyone', text: '{details}' } : e.target.value === 'task' ? { title: 'Follow up with {name}', priority: 'normal' } : e.target.value === 'text_client' ? { text: 'Hi {first_name}, ' } : {} })}>
          {ACTIONS.filter(([k]) => d.trigger_type === 'event' || k !== 'text_client').map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        {d.action === 'report' && <>
          <label className="fld"><span>Report</span><select value={c.report} onChange={(e) => setC({ report: e.target.value })}>{Object.entries(v.reports).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
          <button type="button" className="linkbtn" onClick={() => onPreview(c.report || 'morning')}>See what it looks like today</button>
          <div className="bk-row">
            <label className="fld"><span>Send by</span><select value={c.channel} onChange={(e) => setC({ channel: e.target.value, to: e.target.value === 'sms' ? v.text_numbers[0] || '' : e.target.value === 'chat' ? 'everyone' : '' })}>
              <option value="chat">Team Chats</option><option value="sms">Text message</option><option value="email">Email{v.email.ready ? '' : ' (set up below first)'}</option></select></label>
            <label className="fld"><span>To</span>
              {c.channel === 'chat' ? <select value={c.to} onChange={(e) => setC({ to: e.target.value })}>{recipients}</select>
                : c.channel === 'sms' ? <><input type="tel" list="auto-nums" value={c.to || ''} onChange={(e) => setC({ to: e.target.value })} placeholder="Phone number, for example 604 555 0123" /><datalist id="auto-nums">{v.text_numbers.map((n) => <option key={n} value={n}>{phone(n)}</option>)}</datalist></>
                  : <input type="email" value={c.to || ''} onChange={(e) => setC({ to: e.target.value })} placeholder="name@example.com" />}</label>
          </div>
          {c.channel === 'sms' && v.texting_mode !== 'live' && <p className="mute small">Until texting is live, reports can only be texted to numbers on the texting test list (Admin, Texting){c.to && !v.text_numbers.includes(c.to.replace(/\D/g, '').replace(/^(\d{10})$/, '+1$1').replace(/^1(\d{10})$/, '+1$1')) ? '. This number is not on it yet, so add it there first.' : '.'}</p>}
        </>}
        {d.action === 'chat' && <>
          <label className="fld"><span>To</span><select value={c.to} onChange={(e) => setC({ to: e.target.value })}>{recipients}</select></label>
          <label className="fld"><span>Message ({'{details}'} is filled in with what happened)</span><textarea rows={3} value={c.text || ''} onChange={(e) => setC({ text: e.target.value })} /></label>
        </>}
        {d.action === 'task' && <div className="bk-row">
          <label className="fld"><span>Task title ({'{name}'} is the client)</span><input value={c.title || ''} onChange={(e) => setC({ title: e.target.value })} /></label>
          <label className="fld"><span>For</span><select value={c.assign_to || ''} onChange={(e) => setC({ assign_to: e.target.value })}><option value="">Nobody yet</option>{v.staff.map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}</select></label>
          <label className="fld" style={{ maxWidth: 130 }}><span>Priority</span><select value={c.priority || 'normal'} onChange={(e) => setC({ priority: e.target.value })}><option value="high">High</option><option value="normal">Normal</option><option value="low">Low</option></select></label>
        </div>}
        {d.action === 'text_client' && <>
          <label className="fld"><span>Text ({'{first_name}'} is filled in)</span><textarea rows={3} maxLength={600} value={c.text || ''} onChange={(e) => setC({ text: e.target.value })} /></label>
          <p className="mute small">Only clients who opted in to texts get it. Until texting is live, only the test numbers do.</p>
        </>}
        {d.action === 'seo_audit' && <p className="mute small">Runs the website check; the results show in SEO agent.</p>}
      </div>
    </Modal>
  )
}

function EmailSetup({ v, onSaved }: { v: View; onSaved: () => void }) {
  const { toast } = useApp()
  const [open, setOpen] = useState(false)
  const [f, setF] = useState({ host: v.email.host, port: v.email.port || 587, username: v.email.username, password: '', from_addr: v.email.from_addr, security: v.email.security || 'starttls' })
  const [to, setTo] = useState('')
  async function save() {
    try { await api('/automations/email/settings', { method: 'PUT', quiet: true, body: { ...f, port: Number(f.port), password: f.password || undefined } }); toast('Email settings saved'); onSaved() }
    catch (x) { toast(err(x, 'Not saved.')) }
  }
  async function test() {
    try { const r = await api<{ result: string }>('/automations/email/test', { body: { to }, quiet: true }); toast(`Test sent: ${r.result}`) } catch (x) { toast(err(x, 'The test did not go out.')) }
  }
  return (
    <section className="card pad">
      <div className="rowbtn" style={{ justifyContent: 'space-between' }}><h3 style={{ margin: 0 }}>Email for reports</h3><span className={'tag ' + (v.email.ready ? 'ok' : '')}>{v.email.ready ? `Set up (${v.email.from_addr})` : 'Not set up'}</span></div>
      {!open ? <button className="linkbtn" onClick={() => setOpen(true)}>{v.email.ready ? 'Change the email settings' : 'Set up email (the clinic mail account, SMTP)'}</button> : (
        <div className="bk">
          <p className="mute small">Use the clinic's own mail account (for Google Workspace: smtp.gmail.com, port 587, and an app password). The password is stored encrypted.</p>
          <div className="bk-row"><label className="fld"><span>Server</span><input value={f.host} onChange={(e) => setF({ ...f, host: e.target.value })} placeholder="smtp.gmail.com" /></label>
            <label className="fld" style={{ maxWidth: 110 }}><span>Port</span><input type="number" value={f.port} onChange={(e) => setF({ ...f, port: Number(e.target.value) })} /></label>
            <label className="fld" style={{ maxWidth: 150 }}><span>Security</span><select value={f.security} onChange={(e) => setF({ ...f, security: e.target.value })}><option value="starttls">STARTTLS (587)</option><option value="ssl">SSL (465)</option></select></label></div>
          <div className="bk-row"><label className="fld"><span>Username</span><input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} autoComplete="off" /></label>
            <label className="fld"><span>Password{v.email.has_password ? ' (leave empty to keep)' : ''}</span><input type="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} autoComplete="new-password" /></label></div>
          <label className="fld"><span>Send from</span><input type="email" value={f.from_addr} onChange={(e) => setF({ ...f, from_addr: e.target.value })} placeholder="reception@walnutgroveanimalclinic.com" /></label>
          <div className="rowbtn"><button className="btn primary" onClick={save}>Save</button><input type="email" value={to} onChange={(e) => setTo(e.target.value)} placeholder="Send a test to..." /><button className="btn" disabled={!to.includes('@')} onClick={test}>Send test</button></div>
        </div>
      )}
    </section>
  )
}
