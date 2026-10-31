export type NavItem = { to: string; label: string; perm?: string; admin?: boolean; icon: string }
export const NAV: { group: string; items: NavItem[] }[] = [
  { group: 'Workspace', items: [
    { to: '/home', label: 'Home', perm: 'home.view', icon: 'home' },
    { to: '/tasks', label: 'Tasks', perm: 'tasks.view', icon: 'tasks' },
    { to: '/contacts', label: 'Contacts', perm: 'contacts.view', icon: 'people' },
  ] },
  { group: 'Clinic', items: [
    { to: '/brief', label: 'Brief', perm: 'brief.view', icon: 'sparkle' },
    { to: '/overnight', label: 'Overnight', perm: 'overnight.view', icon: 'clock' },
    { to: '/callbacks', label: 'Callbacks', perm: 'callbacks.view', icon: 'phone' },
    { to: '/results', label: 'Lab results', perm: 'results.view', icon: 'stethoscope' },
    { to: '/schedule', label: 'Schedule', perm: 'schedule.view', icon: 'cal' },
  ] },
  { group: 'Setup', items: [
    { to: '/setup', label: 'After-hours setup', perm: 'afterhours.view', icon: 'list' },
    { to: '/tools', label: 'How it fits', perm: 'tools.view', icon: 'list' },
    { to: '/admin', label: 'Admin', admin: true, icon: 'user' },
  ] },
]
export const isWorkspace = (p: string) => /^\/(home|tasks|contacts)(\/|$)/.test(p)
