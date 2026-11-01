"""Website analytics: visitors and page views of the clinic website, read from Vercel Web Analytics.

* Uses the same Vercel connection as the deployment agent (SEO agent, Deployment agent); read only.
* Vercel's free plan keeps one month of data and has no custom events or campaign (UTM) breakdown. Every few hours the app
  copies the daily totals into `web_analytics_days`, so the trend can go back further than Vercel keeps it.
* Answers are cached for ten minutes: the dashboard and the assistant do not hit Vercel on every look.
* Sanity Studio pages (/studio) are staff using the CMS, not visitors; they are left out of the page lists.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import PhoneCall, WebAnalyticsDay, WebsiteAudit
from app.security import crypto
from app.services import deploy_agent, timeutil

log = logging.getLogger("walnut.web_analytics")
BASE = "/v1/query/web-analytics/visits"
DIMENSIONS = {"pages": "requestPath", "referrers": "referrerHostname", "countries": "country", "devices": "deviceType", "browsers": "browserName", "systems": "osName"}
CACHE_SECONDS = 600
WINDOW_DAYS = 30  # what Vercel's free plan guarantees to keep
_cache: dict[tuple, tuple[float, Any]] = {}


class NotConnected(Exception):
    pass


def _conn(db: Session) -> tuple[str, dict[str, Any]]:
    cfg = deploy_agent.get_cfg(db)
    if not deploy_agent.connected(cfg):
        raise NotConnected()
    return crypto.decrypt(cfg["token_enc"]), cfg


def _q(token: str, cfg: dict[str, Any], kind: str, params: dict[str, Any]) -> Any:
    key = (cfg["project_id"], kind, tuple(sorted(params.items())))
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    with deploy_agent.Vc(token, cfg["team_id"]) as vc:
        data = vc.req("GET", f"{BASE}/{kind}", params={"projectId": cfg["project_id"], **params}).get("data")
    _cache[key] = (time.monotonic(), data)
    if len(_cache) > 300:
        for k in sorted(_cache, key=lambda k: _cache[k][0])[:150]:
            _cache.pop(k, None)
    return data


def _days(token: str, cfg: dict[str, Any], since: date, until: date) -> dict[str, dict[str, int]]:
    rows = _q(token, cfg, "aggregate", {"by": "day", "since": since.isoformat(), "until": until.isoformat()}) or []
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        d = str(r.get("timestamp", ""))[:10]
        if d:
            out[d] = {"pageviews": int(r.get("pageviews") or 0), "visitors": int(r.get("visitors") or 0)}
    return out


def _top(token: str, cfg: dict[str, Any], dim: str, since: date, until: date, limit: int = 10) -> list[dict[str, Any]]:
    rows = _q(token, cfg, "aggregate", {"by": dim, "since": since.isoformat(), "until": until.isoformat(), "limit": limit + 3}) or []
    out = []
    for r in rows:
        name = r.get(dim)
        name = "Direct or unknown" if name in (None, "", "null") else str(name)
        if dim == "requestPath" and (name == "/studio" or name.startswith("/studio/")):
            continue
        out.append({"name": name, "pageviews": int(r.get("pageviews") or 0), "visitors": int(r.get("visitors") or 0)})
    out.sort(key=lambda x: (-x["visitors"], -x["pageviews"]))
    return out[:limit]


def sync(db: Session) -> int:
    """Copy the daily totals Vercel still has into the database (idempotent). Returns the number of days written."""
    try:
        token, cfg = _conn(db)
    except NotConnected:
        return 0
    today = datetime.now(timezone.utc).date()
    db.commit()  # never hold the database while waiting for Vercel
    days = _days(token, cfg, today - timedelta(days=WINDOW_DAYS), today)
    now = timeutil.now_utc()
    for d, v in days.items():
        row = db.get(WebAnalyticsDay, d)
        if row is None:
            db.add(WebAnalyticsDay(day=d, updated_at=now, **v))
        elif (row.pageviews, row.visitors) != (v["pageviews"], v["visitors"]):
            row.pageviews, row.visitors, row.updated_at = v["pageviews"], v["visitors"], now
    db.commit()
    return len(days)


def dashboard(db: Session, days: int) -> dict[str, Any]:
    """Everything the Website analytics page shows for the last `days` days, with the period before for comparison."""
    token, cfg = _conn(db)
    today = datetime.now(timezone.utc).date()
    since = today - timedelta(days=days - 1)
    prev_since = since - timedelta(days=days)
    db.commit()
    live = _days(token, cfg, max(prev_since, today - timedelta(days=WINDOW_DAYS + 5)), today)
    stored = {r.day: {"pageviews": r.pageviews, "visitors": r.visitors} for r in db.scalars(select(WebAnalyticsDay).where(WebAnalyticsDay.day >= prev_since.isoformat())).all()}
    merged = {**stored, **live}  # Vercel's own numbers win for the days it still has

    def span(a: date, b: date) -> list[dict[str, Any]]:
        out, d = [], a
        while d <= b:
            v = merged.get(d.isoformat(), {"pageviews": 0, "visitors": 0})
            out.append({"day": d.isoformat(), **v, "known": d.isoformat() in merged})
            d += timedelta(days=1)
        return out
    series = span(since, today)
    prev = span(prev_since, since - timedelta(days=1))
    total = {k: sum(x[k] for x in series) for k in ("pageviews", "visitors")}
    before = {k: sum(x[k] for x in prev) for k in ("pageviews", "visitors")}
    prev_known = sum(1 for x in prev if x["known"])
    top_since = max(since, today - timedelta(days=WINDOW_DAYS))  # page lists only exist for the days Vercel still keeps
    tops = {name: _top(token, cfg, dim, top_since, today) for name, dim in DIMENSIONS.items()}
    lo = datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc)
    calls = db.scalar(select(func.count()).select_from(PhoneCall).where(PhoneCall.started_at >= lo, PhoneCall.direction == "inbound")) or 0
    audits = db.scalars(select(WebsiteAudit).where(WebsiteAudit.status == "done").order_by(WebsiteAudit.id.desc()).limit(12)).all()
    seo = [{"at": timeutil.iso(a.finished_at or a.started_at), "score": (json.loads(a.summary_json or "{}") or {}).get("score"),
            "areas": (json.loads(a.summary_json or "{}") or {}).get("areas"), "url": a.base_url} for a in reversed(audits)]
    return {
        "days": days, "since": since.isoformat(), "until": today.isoformat(), "site": (cfg.get("domains") or [""])[0],
        "totals": {**total, "pages_per_visitor": round(total["pageviews"] / total["visitors"], 2) if total["visitors"] else 0},
        "previous": before if prev_known >= max(1, days // 2) else None,  # only compare when most of the earlier period is known
        "series": series, "top": tops, "top_since": top_since.isoformat(), "inbound_calls": calls,
        "seo": [x for x in seo if x["score"] is not None], "collecting": total["pageviews"] > 0 or any(x["known"] and x["pageviews"] for x in prev),
        "limits": {"window_days": WINDOW_DAYS, "plan_note": "Vercel's free plan keeps one month and has no campaign or click tracking; the app keeps a daily copy of the totals."},
    }


def tick() -> None:
    """Background: refresh the stored daily totals (called every few hours from the main loop)."""
    from app import db as app_db
    with app_db.get_sessionmaker()() as s:
        try:
            sync(s)
        except Exception as e:  # noqa: BLE001
            s.rollback()
            log.warning("web analytics sync failed (%s)", type(e).__name__)
