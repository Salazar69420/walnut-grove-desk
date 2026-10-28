"""SMS adapter. Nothing is ever sent: it writes an outbox row and logs 'would send'."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import Outbox

log = logging.getLogger("walnut.outbox")


def queue_sms(
    db: Session,
    to_label: str,
    body: str,
    source_type: str = "",
    source_id: int | str = "",
    created_by: str | None = None,
) -> Outbox:
    row = Outbox(
        channel="sms",
        to_label=to_label,
        body=body,
        status="would_send",
        source_type=source_type,
        source_id=str(source_id),
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    log.info("would send sms to %s (outbox %s)", to_label, row.id)
    return row
