from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.llm import make_layer2
from app.aftercare import emergency as em
from app.aftercare import engine as E
from app.aftercare import service as svc
from app.aftercare import templates as T
from app.api.admin_ai import retell_config
from app.db import get_db
from app.models import Conversation, EmergencyRouting, Event, OpenSlot
from app.retell import mapping, schema
from app.retell.verify import verify_signature
from app.security import crypto
from app.services import events as ev
from app.services import timeutil

router = APIRouter(prefix="/api/retell", tags=["retell"])

SAFE_NAME = re.compile(r"^[A-Za-z][A-Za-z' -]{0,30}$")


class Guarded:
    def __init__(self, cfg: dict[str, Any], body: bytes):
        self.cfg, self.body = cfg, body

    @property
    def dry_run(self) -> bool:
        return bool(self.cfg["dry_run"])


async def retell_guard(request: Request, db: Session = Depends(get_db)) -> Guarded:
    cfg = retell_config(db)
    if not cfg["enabled"]:
        raise HTTPException(status_code=404, detail="Not found")
    sent = request.headers.get("x-walnut-secret", "")
    ok = bool(cfg["secret_hash"]) and bool(sent) and hmac.compare_digest(
        hashlib.sha256(sent.encode()).hexdigest().encode(), cfg["secret_hash"].encode())
    if not ok:
        raise HTTPException(status_code=401, detail="Unauthorized")
    body = await request.body()
    if cfg["api_key_enc"]:
        key = crypto.decrypt(cfg["api_key_enc"])
        if not key or not verify_signature(body, key, request.headers.get("x-retell-signature", "")):
            raise HTTPException(status_code=401, detail="Unauthorized")
    return Guarded(cfg, body)


def _json(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="The request body is not valid JSON")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="The request body must be a JSON object")
    return data


def _safe_pet(name: str | None) -> str | None:
    return name if name and SAFE_NAME.match(name) else None


def _emergency_check(db: Session, text: str) -> dict[str, Any]:
    ctx = svc.build_ctx(db)
    return em.detect(text, ctx.keywords, ctx.layer2), ctx


def _shown(matched: list[str]) -> list[str]:
    return ["pattern match" if m.startswith("re:") else m for m in matched]


def _record_emergency(db: Session, g: Guarded, ctx: E.Ctx, det: dict, call_id: str, client: str, pet: str | None, text: str) -> None:
    hosp = ctx.confirmed_hospital()
    outcome = {"kind": "emergency", "pet": pet, "matched": det["matched"], "layer": det["layer"],
               "hospital_id": hosp.get("id") if hosp else None,
               "summary": f"Caller said: {text[:200]}." + (f" Pointed to {hosp['name']}." if hosp else " No confirmed emergency hospital.")}
    conv = svc.persist_outcome(db, outcome, [{"who": "client", "text": text[:500]}, {"who": "bot", "text": str(T.emergency(hosp))}],
                               channel="call", source="retell", is_test=g.dry_run, actor="Voice assistant", client_label=client,
                               origin="a phone call")
    ev.record(db, ev.ADMIN, "retell_function", "Retell function: emergency routing recorded", None, "conversation", conv.id,
              {"call_id": call_id})


def _fn_check_emergency(db: Session, g: Guarded, call: schema.FunctionCall, a: schema.CheckEmergencyArgs) -> dict:
    det, ctx = _emergency_check(db, a.text)
    say = str(T.emergency(ctx.confirmed_hospital())) if det["emergency"] else ""
    return {"emergency": det["emergency"], "matched": _shown(det["matched"]), "say": say}


