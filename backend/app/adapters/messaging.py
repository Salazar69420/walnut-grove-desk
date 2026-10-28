"""Messaging adapters. Nothing is ever sent: both write an outbox row (status would_send) and log it.

There is no real provider code. A real provider would implement the same two protocols.
"""
from __future__ import annotations

import logging
from typing import Protocol

from sqlalchemy.orm import Session

from app.models import Outbox

log = logging.getLogger("walnut.outbox")


class SmsAdapter(Protocol):
    def send(self, to_label: str, body: str) -> Outbox: ...


class VoiceAdapter(Protocol):
    def call(self, to_label: str, script: str) -> Outbox: ...


class _DryRun:
    channel = ""

    def __init__(self, db: Session, source_type: str = "", source_id: int | str = "", created_by: str | None = None):
        self.db = db
        self.source_type = source_type
        self.source_id = str(source_id)
        self.created_by = created_by

    def _write(self, to_label: str, body: str) -> Outbox:
        row = Outbox(channel=self.channel, to_label=to_label, body=body, status="would_send",
                     source_type=self.source_type, source_id=self.source_id, created_by=self.created_by)
        self.db.add(row)
        self.db.flush()
        log.info("would send %s to %s (outbox %s)", self.channel, to_label, row.id)
        return row


class DryRunSms(_DryRun):
    channel = "sms"

    def send(self, to_label: str, body: str) -> Outbox:
        return self._write(to_label, body)


class DryRunVoice(_DryRun):
    channel = "voice"

    def call(self, to_label: str, script: str) -> Outbox:
        return self._write(to_label, script)


def queue_sms(db: Session, to_label: str, body: str, source_type: str = "", source_id: int | str = "",
              created_by: str | None = None) -> Outbox:
    """Wave 1 entry point, kept for existing callers."""
    return DryRunSms(db, source_type, source_id, created_by).send(to_label, body)
