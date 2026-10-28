import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { useApp } from '../ctx'
import { useLive } from '../live'
import { Head, Loading } from '../components/bits'

type Hosp = { id: number; name: string; place: string; phone: string; address: string; hours_claim: string; confirmed: boolean; is_placeholder: boolean }
type S = {
  toggles: { req: boolean; msg: boolean; missed: boolean }; hours_note: string
  visit_types: { id: number; name: string; minutes: number; bookable: boolean }[]
  keywords: string[]; keywords_status: string; hospitals: Hosp[]
  wording: { opening_line: string; urgent_line: string; never_line: string }
}

export function Setup() {
  const { can, toast, run, busy } = useApp()
  const s = useLive<S>('/afterhours/settings', ['settings', 'hospitals', 'visit_types'])
  const [word, setWord] = useState('')
  const edit = can('afterhours.edit')
  const d = s.data
  if (!d) return <Loading loading={s.loading} error={s.error} />
  const put = (key: string, path: string, body: unknown, msg: string, method = 'PUT') =>
    run(key, async () => { await api(path, { method, body }); if (msg) toast(msg); s.reload() })
  const tg = (k: 'req' | 'msg' | 'missed', l: string, desc: string) => (
    <div className="setrow" key={k}>
      <div><b>{l}</b><div style={{ color: 'var(--mute)', fontSize: 13 }}>{desc}</div></div>
      <button className="tog" aria-pressed={d.toggles[k]} disabled={!edit || busy.has('tg' + k)} onClick={() => put('tg' + k, '/afterhours/toggles', { [k]: !d.toggles[k] }, '')}>{d.toggles[k] ? 'On' : 'Off'}</button>
    </div>
  )
  const saveWords = (kw: string[]) => put('kw', '/afterhours/keywords', { keywords: kw, status: 'draft' }, 'Saved')
  return (
    <>
      <Head title="After-hours setup" lead="What the assistant may do when the clinic is closed, what it can book, and where it sends emergencies. Dr. Garg owns every word on this page." />
      <section className="card">
        <h2>What it may do</h2>
        <p className="sub">{d.hours_note || 'Example hours: closed after 6 pm on weekdays, and after 2 pm on Saturdays.'}</p>
        {tg('req', 'Take appointment requests', 'For future dates only. Staff confirm each one in the morning.')}
        {tg('msg', 'Take refill and message requests', 'Becomes a callback card. It never approves medicine.')}
        {tg('missed', 'Text back missed callers', 'A short text with a way to book.')}
      </section>
      <section className="card">
        <h2>Visit types it can book</h2>
        <p className="sub">Anything switched to front desk only is passed on as a callback.</p>
        <div className="tablewrap"><table style={{ minWidth: 480 }}>
          <thead><tr><th>Visit</th><th>Length</th><th>Who books it</th></tr></thead>
          <tbody>{d.visit_types.map((v) => (
            <tr key={v.id}><td>{v.name}</td><td className="mono">{v.minutes} min</td>
              <td><button className="tog" aria-pressed={v.bookable} disabled={!edit || busy.has('vt' + v.id)} onClick={() => put('vt' + v.id, `/visit-types/${v.id}`, { bookable: !v.bookable }, '')}>{v.bookable ? 'Assistant can book' : 'Front desk only'}</button></td></tr>
          ))}</tbody></table></div>
      </section>
      <section className="card">
        <h2>Emergency routing</h2>
        <p className="sub">If a client mentions any of these, the assistant stops booking and points them to an emergency hospital. This list is a placeholder. Dr. Garg writes the real one.</p>
        <div className="tagedit">
          {d.keywords.map((w) => (
            <span key={w} className="tag high">{w}{edit && <button className="x" aria-label={'Remove ' + w} disabled={busy.has('kw')} onClick={() => saveWords(d.keywords.filter((x) => x !== w))}>x</button>}</span>
          ))}
        </div>
        {edit && (
          <form className="formrow" onSubmit={(e) => { e.preventDefault(); const w = word.trim(); if (!w || d.keywords.includes(w)) return; setWord(''); saveWords([...d.keywords, w]) }}>
            <label className="f">Add a word or phrase<input value={word} maxLength={120} onChange={(e) => setWord(e.target.value)} /></label>
            <button className="btn small" type="submit" disabled={busy.has('kw') || !word.trim()}>Add</button>
          </form>
        )}
        <div className="tablewrap" style={{ marginTop: 10 }}>
          <table style={{ minWidth: 760 }}>
            <thead><tr><th>Emergency hospital</th><th>Where</th><th>Phone</th><th>Address</th><th>Hours, as the hospital states them</th><th>Status</th></tr></thead>
            <tbody>{d.hospitals.map((h) => <HospRow key={h.id} h={h} edit={edit} onDone={s.reload} />)}</tbody>
          </table>
        </div>
        {edit && <div style={{ marginTop: 10 }}><button className="btn small" disabled={busy.has('hadd')} onClick={() => put('hadd', '/hospitals', { name: 'New hospital', place: '', phone: '', address: '', hours_claim: '' }, 'Added', 'POST')}>Add a hospital</button></div>}
        <p className="note">The assistant only names hospitals marked confirmed and open at that moment. Someone at the clinic has to check the hours by phone and add a backup.</p>
      </section>
      <section className="card">
        <h2>What it says</h2>
        <div className="draft"><div className="to">Opening line</div><p style={{ margin: 0 }}>{d.wording.opening_line}</p></div>
        <div className="draft"><div className="to">When something sounds urgent</div><p style={{ margin: 0 }}>{d.wording.urgent_line}</p></div>
        <p className="note">{d.wording.never_line}</p>
      </section>
    </>
  )
}

