const P: Record<string, string> = {
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zm9 16-4-4',
  bell: 'M6 9a6 6 0 1 1 12 0c0 6 2 7 2 7H4s2-1 2-7zm4 10a2 2 0 0 0 4 0',
  phone: 'M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a1 1 0 0 1-1 1A16 16 0 0 1 4 5a1 1 0 0 1 1-1z',
  mail: 'M4 6h16v12H4zM4 7l8 6 8-6',
  chat: 'M4 5h16v11H9l-5 4z',
  clip: 'M20 11l-8.5 8.5a5 5 0 0 1-7-7L13 4a3.5 3.5 0 0 1 5 5l-8.5 8.5a2 2 0 0 1-3-3L14 7',
  clock: 'M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16zm0 4v4l3 2',
  chevL: 'M15 5l-7 7 7 7',
  chevR: 'M9 5l7 7-7 7',
  chevD: 'M5 9l7 7 7-7',
  x: 'M6 6l12 12M18 6 6 18',
  plus: 'M12 5v14M5 12h14',
  check: 'M5 12l5 5 9-10',
  note: 'M6 4h9l4 4v12H6zM14 4v5h5M9 13h7M9 17h5',
  user: 'M12 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM4 20a8 8 0 0 1 16 0',
  home: 'M4 11l8-7 8 7v9h-5v-6H9v6H4z',
  tasks: 'M5 5h14v14H5zM9 12l2 2 4-4',
  people: 'M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 6.5M18 14a6.5 6.5 0 0 1 3.5 6',
  cal: 'M5 6h14v14H5zM5 10h14M9 4v4M15 4v4',
  send: 'M4 12l16-8-6 16-3-7z',
  sparkle: 'M12 4l1.8 4.7L18.5 10l-4.7 1.8L12 16.5l-1.8-4.7L5.5 10l4.7-1.3zM18 16l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z',
  download: 'M12 4v11m-4-4 4 4 4-4M5 20h14',
  columns: 'M4 5h16v14H4zM10 5v14M15 5v14',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zm10-3a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  bookmark: 'M7 4h10v16l-5-4-5 4z',
  trash: 'M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13',
  edit: 'M4 20h4L19 9l-4-4L4 16zM14 6l4 4',
  alert: 'M12 4l9 16H3zM12 10v4m0 3v.5',
  menu: 'M4 7h16M4 12h16M4 17h16',
  list: 'M8 6h12M8 12h12M8 18h12M4 6h.5M4 12h.5M4 18h.5',
  stethoscope: 'M6 4v6a4 4 0 0 0 8 0V4M10 14v2a4 4 0 0 0 8 0v-1m0 0a2 2 0 1 0 0-.01',
  undo: 'M9 7 4 12l5 5M4 12h9a6 6 0 0 1 0 8h-3',
  play: 'M8 5v14l11-7z',
  pause: 'M7 5v14M17 5v14',
}
export function Icon({ n, size = 18 }: { n: keyof typeof P | string; size?: number }) {
  return (
    <svg className="ic" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d={P[n] || ''} />
    </svg>
  )
}
