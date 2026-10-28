export type Me = {
  user: { id: number; username: string; display_name: string; title: string; initials: string; role: 'owner' | 'desk' | 'nurse'; is_admin: boolean; must_change_password: boolean }
  perms: string[]
  csrf_token: string
  settings: { clinic_name: string; banner_on: boolean }
}

let csrf = ''
export function setCsrf(t: string) { csrf = t }

type Handlers = { onToast: (m: string) => void; onAuth: () => void; onChangePw: () => void }
let handlers: Handlers = { onToast: () => {}, onAuth: () => {}, onChangePw: () => {} }
export function setHandlers(h: Handlers) { handlers = h }

export class ApiError extends Error {
  status: number
  constructor(status: number, msg: string) { super(msg); this.status = status }
}

type Opts = { method?: string; body?: unknown; form?: FormData; quiet?: boolean; raw?: boolean }

export async function api<T = any>(path: string, o: Opts = {}): Promise<T> {
  const method = o.method || (o.body !== undefined || o.form ? 'POST' : 'GET')
  const headers: Record<string, string> = {}
  let body: BodyInit | undefined
  if (o.form) body = o.form
  else if (o.body !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(o.body) }
  if (method !== 'GET') headers['X-CSRF-Token'] = csrf
  let res: Response
  try {
    res = await fetch('/api' + path, { method, headers, body, credentials: 'same-origin' })
  } catch {
    if (!o.quiet) handlers.onToast('Cannot reach the server. Try again.')
    throw new ApiError(0, 'network')
  }
  let data: any = null
  const text = await res.text()
  if (text) { try { data = JSON.parse(text) } catch { data = null } }
  if (res.ok) return data as T
  let detail = ''
  if (data && typeof data.detail === 'string') detail = data.detail
  else if (data && Array.isArray(data.detail)) detail = 'Please check what you typed.'
  if (res.status === 401 && path !== '/auth/login' && path !== '/auth/me') handlers.onAuth()
  else if (res.status === 403 && detail === 'password_change_required') handlers.onChangePw()
  else if (!o.quiet && !(res.status === 401 && (path === '/auth/login' || path === '/auth/me'))) {
    handlers.onToast(detail && detail !== 'forbidden' && detail !== 'csrf' ? detail : res.status === 403 ? 'You do not have access to that.' : 'Something went wrong. Try again.')
  }
  throw new ApiError(res.status, detail || 'error')
}
