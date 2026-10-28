"""Convert Retell's payloads to the internal schema, and results back to Retell."""
from __future__ import annotations

import re
from typing import Any

from app.retell.schema import FunctionCall


def to_function_call(payload: dict[str, Any], name_from_path: str) -> FunctionCall:
    """Accepts the internal shape {"call_id","name","args"} or Retell's {"call":{...},"name","args"}."""
    call = payload.get("call") if isinstance(payload.get("call"), dict) else {}
    call_id = payload.get("call_id") or call.get("call_id") or ""
    args = payload.get("args")
    return FunctionCall(call_id=str(call_id)[:120], name=name_from_path, args=args if isinstance(args, dict) else {})


def to_response(result: dict[str, Any]) -> dict[str, Any]:
    return {"result": result}


_LINE = re.compile(r"^\s*(Agent|User)\s*:\s*(.*)$", re.I)


def parse_transcript(transcript: str) -> list[dict[str, str]]:
    """Lines starting 'Agent:' or 'User:' become {"who": "bot"|"client", "text"}. Other lines continue the last one."""
    out: list[dict[str, str]] = []
    for line in (transcript or "").splitlines():
        if not line.strip():
            continue
        m = _LINE.match(line)
        if m:
            out.append({"who": "bot" if m.group(1).lower() == "agent" else "client", "text": m.group(2).strip()})
        elif out:
            out[-1]["text"] = (out[-1]["text"] + " " + line.strip()).strip()
    return out