def _fn_get_open_slots(db: Session, g: Guarded, call: schema.FunctionCall, a: schema.GetOpenSlotsArgs) -> dict:
    ctx = svc.build_ctx(db, use_layer2=False)
    vt = None
    if a.visit_type:
        vt = next((v for v in ctx.visit_types if v["name"].lower() == a.visit_type.lower()), None)
        if vt is None:
            raise HTTPException(status_code=400, detail="Unknown visit type")
        if not vt["bookable"] or not ctx.toggles["req"]:
            return {"slots": [], "bookable": False}
    after = None
    if a.after:
        try:
            d = datetime.fromisoformat(a.after.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="after must be an ISO date or time")
        after = d.astimezone(timezone.utc) if d.tzinfo else timeutil.at_local(d.date(), d.hour, d.minute)
    avail = [s for s in ctx.future_slots(vt["id"] if vt else None) if after is None or s["start"] >= after]
    out, seen = [], set()
    for s in avail:  # spread over days first
        if s["start"].date() not in seen:
            out.append(s)
            seen.add(s["start"].date())
        if len(out) == 3:
            break
    for s in avail:
        if len(out) == 3:
            break
        if s not in out:
            out.append(s)
    out.sort(key=lambda s: s["start"])
    return {"slots": [{"id": s["id"], "label": s["label"]} for s in out], "bookable": True}


def _fn_create_appointment_request(db: Session, g: Guarded, call: schema.FunctionCall, a: schema.CreateAppointmentArgs) -> dict:
    det, ctx = _emergency_check(db, f"{a.pet_name} {a.visit_type}")
    if det["emergency"]:
        _record_emergency(db, g, ctx, det, call.call_id, a.client_name, a.pet_name, f"{a.pet_name} {a.visit_type}")
        db.commit()
        return {"ok": True, "emergency": True, "say": str(T.emergency(ctx.confirmed_hospital()))}
    vt = next((v for v in ctx.visit_types if v["name"].lower() == a.visit_type.lower()), None)
    pet = _safe_pet(a.pet_name)
    tail = f" Phone ends {a.phone_last4}." if a.phone_last4 else ""
    msgs = [{"who": "client", "text": f"Booking request by phone for {a.pet_name} ({a.visit_type})."}]
    slot = db.get(OpenSlot, a.slot_id)
    if not ctx.toggles["req"] or vt is None or not vt["bookable"]:
        say = T.done_booking_off() if not ctx.toggles["req"] else T.done_visit_front_desk()
        outcome = {"kind": "callback", "callback_kind": "Booking", "pet": a.pet_name,
                   "summary": f"Wants to book {a.visit_type.lower()} (front desk arranges).{tail}"}
        kind_out = "callback"
    elif slot is None or slot.taken or slot.start <= ctx.now:
        raise HTTPException(status_code=400, detail="That opening is not available. Ask for the open slots again.")
    else:
        label = timeutil.slot_display(slot.start)
        say = T.retell_booking_say(label, pet)
        outcome = {"kind": "booking", "pet": a.pet_name, "slot_id": slot.id, "slot_label": label, "visit_type_id": vt["id"],
                   "summary": f"Wants {vt['name'].lower()}. Asked for {label}.{tail}"}
        kind_out = "booking"
    msgs.append({"who": "bot", "text": str(say)})
    conv = svc.persist_outcome(db, outcome, msgs, channel="call", source="retell", is_test=g.dry_run, actor="Voice assistant",
                               client_label=a.client_name, origin="a phone call")
    ev.record(db, ev.ADMIN, "retell_function", f"Retell function: {kind_out} recorded", None, "conversation", conv.id, {"call_id": call.call_id})
    db.commit()
    return {"ok": True, "say": str(say), "conversation_id": conv.id, "kind": conv.kind}


def _fn_create_callback(db: Session, g: Guarded, call: schema.FunctionCall, a: schema.CreateCallbackArgs) -> dict:
    det, ctx = _emergency_check(db, a.reason)
    if det["emergency"]:
        _record_emergency(db, g, ctx, det, call.call_id, a.client_name, a.pet_name, a.reason)
        db.commit()
        return {"ok": True, "emergency": True, "say": str(T.emergency(ctx.confirmed_hospital()))}
    say = T.done_msg() if ctx.toggles["msg"] else T.done_msg_off()
    outcome = {"kind": "callback", "callback_kind": "Message", "pet": a.pet_name, "summary": f"Left a message: {a.reason[:300]}"}
    conv = svc.persist_outcome(db, outcome, [{"who": "client", "text": a.reason[:500]}, {"who": "bot", "text": str(say)}],
                               channel="call", source="retell", is_test=g.dry_run, actor="Voice assistant",
                               client_label=a.client_name, origin="a phone call")
    ev.record(db, ev.ADMIN, "retell_function", "Retell function: callback recorded", None, "conversation", conv.id, {"call_id": call.call_id})
    db.commit()
    return {"ok": True, "say": str(say), "conversation_id": conv.id}


