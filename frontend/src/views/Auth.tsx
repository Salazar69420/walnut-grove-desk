import { FormEvent, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, Me } from '../api/client'
import { useApp } from '../ctx'
import { landing } from '../App'

export function SignIn() {
  const { setMe, run, busy } = useApp()
  const nav = useNavigate()
  const [u, setU] = useState('')
  const [p, setP] = useState('')
  const [err, setErr] = useState('')
  async function submit(e: FormEvent) {
    e.preventDefault()
    setErr('')
    await run('login', async () => {
      try {
        const m = await api<Me>('/auth/login', { body: { username: u.trim(), password: p }, quiet: true })
        setMe(m)
        nav(m.user.must_change_password ? '/change-password' : landing(m.user.role), { replace: true })
      } catch (x: any) {
        setErr(x.status === 423 ? 'Too many tries. Wait 15 minutes.' : x.status === 429 ? 'Too many tries. Wait a few minutes.' : x.status === 401 ? 'Wrong username or password' : x.message && x.message !== 'error' ? x.message : 'Something went wrong. Try again.')
      }
    })
  }
  return (
    <main className="auth">
      <form className="card" onSubmit={submit}>
        <div className="brand"><div className="logo">W</div>Walnut Grove Desk</div>
        <h1>Sign in</h1>
        <label className="f">Username<input value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" autoCapitalize="none" required /></label>
        <label className="f">Password<input type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" required /></label>
        {err && <div className="err" role="alert">{err}</div>}
        <button className="btn primary" type="submit" disabled={busy.has('login')}>Sign in</button>
      </form>
    </main>
  )
}

export function ChangePassword({ forced }: { forced?: boolean }) {
  const { setMe, run, busy, toast, me } = useApp()
  const nav = useNavigate()
  const [cur, setCur] = useState('')
  const [n1, setN1] = useState('')
  const [n2, setN2] = useState('')
  const [err, setErr] = useState('')
  async function submit(e: FormEvent) {
    e.preventDefault()
    setErr('')
    if (n1 !== n2) { setErr('The two new passwords are not the same.'); return }
    await run('pw', async () => {
      try {
        const m = await api<Me>('/auth/change-password', { body: { current_password: cur, new_password: n1 }, quiet: true })
        setMe(m)
        toast('Password changed')
        nav(landing(m.user.role), { replace: true })
      } catch (x: any) { setErr(x.message && x.message !== 'error' ? x.message : 'Something went wrong. Try again.') }
    })
  }
  const form = (
    <form className="card" onSubmit={submit}>
      {forced && <div className="brand"><div className="logo">W</div>Walnut Grove Desk</div>}
      <h1>Choose a new password</h1>
      {forced && <p className="lead">This is your first sign in. Please set a password of your own before you start.</p>}
      <ul className="note" style={{ margin: 0, paddingLeft: 18 }}>
        <li>At least 12 characters</li>
        <li>Not your username</li>
        <li>Not a common password</li>
        <li>Not the same as your current one</li>
      </ul>
      <label className="f">Current password<input type="password" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" required /></label>
      <label className="f">New password<input type="password" value={n1} onChange={(e) => setN1(e.target.value)} autoComplete="new-password" required minLength={12} /></label>
      <label className="f">New password again<input type="password" value={n2} onChange={(e) => setN2(e.target.value)} autoComplete="new-password" required minLength={12} /></label>
      {err && <div className="err" role="alert">{err}</div>}
      <button className="btn primary" type="submit" disabled={busy.has('pw')}>Save new password</button>
    </form>
  )
  if (forced) return <main className="auth">{form}</main>
  return <>{me && form}</>
}
