export type Consent = 'full' | 'transactional' | 'none' | 'unsubscribed'
export type Kind = 'inquiry' | 'patient'
export type Person = { id: number; display_name: string; initials?: string; avatar_hue?: number; kind?: Kind }
export type Row = {
  id: number; display_name: string; initials: string; avatar_hue: number; kind: Kind
  email: string | null; phone: string | null; phone_display: string | null
  email_consent: Consent; sms_consent: Consent
  owner: { id: number; display_name: string } | null
  ai_enabled: boolean; source: string; visit_count: number; last_visit_at: string | null
  pet_names: string[]; open_tasks: number; unread: number
  last_message: { channel: string; direction: string; preview: string; at: string } | null
  last_active_at: string | null; created_at: string
}
export type Pet = { id: number; contact_id?: number; name: string; species: string; breed: string | null; sex: string | null; birth_date: string | null; weight_kg: number | null; notes: string | null; status: 'active' | 'deceased' | 'moved' }
export type Ev = {
  id: number; kind: string; kind_label: string; classification: string; title: string
  starts_at: string; ends_at: string | null; status: string; source: string
  contact: { id: number; display_name: string } | null; pet: { id: number; name: string } | null; staff: { id: number; display_name: string } | null
}
export type Task = {
  id: number; kind: string; kind_label: string; classification: string; title: string; reason: string | null
  priority: 'high' | 'normal' | 'low'; status: 'open' | 'in_progress' | 'done'; due_at: string | null; raised_by: 'ai' | 'human' | 'rule'; created_at: string
  contact: { id: number; display_name: string; kind: Kind; initials: string; avatar_hue: number } | null
  assigned: { id: number; display_name: string; initials: string } | null
}
export type Link = { id: number; relationship: string; note: string | null; contact: { id: number; display_name: string; kind: Kind; initials: string; avatar_hue: number } }
export type Visit = { id: number; pet: { id: number; name: string } | null; visited_at: string; kind: string; summary: string | null; source: string }
export type Attach = { id: number; filename: string; mime: string; bytes: number; created_at?: string }
export type Detail = {
  contact: Row & { preference_notes: string | null; consent_source: string | null; consent_at: string | null }
  pets: Pet[]; links: Link[]; visits: Visit[]; upcoming_events: Ev[]; open_tasks: Task[]; attachments: Attach[]
  counts: { messages: number; notes: number; visits: number }
}
export type TItem = {
  id: number | string; type: 'message' | 'note' | 'visit' | 'task'; channel: string | null; direction: string | null
  sender: { kind: string; name: string; initials: string } | null
  subject: string | null; body: string | null; at: string; delivery_state: string | null; delivery_code?: string | null; scheduled_for: string | null
  attachments: { id: number; filename: string; mime: string; bytes: number }[]
  mentions: { id: number; display_name: string }[]
  meta?: { call?: { id: number; has_recording: boolean; has_transcript: boolean; seconds: number } }
  pending?: boolean
}
export type Staff = { id: number; display_name: string; initials: string; role: string }
export type Notif = { id: number; kind: string; text: string; link: string | null; created_at: string; read_at: string | null }

