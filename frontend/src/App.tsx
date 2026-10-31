import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useApp } from './ctx'
import { SignIn, ChangePassword } from './views/Auth'
import { Shell } from './components/Shell'
import { Brief } from './views/Brief'
import { Overnight } from './views/Overnight'
import { Callbacks } from './views/Callbacks'
import { Results } from './views/Results'
import { Schedule } from './views/Schedule'
import { Setup } from './views/Setup'
import { Tools } from './views/Tools'
import { Admin } from './views/Admin'
import { Home } from './views/Home'
import { Contacts } from './views/Contacts'
import { ContactPage } from './views/ContactPage'
import { Tasks } from './views/Tasks'

export function landing(role: string) {
  return role === 'owner' ? '/brief' : role === 'desk' ? '/home' : '/results'
}

function Guard({ perm, admin, children }: { perm?: string; admin?: boolean; children: JSX.Element }) {
  const { can, me } = useApp()
  if (admin && !me?.user.is_admin) return <Navigate to={landing(me!.user.role)} replace />
  if (perm && !can(perm)) return <Navigate to={landing(me!.user.role)} replace />
  return children
}

export function App() {
  const { me, ready, toastText, toastShow, toastUndo, dismissToast } = useApp()
  const loc = useLocation()
  let body
  if (!ready) body = <div className="auth"><p className="lead">Loading</p></div>
  else if (!me) {
    body = loc.pathname === '/login' ? <SignIn /> : <Navigate to="/login" replace />
  } else if (me.user.must_change_password) {
    body = loc.pathname === '/change-password' ? <ChangePassword forced /> : <Navigate to="/change-password" replace />
  } else {
    body = (
      <Routes>
        <Route path="/login" element={<Navigate to={landing(me.user.role)} replace />} />
        <Route element={<Shell />}>
          <Route path="/home" element={<Guard perm="home.view"><Home /></Guard>} />
          <Route path="/tasks" element={<Guard perm="tasks.view"><Tasks /></Guard>} />
          <Route path="/contacts" element={<Guard perm="contacts.view"><Contacts /></Guard>} />
          <Route path="/contacts/:id" element={<Guard perm="contacts.view"><ContactPage /></Guard>} />
          <Route path="/brief" element={<Guard perm="brief.view"><Brief /></Guard>} />
          <Route path="/overnight" element={<Guard perm="overnight.view"><Overnight /></Guard>} />
          <Route path="/callbacks" element={<Guard perm="callbacks.view"><Callbacks /></Guard>} />
          <Route path="/results" element={<Guard perm="results.view"><Results /></Guard>} />
          <Route path="/schedule" element={<Guard perm="schedule.view"><Schedule /></Guard>} />
          <Route path="/setup" element={<Guard perm="afterhours.view"><Setup /></Guard>} />
          <Route path="/tools" element={<Guard perm="tools.view"><Tools /></Guard>} />
          <Route path="/admin" element={<Guard admin><Admin /></Guard>} />
          <Route path="/change-password" element={<ChangePassword />} />
          <Route path="*" element={<Navigate to={landing(me.user.role)} replace />} />
        </Route>
      </Routes>
    )
  }
  return (
    <>
      {body}
      <div className={'toast' + (toastShow ? ' show' : '') + (toastUndo ? ' act' : '')} role="status" aria-live="polite">
        <span>{toastText}</span>
        {toastUndo && toastShow && <button className="toast-undo" onClick={() => { const f = toastUndo; dismissToast(); f() }}>Undo</button>}
      </div>
    </>
  )
}
