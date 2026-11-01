"""Creative studio: images and short vertical videos for the clinic's own marketing, the way a good agency would make them.

The idea comes from Contento (an AI video tool for small food businesses): the value is not the video model, any tool can call
one. It is the director in front of it. A busy vet is not a marketer, will not write a good prompt, and should never be asked a
camera or "aesthetic" question. So the app:
1. offers a short list of formats that work for a clinic, each with its own fixed creative doctrine (hook, pacing, camera);
2. runs a short interview: one plain multiple-choice question at a time, only about things the clinic actually knows, pushing
   a vague answer ("people love it") to a real specific ("we did 40 dentals last month"); it already knows the approved
   clinic knowledge, fees and hours, so it never asks for those;
3. writes, in one pass, a short plan (hook, arc, the one real fact used) and then the final prompt, a social caption and a short
   campaign text; the owner reads and edits all of it before anything is spent;
4. generates through WaveSpeed (gpt-image-2 for stills, Seedance 2.5 for 9:16 video), stores the result on this server and gives
   it a share link, so a text campaign can carry it and an email can show it.
Clinic rules on top: no medical claims or guarantees, no prices that are not approved fees, no client or pet names unless the
clinic typed them, nothing graphic. The model is the assistant's OpenRouter model; nothing is held in a database transaction
while a model or the video service works.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Creative
from app.security import crypto
from app.services import settings_store, timeutil

log = logging.getLogger("walnut.creatives")
KEY = "creative_studio"
WAVESPEED = "https://api.wavespeed.ai/api/v3"
TRANSPORT = None  # tests put a fake WaveSpeed here
MAX_QUESTIONS = 6
MAX_FILE = 150 * 1024 * 1024
DEFAULTS = {"wavespeed_key_enc": "", "wavespeed_last4": None, "monthly_budget_usd": 50.0,
            "image_model": "openai/gpt-image-2/text-to-image", "image_edit_model": "openai/gpt-image-2/edit",
            "video_model": "bytedance/seedance-2.5/text-to-video", "video_image_model": "bytedance/seedance-2.5/image-to-video"}

# ---------------------------------------------------------------- formats (each with its own doctrine, decided once, never asked)
IMAGE_FORMATS: dict[str, dict[str, str]] = {
    "service_spotlight": {"label": "Service spotlight", "blurb": "One service, shown so a pet owner instantly gets what it is and why it matters.",
                          "doctrine": "A bright, warm, professional photo-style image focused on ONE service in action or its result: a calm, healthy-looking pet with gentle, caring hands, clean modern clinic surroundings. Short headline text naming the service and the clinic name. Friendly and trustworthy, never clinical or frightening."},
    "seasonal_reminder": {"label": "Seasonal reminder", "blurb": "Vaccines, ticks, dental month, holiday hazards: the right reminder at the right time.",
                          "doctrine": "A seasonal scene that makes the timing obvious at a glance (spring grass, summer trail, winter, holiday table) with a happy pet in it, plus a short, clear reminder headline and the clinic name. Inviting, never fear-based."},
    "tip_card": {"label": "Pet care tip", "blurb": "One genuinely useful tip a pet owner would save or share.",
                 "doctrine": "A clean, shareable tip card: one short headline (the tip itself, under 8 words), a simple supporting visual of a pet, clinic name small at the bottom. Generous whitespace, high contrast, legible on a phone."},
    "offer_news": {"label": "Offer or clinic news", "blurb": "New service, extended hours, a real offer, holiday hours, a new vet joining.",
                   "doctrine": "An announcement graphic: the news in one bold, short line, one supporting visual (the clinic, a pet, a friendly team), and the clinic name. Clear hierarchy: headline first, details second."},
    "pet_of_month": {"label": "Pet of the month", "blurb": "Celebrate a patient (with the owner's OK): the most shared kind of clinic post.",
                     "doctrine": "A joyful portrait-style image of the featured pet (match the reference photo exactly if one is given), with a short celebratory title and the pet's first name only, clinic name small. Warm, fun, personal."},
}
VIDEO_FORMATS: dict[str, dict[str, str]] = {
    "clinic_walkthrough": {"label": "Clinic walkthrough", "blurb": "Show a nervous pet owner what it actually looks like inside: calm, clean, welcoming.",
                           "doctrine": "Smooth handheld walkthrough, gimbal-steady, eye level. Opens mid-motion on the most welcoming sight (a happy dog greeted at the front desk), then moves through the space room by room, each beat one clear area and what happens there. Bright, natural light. Ends back at the front door or a happy pet leaving, so it loops."},
    "tip_15s": {"label": "15-second tip", "blurb": "One useful tip, shown not told: the kind people save.",
                "doctrine": "Opens on the problem already happening (a dog scratching, a cat ignoring food) in the first second, then the simple fix shown hands-on in 2-3 clear beats, ending on the happy result. Close, well-lit, calm pacing, one idea only."},
    "myth_busted": {"label": "Myth, busted", "blurb": "Correct a common wrong belief: the correction is the hook.",
                    "doctrine": "First beat states the myth visually (a pet owner doing the common wrong thing), a beat of gentle surprise, then the right way shown clearly, ending on the happy pet. Friendly, never shaming; no medical procedures shown."},
    "behind_scenes": {"label": "Behind the scenes", "blurb": "A real moment of the team caring for pets: trust, not a sale.",
                      "doctrine": "Documentary feel, warm and candid: the team's small caring moments (a treat, a cuddle in recovery, a careful check-up) in quick, varied beats, never the same angle twice. Real, not staged-looking; no needles, blood or surgery shown."},
    "happy_visit": {"label": "Happy visit story", "blurb": "A pet's visit from nervous arrival to a happy wag out the door.",
                    "doctrine": "A small arc: a slightly nervous pet arriving, a gentle welcome, a calm moment with the vet (stethoscope, a treat), and a joyful exit. Escalates in warmth; ends on the happiest frame, framed to loop back to the door."},
    "cinematic_ad": {"label": "Cinematic service ad", "blurb": "A polished, professional commercial for one service.",
                     "doctrine": "Polished commercial look: composed shots, soft studio-like light, slow purposeful camera moves (slow push-in, gentle orbit), shallow depth of field. One service, one emotional idea (relief, trust, joy), a clean end frame with the healthy, happy pet."},
}

SAFETY = """CLINIC RULES (always): no medical claims, promises or guarantees ("cures", "pain-free", "100% safe"); no prices unless they appear in the APPROVED FEES given to you; no real client or pet names unless the clinic typed them in this interview; nothing graphic (no needles going in, blood, surgery, injured or sick-looking animals); never imply a diagnosis. Any text in an image must be real, short and spelled correctly."""

INTERVIEW_PROMPT = """You are interviewing someone at a small veterinary clinic (usually the vet who owns it) to make one {kind} for the clinic's own marketing: {format_label}. {format_blurb}
You are the only part of this system that talks to them. They know their clinic, their patients and their clients deeply and know nothing about marketing, filming or design, have no time, and would never admit not knowing. So:
- NEVER ask about camera, shots, lighting, colours, fonts, layout, editing, music, "style", "aesthetic", "mood" or platforms. Those are decided elsewhere and are none of their concern.
- Ask ONLY what they know better than anyone: which service or message, which pets and owners it is for, what clients actually ask or worry about, what makes this clinic different, a real moment from their week, one real number.
- You ALREADY KNOW the clinic facts below; never ask for them, use them.
WHAT MAKES IT WORK: a pet owner decides in under a second whether to keep looking, and shares only what is specific. So dig for: THE ONE MESSAGE (one service or idea, not five); WHO IT IS FOR (new puppy owners, senior cats, people who avoid the vet); THE SPECIFIC FACT a person would repeat (a real number or a real habit: "most dogs over 3 have some dental disease" only if they say it, "we see 20 new puppies a month"); THE MOMENT (a real, happy, visual moment from the clinic that shows it); and THE FEEL they want (always ask once: warm and reassuring, fun and playful, calm and professional, or let you decide).
A vague answer ("people love us", "we care") is the start of a follow-up, not the end: ask the sharper question that gets the real detail, once, then move on.
Rules: exactly ONE question at a time with 3-4 short, concrete, clearly different options they can tap (realistic guesses at their answer), never an "other" option (they can always type). Questions under 15 words, options under 6 words. Build on their previous answers by name. Stop once you have enough for a specific, striking {kind}, usually after 3-5 questions; always ask the feel question first. Hard cap: {max_q} questions.
{safety}
Reply with strict JSON only: {{"done": false, "question": "...", "options": ["...", "...", "..."]}} or {{"done": true}}.

