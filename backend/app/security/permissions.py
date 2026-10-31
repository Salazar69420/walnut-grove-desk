"""Single source of truth for who may do what (docs/CONTRACT.md)."""
from __future__ import annotations

PERMS: dict[str, set[str]] = {
    "overnight.view": {"desk", "nurse", "owner"},
    "overnight.manage": {"desk", "owner"},  # confirm, entered, to-callback, ack
    "overnight.followup": {"desk", "nurse", "owner"},  # mark emergency followed up
    "callbacks.view": {"desk", "nurse", "owner"},
    "callbacks.work": {"desk", "nurse", "owner"},  # claim, done
    "drafts.view": {"desk", "owner"},
    "drafts.approve": {"desk", "owner"},  # edit, approve
    "results.view": {"desk", "nurse", "owner"},
    "results.advance": {"nurse", "owner"},
    "schedule.view": {"desk", "nurse", "owner"},
    "brief.view": {"owner"},
    "suggestions.decide": {"owner"},
    "afterhours.view": {"owner"},
    "afterhours.edit": {"owner"},
    "sim.use": {"desk", "nurse", "owner"},
    "tools.view": {"desk", "nurse", "owner"},
    "assistant.use": {"desk", "nurse", "owner"},
    "export.use": {"desk", "nurse", "owner"},  # each export also checks the list's view perm
    # Clinic CRM, phases 1 and 2 (docs/CONTRACT_CRM_P1.md)
    "contacts.view": {"desk", "nurse", "owner"},
    "contacts.edit": {"desk", "nurse", "owner"},
    "contacts.export": {"desk", "owner"},
    "messages.view": {"desk", "nurse", "owner"},
    "messages.send": {"desk", "nurse", "owner"},
    "notes.write": {"desk", "nurse", "owner"},
    "tasks.view": {"desk", "nurse", "owner"},
    "tasks.work": {"desk", "nurse", "owner"},
    "calendar.view": {"desk", "nurse", "owner"},
    "calendar.edit": {"desk", "owner"},
    "home.view": {"desk", "nurse", "owner"},
    # Knowledge and fees, phone, campaigns, reports (docs/PLAN_CLINIC_CRM.md phases 4, 5, 7, 8)
    "knowledge.view": {"desk", "nurse", "owner"},
    "knowledge.suggest": {"desk", "nurse", "owner"},  # add or edit a pending entry
    "knowledge.decide": {"owner"},  # approve, decline, retire, and approve fees
    "phone.view": {"desk", "nurse", "owner"},
    "phone.export": {"desk", "owner"},
    "phone.transcript": {"owner"},  # every open is written to the access log
    "phone.listen": {"owner"},  # play a call recording; every play is written to the access log
    "campaigns.view": {"desk", "owner"},
    "campaigns.edit": {"desk", "owner"},  # drafts and previews
    "campaigns.approve": {"owner"},  # approve and run (dry run)
    "reports.view": {"desk", "owner"},
}
ADMIN_PERMS: set[str] = {"admin.users", "admin.audit", "admin.settings", "admin.import", "admin.reset_demo", "admin.telus", "admin.contacts_erase", "admin.access_log"}  # require is_admin

ROLES = ("owner", "desk", "nurse")


def has_perm(user, perm: str) -> bool:
    if perm in ADMIN_PERMS:
        return bool(user.is_admin)
    allowed = PERMS.get(perm)
    if allowed is None:
        raise KeyError(f"unknown permission {perm!r}")
    return user.role in allowed


def perms_for(user) -> list[str]:
    out = [p for p, roles in PERMS.items() if user.role in roles]
    if user.is_admin:
        out.extend(ADMIN_PERMS)
    return sorted(out)