function HospRow({ h, edit, onDone }: { h: Hosp; edit: boolean; onDone: () => void }) {
  const { toast, run, busy } = useApp()
  const [f, setF] = useState({ name: h.name, place: h.place, phone: h.phone, address: h.address, hours_claim: h.hours_claim })
  useEffect(() => { setF({ name: h.name, place: h.place, phone: h.phone, address: h.address, hours_claim: h.hours_claim }) }, [h.name, h.place, h.phone, h.address, h.hours_claim])
  const dirty = f.name !== h.name || f.place !== h.place || f.phone !== h.phone || f.address !== h.address || f.hours_claim !== h.hours_claim
  const inp = (k: keyof typeof f, label: string) => (
    <input aria-label={`${label} for ${h.name}`} value={f[k]} maxLength={200} disabled={!edit} onChange={(e) => setF({ ...f, [k]: e.target.value })} />
  )
  const key = 'h' + h.id
  return (
    <tr data-hosp={h.id}>
      <td>{inp('name', 'Name')}</td><td>{inp('place', 'Place')}</td><td>{inp('phone', 'Phone')}</td><td>{inp('address', 'Address')}</td><td>{inp('hours_claim', 'Hours')}</td>
      <td>
        {h.is_placeholder && <div className="tag" style={{ marginBottom: 4 }}>The clinic chooses one</div>}
        {edit && dirty && <button className="btn small primary" disabled={busy.has(key)} onClick={() => run(key, async () => { await api(`/hospitals/${h.id}`, { method: 'PUT', body: f }); toast('Saved'); onDone() })}>Save</button>}
        {h.confirmed ? <>
          <div><span className="tag ok">Confirmed by clinic</span></div>
          {edit && <button className="btn small" disabled={busy.has(key + 'u')} onClick={() => run(key + 'u', async () => { await api(`/hospitals/${h.id}/unconfirm`, { method: 'POST' }); toast('Marked not confirmed'); onDone() })}>Unconfirm</button>}
        </> : edit && !dirty && !h.is_placeholder && <button className="btn small" disabled={busy.has(key + 'c')} onClick={() => run(key + 'c', async () => { await api(`/hospitals/${h.id}/confirm`, { method: 'POST' }); toast('Marked confirmed'); onDone() })}>Mark confirmed</button>}
      </td>
    </tr>
  )
}