CLINIC FACTS YOU ALREADY KNOW:
{facts}"""

WRITER_IMAGE = """You write the final brief for an AI image generator (GPT Image) making ONE promotional still for a veterinary clinic, plus its social caption and a short campaign text.
Format and its fixed style direction: {format_label}. {doctrine}
Use the interview and the clinic facts. Weave the clinic's real details in naturally; never invent a fact, number, price or name. If a reference photo was given, the image must match it closely (that real pet, that real clinic) rather than inventing a stand-in.
"prompt": one paragraph, 3-6 sentences, plain prose: the scene, the subject, the composition, the exact short headline text to render (quote it), the clinic name, and that any text must be real, legible and correctly spelled, and that it should look professional and ready to post. Never use the words AI, generated or prompt.
"caption": a warm social caption, 1-3 short sentences plus up to 3 relevant hashtags (one local, like #LangleyBC).
"sms_text": a campaign text under 150 characters that a client would actually read: warm, one clear message, the clinic name. No link (it is added for you), no prices unless approved.
"title": a few words naming this creative for the clinic's library.
"plan": {{"hook": what makes someone stop and why it fits, "arc": the one message and how the image delivers it, "checks": the one real fact from their answers you used, and confirmation that no rule below is broken}}.
{safety}
Reply with strict JSON only: {{"plan": {{"hook": "...", "arc": "...", "checks": "..."}}, "prompt": "...", "caption": "...", "sms_text": "...", "title": "..."}}

CLINIC FACTS:
{facts}"""

WRITER_VIDEO = """You are a director and prompt writer making ONE short vertical (9:16) video for a veterinary clinic with ByteDance Seedance 2.5 (text-to-video, joint audio and video, strong adherence over one continuous take of up to {seconds} seconds), plus its social caption and a short campaign text.
Format and its fixed doctrine: {format_label}. {doctrine}
How to make it work (decided by you, never by the clinic):
1. THE FIRST SECOND IS EVERYTHING: frame one already has motion and a pet, and it is instantly clear what this is about. The opening beat is the shortest (under 2 seconds).
2. The second beat delivers on the first, never a pause or a wide establishing shot.
3. No dead beats (walking to a door, waiting, tidying). Each beat either moves the story or shows a warm, satisfying moment (a happy wag, a treat taken, a calm cuddle).
4. Escalate in warmth; the most joyful footage is in the final third; end on a frame that loops back to the start.
5. One idea only. Use the ONE real fact or number from the interview, exactly as given, as on-screen text or in the action; never invent one.
6. Cause and effect: each beat starts where the previous one ended (same hands, same pet, camera carries over). One primary action and one camera move per beat.
7. Concrete, never vague: describe what is seen ("a golden retriever's tail thumping on the exam table"), never "cinematic" or "beautiful".
8. Audio is generated with the picture: end the prompt with one line "Audio:" naming the real sounds and when they land (a happy bark at the door, soft clinic ambience, a treat bag rustle); no voiceover unless the clinic asked for words, no copyrighted music.
Write "prompt" as a timestamped shot list for {seconds} seconds, one line per beat ("0-2s: ..."), each naming the camera position or move and the action, then the Audio line. Keep it under about 2800 characters.
Also: "caption" (1-3 warm sentences plus up to 3 hashtags, one local like #LangleyBC), "sms_text" (under 150 characters, warm, one message, the clinic name, no link, no unapproved prices), "title" (a few words for the clinic's library), and first a "plan" {{"hook", "arc", "checks"}} as your working note: the hook shape and why it fits, the arc beat by beat in one line, and the one real fact you used plus confirmation that no rule is broken. If the plan and prompt disagree, fix the prompt.
{safety}
Reply with strict JSON only: {{"plan": {{"hook": "...", "arc": "...", "checks": "..."}}, "prompt": "...", "caption": "...", "sms_text": "...", "title": "..."}}

CLINIC FACTS:
{facts}"""


# ---------------------------------------------------------------- settings and money
def get_cfg(db: Session) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    stored = settings_store.get_raw(db, KEY, None)
    if isinstance(stored, dict):
        cfg.update({k: v for k, v in stored.items() if k in DEFAULTS})
    return cfg


def public_cfg(db: Session) -> dict[str, Any]:
    cfg = get_cfg(db)
    return {"has_key": bool(cfg["wavespeed_key_enc"]), "key_last4": cfg["wavespeed_last4"], "monthly_budget_usd": cfg["monthly_budget_usd"],
            "spent_this_month_usd": round(spent_this_month(db), 2)}


def estimate_usd(kind: str, opts: dict[str, Any], with_reference: bool = False) -> float:
    """The provider's own published prices (the same formulas Contento measured), before any model-writing cost."""
    if kind == "video":
        mult = {"480p": 1, "720p": 2, "1080p": 5}.get(opts.get("resolution", "720p"), 2)
        return round(900000 * mult * int(opts.get("duration", 10)) / 5 / 1_000_000, 2)
    table = {"low": 0.01, "medium": 0.06, "high": 0.22}
    return round(table.get(opts.get("quality", "medium"), 0.06) + (0.01 if with_reference else 0.0), 3)


def spent_this_month(db: Session) -> float:
    start = timeutil.local(timeutil.now_utc()).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return float(db.scalar(select(func.coalesce(func.sum(Creative.cost_usd + Creative.llm_cost_usd), 0.0)).where(Creative.created_at >= start)) or 0.0)


def default_options(kind: str) -> dict[str, Any]:
    return {"resolution": "720p", "duration": 10} if kind == "video" else {"quality": "medium", "aspect_ratio": "4:5"}


# ---------------------------------------------------------------- what the interviewer and writer already know
def clinic_facts(db: Session) -> str:
    from app.assistant import ai, booking
    kb, _ = ai.knowledge_block(db)
    name = settings_store.get_raw(db, "clinic_name", None) or "Walnut Grove Animal Hospital"
    hours = booking.get_hours(db)["days"]
    week = "; ".join(f"{d}: " + (", ".join(f"{a}-{b}" for a, b in hours.get(d, [])) or "closed") for d in booking.DAYS)
    out = [f"Clinic: {name}, Langley, British Columbia. Opening hours: {week}."]
    try:
        from app.models import WebsiteAudit
        a = db.scalar(select(WebsiteAudit).where(WebsiteAudit.status == "done").order_by(WebsiteAudit.id.desc()).limit(1))
        if a:
            pages = json.loads(a.pages_json)
            home = next((p for p in pages if (p.get("url") or "").rstrip("/").count("/") <= 2), None)
            if home and home.get("text"):
                out.append("From the clinic's own website: " + home["text"][:1500])
    except Exception:  # noqa: BLE001
        pass
    out.append(kb[:5000])
    return "\n".join(out)


def _fmt(kind: str, key: str) -> dict[str, str]:
    table = VIDEO_FORMATS if kind == "video" else IMAGE_FORMATS
    if key not in table:
        raise ValueError("format")
    return table[key]


# ---------------------------------------------------------------- the model calls (no transaction open while they run)
def _llm(db: Session, messages: list[dict[str, Any]], *, temperature: float, max_tokens: int, timeout: float = 120.0) -> tuple[dict[str, Any], float]:
    from app.adapters import llm as llm_adapter
    from app.assistant import ai
    cfg = ai.get_config(db)
    if not cfg.get("key_enc"):
        raise RuntimeError("The OpenRouter key is not set (Admin, AI assistant).")
    key = crypto.decrypt(cfg["key_enc"])
    db.commit()
    payload = {"model": cfg["model"], "messages": messages, "temperature": temperature, "max_tokens": max_tokens, "response_format": {"type": "json_object"},
               "provider": {"require_parameters": True, **({"data_collection": "deny"} if cfg["deny_data_collection"] else {})}, "usage": {"include": True}}
    last_err = "no answer"
    for _ in range(2):  # one retry when an answer comes back cut off or malformed
        with httpx.Client(timeout=timeout, transport=llm_adapter.TRANSPORT) as h:
            r = h.post(ai.URL, json=payload, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-Title": "Walnut Grove Desk Creative studio"})
        if r.status_code != 200:
            last_err = f"http_{r.status_code}"
            continue
        body = r.json()
        content = (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        m = re.search(r"\{.*\}", content, re.S)
        try:
            data = json.loads(m.group(0)) if m else None
        except ValueError:
            data = None
        if isinstance(data, dict):
            return data, float((body.get("usage") or {}).get("cost") or 0.0)
        last_err = "bad_json"
    raise RuntimeError(f"The writing model did not answer properly ({last_err}). Try again.")


def _clean(text: str) -> str:
    return re.sub(r"\s*[\u2014\u2013]\s*", ", ", str(text or "")).strip()


def next_turn(db: Session, cid: int, answer: str | None) -> Creative:
    """Append the answer (if any), ask the model for the next question; when it is done, write the brief."""
    c = db.get(Creative, cid)
    msgs = json.loads(c.messages_json)
    qa = json.loads(c.qa_json)
    asked = sum(1 for m in msgs if m["role"] == "assistant")
    if answer is not None:
        q = json.loads(c.question_json or "{}").get("question", "")
        qa.append({"question": q, "answer": answer[:1000]})
        msgs.append({"role": "user", "content": f"{answer[:1000]}\n\n[Questions asked so far: {asked} (cap {MAX_QUESTIONS}). "
                                               + ("This must be the last question if you ask one at all." if asked >= MAX_QUESTIONS - 1 else "Ask the next question, or stop if you have enough.") + "]"})
    else:
        msgs.append({"role": "user", "content": "[Begin the interview. Ask your first question.]"})
    done = asked >= MAX_QUESTIONS
    cost = 0.0
    if not done:
        data, cost = _llm(db, msgs, temperature=0.8, max_tokens=600)
        c = db.get(Creative, cid)
        done = bool(data.get("done")) and asked >= 1
        if not done:
            question = _clean(data.get("question") or "")[:200]
            options = [_clean(o)[:60] for o in (data.get("options") or []) if str(o).strip()][:4]
            if not question:
                done = True
            else:
                msgs.append({"role": "assistant", "content": json.dumps({"done": False, "question": question, "options": options})})
                c.messages_json, c.qa_json, c.question_json = json.dumps(msgs), json.dumps(qa), json.dumps({"question": question, "options": options})
                c.llm_cost_usd = (c.llm_cost_usd or 0) + cost
                db.commit()
                return c
    c = db.get(Creative, cid)
    c.messages_json, c.qa_json, c.question_json = json.dumps(msgs), json.dumps(qa), "{}"
    c.llm_cost_usd = (c.llm_cost_usd or 0) + cost
    db.commit()
    return write(db, cid)


def write(db: Session, cid: int) -> Creative:
    from app.assistant import ai
    c = db.get(Creative, cid)
    f = _fmt(c.kind, c.format)
    opts = json.loads(c.options_json or "{}")
    facts = clinic_facts(db)
    system = (WRITER_VIDEO.format(seconds=int(opts.get("duration", 10)), format_label=f["label"], doctrine=f["doctrine"], safety=SAFETY, facts=facts)
              if c.kind == "video" else WRITER_IMAGE.format(format_label=f["label"], doctrine=f["doctrine"], safety=SAFETY, facts=facts))
    qa = json.loads(c.qa_json or "[]")
    interview = "\n".join(f"Q: {x['question']}\nA: {x['answer']}" for x in qa) or "(no answers: use the format and the clinic facts)"
    ref = "\nA real reference photo was attached: match it closely (that real pet or place)." if c.reference_url else ""
    data, cost = _llm(db, [{"role": "system", "content": system}, {"role": "user", "content": f"THE INTERVIEW:\n{interview}{ref}\n\nWrite it now."}],
                      temperature=0.6, max_tokens=3500, timeout=180.0)
    c = db.get(Creative, cid)
    prompt = _clean(data.get("prompt") or "")
    if not prompt:
        raise RuntimeError("The writing model returned an empty brief. Try again.")
    c.prompt = prompt[:6000]
    c.caption = _clean(data.get("caption") or "")[:1200]
    c.sms_text = _clean(data.get("sms_text") or "")[:300]
    c.title = (_clean(data.get("title") or "") or f["label"])[:160]
    c.plan_json = json.dumps({k: _clean((data.get("plan") or {}).get(k, ""))[:600] for k in ("hook", "arc", "checks")})
    c.llm_cost_usd = (c.llm_cost_usd or 0) + cost
    c.status = "review"
    for text in (c.caption, c.sms_text):
        if ai.FORBIDDEN.search(text):
            c.error = "Check the wording: it mentions a medicine or a dose, which the clinic's rules do not allow in marketing."
    db.commit()
    return c


# ---------------------------------------------------------------- WaveSpeed
def _ws_key(db: Session) -> str:
    cfg = get_cfg(db)
    if not cfg["wavespeed_key_enc"]:
        raise RuntimeError("Add the WaveSpeed key first (Creative studio, Settings).")
    return crypto.decrypt(cfg["wavespeed_key_enc"])


def upload_reference(db: Session, cid: int, data: bytes, filename: str, content_type: str) -> str:
    key = _ws_key(db)
    db.commit()
    with httpx.Client(timeout=60.0, transport=TRANSPORT) as h:
        r = h.post(f"{WAVESPEED}/media/upload/binary", headers={"Authorization": f"Bearer {key}"}, files={"file": (filename, data, content_type)})
    body = r.json() if r.content else {}
    url = (body.get("data") or {}).get("download_url") or body.get("download_url")
    if r.status_code >= 400 or not url:
        raise RuntimeError("The photo could not be uploaded to the generator. Try again.")
    c = db.get(Creative, cid)
    c.reference_url = url[:600]
    db.commit()
    return url


def generate(db: Session, cid: int) -> Creative:
    c = db.get(Creative, cid)
    if c.status not in ("review", "failed"):
        raise RuntimeError("Finish the interview first.")
    cfg = get_cfg(db)
    opts = json.loads(c.options_json or "{}")
    est = estimate_usd(c.kind, opts, bool(c.reference_url))
    if spent_this_month(db) + est > float(cfg["monthly_budget_usd"]):
        raise RuntimeError(f"This would go over the monthly budget of ${cfg['monthly_budget_usd']:.0f}. Raise it in Settings if that is intended.")
    key = _ws_key(db)
    if c.kind == "video":
        if c.reference_url:
            model, body = cfg["video_image_model"], {"prompt": c.prompt, "image": c.reference_url, "duration": int(opts.get("duration", 10)),
                                                    "resolution": opts.get("resolution", "720p"), "generate_audio": True}
        else:
            model, body = cfg["video_model"], {"prompt": c.prompt, "aspect_ratio": "9:16", "duration": int(opts.get("duration", 10)),
                                              "resolution": opts.get("resolution", "720p"), "generate_audio": True}
    else:
        base = {"prompt": c.prompt, "aspect_ratio": opts.get("aspect_ratio", "4:5"), "quality": opts.get("quality", "medium"), "resolution": "1k"}
        model, body = (cfg["image_edit_model"], {**base, "images": [c.reference_url]}) if c.reference_url else (cfg["image_model"], base)
    db.commit()
    with httpx.Client(timeout=60.0, transport=TRANSPORT) as h:
        r = h.post(f"{WAVESPEED}/{model}", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=body)
    resp = r.json() if r.content else {}
    task = resp.get("data") or resp
    c = db.get(Creative, cid)
    if r.status_code >= 400 or not task.get("id"):
        c.status, c.error = "failed", (str(resp.get("message") or f"The generator answered {r.status_code}."))[:300]
        db.commit()
        return c
    c.task_url = ((task.get("urls") or {}).get("get") or f"{WAVESPEED}/predictions/{task['id']}/result")[:600]
    c.status, c.error, c.cost_usd, c.generated_at = "generating", "", est, timeutil.now_utc()
    db.commit()
    return c


def storage_dir() -> str:
    from app import config
    d = os.path.join(os.path.dirname(os.path.abspath(config.get_settings().db_path)), "creatives")
    os.makedirs(d, mode=0o750, exist_ok=True)
    return d


def poll_once(db: Session) -> int:
    """Check generating creatives; download finished ones to this server (provider links expire) and give them a share link."""
    rows = db.scalars(select(Creative).where(Creative.status == "generating")).all()
    if not rows:
        return 0
    key = _ws_key(db)
    jobs = [(c.id, c.task_url, c.kind, c.generated_at) for c in rows]
    db.commit()
    n = 0
    for cid, url, kind, started in jobs:
        try:
            with httpx.Client(timeout=60.0, transport=TRANSPORT, follow_redirects=True) as h:
                body = h.get(url, headers={"Authorization": f"Bearer {key}"}).json()
                data = body.get("data") or body
                status = data.get("status")
                outputs = data.get("outputs") or []
                path = name = None
                if status == "completed" and outputs:
                    ext = "mp4" if kind == "video" else (os.path.splitext(outputs[0].split("?")[0])[1].lstrip(".").lower() or "png")
                    ext = ext if ext in ("mp4", "png", "jpg", "jpeg", "webp") else ("mp4" if kind == "video" else "png")
                    name = f"{cid}-{secrets.token_hex(6)}.{ext}"
                    path = os.path.join(storage_dir(), name)
                    with h.stream("GET", outputs[0]) as dl, open(path, "wb") as fh:
                        size = 0
                        for chunk in dl.iter_bytes():
                            size += len(chunk)
                            if size > MAX_FILE:
                                raise RuntimeError("too_big")
                            fh.write(chunk)
        except Exception as e:  # noqa: BLE001
            log.warning("creative poll failed (%s)", type(e).__name__)
            continue
        c = db.get(Creative, cid)
        if status == "completed" and name:
            c.status, c.output_url, c.file_name, c.done_at = "done", outputs[0][:800], name, timeutil.now_utc()
            c.public_token = c.public_token or secrets.token_urlsafe(16)
            n += 1
        elif status in ("failed", "cancelled", "timeout"):
            c.status, c.error = "failed", (str(data.get("error") or status))[:300]
            c.cost_usd = 0.0
        elif started and timeutil.now_utc() - started > timedelta(minutes=45):
            c.status, c.error = "failed", "The generator took too long. Try again."
        db.commit()
    return n


def tick() -> None:
    from app import db as app_db
    with app_db.get_sessionmaker()() as s:
        try:
            if s.scalar(select(func.count()).select_from(Creative).where(Creative.status == "generating")):
                poll_once(s)
        except Exception as e:  # noqa: BLE001
            s.rollback()
            log.warning("creative tick failed (%s)", type(e).__name__)


def share_url(c: Creative) -> str | None:
    from app import config
    base = (config.get_settings().public_url or os.environ.get("WALNUT_PUBLIC_URL", "")).rstrip("/")
    return f"{base}/c/{c.public_token}" if c.public_token and base else None


def in_background(fn, *args) -> None:
    """Run a slow step (a model call) after the request returns, in its own session."""
    def go():
        from app import db as app_db
        with app_db.get_sessionmaker()() as s:
            try:
                fn(s, *args)
            except Exception as e:  # noqa: BLE001
                s.rollback()
                c = s.get(Creative, args[0])
                if c is not None:
                    c.error = (str(e) if isinstance(e, RuntimeError) else f"Something went wrong ({type(e).__name__}). Try again.")[:300]
                    s.commit()
    if os.environ.get("WALNUT_ENV") == "test":
        go()
    else:
        threading.Thread(target=go, daemon=True).start()