def _fn_get_emergency_hospital(db: Session, g: Guarded, call: schema.FunctionCall, a: schema.GetEmergencyHospitalArgs) -> dict:
    hosp = svc.build_ctx(db, use_layer2=False).confirmed_hospital()
    return {"confirmed": hosp is not None, "say": str(T.emergency(hosp))}


FUNCTIONS = {
    "check_emergency": _fn_check_emergency,
    "get_open_slots": _fn_get_open_slots,
    "create_appointment_request": _fn_create_appointment_request,
    "create_callback": _fn_create_callback,
    "get_emergency_hospital": _fn_get_emergency_hospital,
}


@router.post("/functions/{name}")
def call_function(name: str, g: Guarded = Depends(retell_guard), db: Session = Depends(get_db)):
    if name not in FUNCTIONS:
        raise HTTPException(status_code=404, detail="Not found")
    call = mapping.to_function_call(_json(g.body), name)
    try:
        args = schema.ARG_MODELS[name](**call.args)
    except ValidationError as e:
        first = e.errors()[0]
        raise HTTPException(status_code=400, detail=f"Bad arguments: {'.'.join(str(x) for x in first['loc'])} {first['msg']}")
    return mapping.to_response(FUNCTIONS[name](db, g, call, args))


@router.post("/webhook")
def webhook(g: Guarded = Depends(retell_guard), db: Session = Depends(get_db)):
    try:
        hook = schema.Webhook(**_json(g.body))
    except ValidationError:
        raise HTTPException(status_code=400, detail="Bad webhook body")
    if hook.event != "call_ended":
        return Response(status_code=204)
    call = hook.call
    if call.call_id:
        seen = db.scalar(select(Event.id).where(Event.action == "retell_call_ended", Event.entity_id == call.call_id[:40]).limit(1))
        if seen:
            return Response(status_code=204)
    msgs = mapping.parse_transcript(call.transcript)
    caller_text = " ".join(m["text"] for m in msgs if m["who"] == "client")
    keywords = svc.build_ctx(db, use_layer2=False).keywords
    det = em.detect(caller_text, keywords)
    when = timeutil.now_utc()
    if call.start_timestamp:
        try:
            when = datetime.fromtimestamp(call.start_timestamp / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            pass
    summary = (call.call_analysis.call_summary or "").strip() or "Phone call, no summary was provided."
    kind = "emergency" if det["emergency"] else "callback"
    conv = Conversation(channel="call", client_label="Phone caller", pet_label="Pet not named", kind=kind, status="new", summary=summary[:2000],
                        messages_json=json.dumps(msgs), source="retell", is_test=g.dry_run, created_at=when)
    db.add(conv)
    db.flush()
    if kind == "emergency":
        hosp = svc.build_ctx(db, use_layer2=False).confirmed_hospital()
        db.add(EmergencyRouting(conversation_id=conv.id, hospital_id=hosp.get("id") if hosp else None,
                                matched_terms=json.dumps(det["matched"]), layer=det["layer"] or "1", created_at=when))
    ev.record(db, ev.AFTER_HOURS, "phone_call_logged", f"New {'emergency routing' if kind == 'emergency' else 'phone message'} from a phone call",
              None, "conversation", conv.id, {"call_id": call.call_id[:40]})
    # the dedupe lookup above reads entity_id, so store the call id there on a second audit-only row
    ev.record(db, ev.ADMIN, "retell_call_ended", "Retell call ended", None, "retell_call", call.call_id[:40], {"conversation_id": conv.id}, publish=False)
    db.commit()
    return {"ok": True, "conversation_id": conv.id}
