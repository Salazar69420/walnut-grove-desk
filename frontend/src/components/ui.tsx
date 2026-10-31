import { ReactNode, RefObject, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Icon } from './icons'
import { clock, exact, rel, useNow } from '../lib/fmt'
import { CONSENT_LABEL, KIND_LABEL } from '../lib/types'

export function useMedia(q: string): boolean {
  const [m, setM] = useState(() => window.matchMedia(q).matches)
  useEffect(() => {
    const mq = window.matchMedia(q)
    const f = () => setM(mq.matches)
    f()
    mq.addEventListener('change', f)
    return () => mq.removeEventListener('change', f)
  }, [q])
  return m
}

export function Avatar({ name, initials, hue, size = 32 }: { name: string; initials?: string; hue?: number; size?: number }) {
  const ini = initials || name.split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase()
  return (
    <span className="av2" aria-hidden="true" style={{ ['--h' as any]: hue ?? 170, width: size, height: size, fontSize: Math.max(11, Math.round(size * 0.38)) }}>{ini}</span>
  )
}
export function KindBadge({ kind }: { kind: string }) {
  return <span className={'kb ' + kind}>{KIND_LABEL[kind] || kind}</span>
}
export function ConsentDot({ value, channel }: { value: string; channel: 'Email' | 'Text' }) {
  const text = `${channel}: ${CONSENT_LABEL[value] || value}`
  return <span className={'cdot c-' + value} role="img" aria-label={text} title={text} />
}
export function ConsentPill({ value }: { value: string }) {
  return <span className={'cpill c-' + value}><i className="cdot" aria-hidden="true" />{CONSENT_LABEL[value] || value}</span>
}
/** Relative time with the exact time on hover. */
export function Time({ iso, className }: { iso: string | null | undefined; className?: string }) {
  useNow()
  if (!iso) return null
  return <time className={className} dateTime={iso} title={exact(iso)}>{rel(iso)}</time>
}
export function Clock({ iso }: { iso: string }) {
  return <time dateTime={iso} title={exact(iso)}>{clock(iso)}</time>
}
export function Skeleton({ w, h = 14, r }: { w?: number | string; h?: number; r?: number }) {
  return <span className="sk" style={{ width: w ?? '100%', height: h, borderRadius: r }} />
}
export function SkeletonRows({ n = 6, h = 52 }: { n?: number; h?: number }) {
  return <div className="sk-rows" aria-hidden="true">{Array.from({ length: n }, (_, i) => (
    <div key={i} className="sk-row" style={{ height: h }}><span className="sk" style={{ width: 32, height: 32, borderRadius: 999 }} /><div><Skeleton w={`${50 + (i * 13) % 30}%`} /><Skeleton w={`${30 + (i * 17) % 30}%`} h={11} /></div></div>
  ))}</div>
}
export function Empty({ icon = 'people', title, text, action }: { icon?: string; title: string; text?: string; action?: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-ic"><Icon n={icon} size={26} /></div>
      <h3>{title}</h3>
      {text && <p>{text}</p>}
      {action}
    </div>
  )
}
export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} className="sw" onClick={() => onChange(!checked)}><i /></button>
  )
}

/** Close on Escape, keep Tab inside, focus the first field, give focus back on close. */
export function useDialog(open: boolean, onClose: () => void, ref: RefObject<HTMLElement>) {
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useEffect(() => {
    if (!open) return
    const prev = document.activeElement as HTMLElement | null
    const el = ref.current
    const list = () => el ? Array.from(el.querySelectorAll<HTMLElement>('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')).filter((x) => x.offsetParent !== null) : []
    const t = window.setTimeout(() => { (el?.querySelector<HTMLElement>('[data-autofocus]') || list()[0])?.focus() }, 40)
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); closeRef.current(); return }
      if (e.key === 'Tab') {
        const f = list()
        if (!f.length) return
        const first = f[0], last = f[f.length - 1]
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
      }
    }
    document.addEventListener('keydown', onKey, true)
    return () => { clearTimeout(t); document.removeEventListener('keydown', onKey, true); prev?.focus?.() }
  }, [open, ref])
}

export function Sheet({ open, onClose, title, children, footer }: { open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  const id = useId()
  useDialog(open, onClose, ref)
  if (!open) return null
  return createPortal(
    <div className="scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="sheet" role="dialog" aria-modal="true" aria-labelledby={id} ref={ref}>
        <header className="sheet-h"><h2 id={id}>{title}</h2><button className="ibtn" onClick={onClose} aria-label="Close"><Icon n="x" /></button></header>
        <div className="sheet-b">{children}</div>
        {footer && <footer className="sheet-f">{footer}</footer>}
      </div>
    </div>, document.body)
}
export function Modal({ open, onClose, title, children, footer }: { open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  const id = useId()
  useDialog(open, onClose, ref)
  if (!open) return null
  return createPortal(
    <div className="scrim center" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby={id} ref={ref}>
        <h2 id={id}>{title}</h2>
        <div className="modal-b">{children}</div>
        {footer && <footer className="modal-f">{footer}</footer>}
      </div>
    </div>, document.body)
}

/** A small popover anchored to its button. Closes on outside click and Escape. */
export function Pop({ button, children, align = 'left', className, onOpen }: { onOpen?: () => void; button: (o: { open: boolean; toggle: () => void }) => ReactNode; children: (close: () => void) => ReactNode; align?: 'left' | 'right'; className?: string }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const onOpenRef = useRef(onOpen)
  onOpenRef.current = onOpen
  useEffect(() => { if (open) onOpenRef.current?.() }, [open])
  useEffect(() => {
    if (!open) return
    const down = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); ref.current?.querySelector<HTMLElement>('button')?.focus() } }
    document.addEventListener('mousedown', down)
    document.addEventListener('keydown', key)
    return () => { document.removeEventListener('mousedown', down); document.removeEventListener('keydown', key) }
  }, [open])
  return (
    <div className={'popwrap ' + (className || '')} ref={ref}>
      {button({ open, toggle: () => setOpen((o) => !o) })}
      {open && <div className={'pop ' + align}>{children(() => setOpen(false))}</div>}
    </div>
  )
}

export function Segmented<T extends string>({ value, onChange, options, label }: { value: T; onChange: (v: T) => void; options: [T, string][]; label: string }) {
  return (
    <div className="seg2" role="group" aria-label={label}>
      {options.map(([k, l]) => <button key={k} type="button" aria-pressed={value === k} onClick={() => onChange(k)}>{l}</button>)}
    </div>
  )
}