export const KIND_LABEL: Record<string, string> = { inquiry: 'Inquiry', patient: 'Patient' }
export const SOURCE_LABEL: Record<string, string> = { phone: 'Phone call', walk_in: 'Walk-in', website: 'Website', referral: 'Referral', after_hours: 'After hours', import: 'Import', other: 'Other' }
export const CONSENT_LABEL: Record<string, string> = { full: 'Full consent', transactional: 'Care messages only', none: 'Not opted in', unsubscribed: 'Unsubscribed' }
export const CONSENT_SHORT: Record<string, string> = { full: 'Full', transactional: 'Care only', none: 'None', unsubscribed: 'Unsubscribed' }
export const REL_LABEL: Record<string, string> = { friend: 'Friend', spouse: 'Spouse', partner: 'Partner', parent: 'Parent', child: 'Child', neighbour: 'Neighbour', family: 'Family', caregiver: 'Caregiver', other: 'Other' }
export const TASK_KINDS: [string, string][] = [['unknown_knowledge', 'Missing answer'], ['escalation', 'Escalation'], ['callback', 'Callback'], ['refill', 'Refill'], ['booking', 'Booking'], ['complaint', 'Complaint'], ['emergency_followup', 'Emergency follow-up'], ['results', 'Results'], ['other', 'Other']]
export const SPECIES_EMOJI: Record<string, string> = { dog: '\u{1F415}', cat: '\u{1F408}', rabbit: '\u{1F407}', bird: '\u{1F426}', reptile: '\u{1F98E}', hamster: '\u{1F439}', horse: '\u{1F434}', fish: '\u{1F41F}' }
export const emojiFor = (s: string) => SPECIES_EMOJI[(s || '').toLowerCase()] || '\u{1F43E}'
export const VISIT_LABEL: Record<string, string> = { checkup: 'Check-up', vaccine: 'Vaccine', surgery: 'Surgery', dental: 'Dental', sick: 'Sick visit', emergency: 'Emergency', other: 'Visit' }

export const EVENT_KINDS: [string, string, number][] = [
  ['checkup', 'Check-up', 170], ['vaccine', 'Vaccine', 210], ['surgery', 'Surgery', 350], ['dental', 'Dental', 280], ['consult', 'Consult', 40],
  ['inquiry_call', 'Inquiry call', 100], ['staff', 'Staff', 215], ['reminder', 'Reminder', 60], ['other', 'Other', 190],
]
export const EVENT_HUE: Record<string, number> = Object.fromEntries(EVENT_KINDS.map(([k, , h]) => [k, h]))
export const EVENT_LABEL: Record<string, string> = Object.fromEntries(EVENT_KINDS.map(([k, l]) => [k, l]))

export type KEntry = {
  id: number; category: string; category_label: string; title: string; body: string; version: number
  status: 'pending' | 'approved' | 'declined' | 'retired'; proposed_by: 'human' | 'ai' | 'import'
  source_kind: string; source_ref: string; source_quote: string; confidence: number | null
  supersedes_id: number | null; previous_body: string; conflicts: boolean
  proposer: string | null; decider: string | null; proposed_at: string; decided_at: string | null; decline_reason: string
}
export type KCounts = { pending: number; approved: number; declined: number; retired: number; conflicts: number; pending_by_category: Record<string, number>; approved_by_category: Record<string, number> }
export type FeeRow = {
  id: number; service: string; price_cents: number | null; price_dollars: number | null; price_prefix: string; unit: string; note: string
  status: 'pending' | 'approved' | 'retired'; proposed_by: string; effective_from: string | null; effective_to: string | null; decider: string | null
}

export type PCall = {
  id: number; started_at: string; direction: 'inbound' | 'outbound' | 'unknown'; handled_by: 'human' | 'ai' | 'none'; agent_label: string
  contact: { id: number; display_name: string; kind: string } | null; phone_display: string; duration_seconds: number
  result: string; result_label: string; call_type: string; short_note: string; has_transcript: boolean; has_recording: boolean; source: string; transcript?: string | null
  intent?: string | null; transcript_state?: string; summary?: string | null
}

export type Camp = {
  id: number; name: string; channel: 'sms' | 'email'; purpose: 'care' | 'promo'; status: string; status_label: string
  segment: Record<string, any>; subject: string; body: string; created_by: string | null; approved_by: string | null
  created_at: string; approved_at: string | null; ran_at: string | null
  counts: { matched?: number; sent?: number; skipped?: Record<string, number> }
  recipients?: { contact: { id: number; display_name: string }; state: string; skipped_label: string }[]
}
export type Preview = { matched: number; eligible: number; skipped: Record<string, number>; skipped_list: { reason: string; label: string; count: number }[]; samples: { contact: { id: number; display_name: string }; subject: string; text: string }[]; quiet_hours: boolean }
