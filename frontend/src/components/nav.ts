export type NavItem = { to: string; label: string; perm?: string; admin?: boolean; icon: string; href?: string }
export const NAV: { group: string; items: NavItem[] }[] = [
  { group: 'Workspace', items: [
    { to: '/home', label: 'Home', perm: 'home.view', icon: 'home' },
    { to: '/tasks', label: 'Tasks', perm: 'tasks.view', icon: 'tasks' },
    { to: '/contacts', label: 'Contacts', perm: 'contacts.view', icon: 'people' },
    { to: '/calendar', label: 'Calendar', perm: 'calendar.view', icon: 'cal' },
  ] },
  { group: 'Operations', items: [
    { to: '/knowledge', label: 'Knowledge & Fees', perm: 'knowledge.view', icon: 'sparkle' },
    { to: '/phone', label: 'Phone', perm: 'phone.view', icon: 'phone' },
    { to: '/campaigns', label: 'Campaigns', perm: 'campaigns.view', icon: 'mail' },
    { to: '/reports', label: 'Reports', perm: 'reports.view', icon: 'list' },
  ] },
  { group: 'Website', items: [
    { to: '/website/content', label: 'Website content', perm: 'website.view', icon: 'list' },
    { to: '/website/seo', label: 'SEO agent', perm: 'website.view', icon: 'sparkle' },
    // Contento runs as its own app on this server (its own logins and credits); it opens in a new tab
    { to: 'contento', href: 'https://contento.77-42-69-194.sslip.io/admin/dashboard', label: 'Content studio', perm: 'website.run', icon: 'play' },
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
export const isWorkspace = (p: string) => /^\/(home|tasks|contacts|calendar|knowledge|phone|campaigns|reports|website)(\/|$)/.test(p)
