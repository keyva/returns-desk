"""The resolution log: every resolution the desk has made, one JSON object per line."""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timezone
from pathlib import Path

from .types import (
    Disposition, Order, Reading, Request, Resolution, Resolver, ResponsibleParty, Scores,
)

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "logs" / "resolutions.jsonl"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def to_dict(r: Resolution) -> dict:
    return {
        "request_id": r.request_id,
        "resolver": r.resolver.value,
        "responsible_party": r.responsible_party.value,
        "disposition": r.disposition.value,
        "amount": r.amount,
        "fee": r.fee,
        "return_postage": r.return_postage,
        "note": r.note,
        "reply_draft": r.reply_draft,
        "reading": r.reading.model_dump(mode="json") if r.reading else None,
        "scores": ({"probabilities": {ResponsibleParty(k).value: v for k, v in r.scores.probabilities.items()},
                    "confidence": r.scores.confidence} if r.scores else None),
        "resolved_at": r.resolved_at,
    }


def from_dict(d: dict) -> Resolution:
    s = d.get("scores")
    return Resolution(
        request_id=d["request_id"],
        resolver=Resolver(d["resolver"]),
        responsible_party=ResponsibleParty(d["responsible_party"]),
        disposition=Disposition(d["disposition"]),
        amount=float(d["amount"]),
        fee=float(d["fee"]),
        return_postage=d["return_postage"],
        note=d.get("note", ""),
        reply_draft=d.get("reply_draft", ""),
        reading=Reading.model_validate(d["reading"]) if d.get("reading") else None,
        scores=(Scores({ResponsibleParty(k): float(v) for k, v in s["probabilities"].items()},
                       float(s["confidence"])) if s else None),
        resolved_at=d.get("resolved_at", ""),
    )


def _quarter_start(today: date) -> date:
    return date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)


class ResolutionLog:
    def __init__(self, path: str | Path | None = None, clock: Callable[[], datetime] | None = None):
        self.path = Path(path) if path else DEFAULT_PATH
        self.clock = clock or _now

    def append(self, resolution: Resolution) -> Resolution:
        resolution.resolved_at = self.clock().isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(to_dict(resolution)) + "\n")
        return resolution

    def all(self) -> list[Resolution]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [from_dict(json.loads(l)) for l in lines if l.strip()]

    def by_request(self) -> dict[str, Resolution]:
        """Latest resolution per request id."""
        return {r.request_id: r for r in self.all()}

    def returns_this_quarter(self, customer_id: str, today: date, orders: Mapping[str, Order],
                             requests: Iterable[Request], before: Request | None = None) -> int:
        """Requests from this customer submitted in today's calendar quarter that are in the log,
        or that are ahead of `before` (submitted earlier). `before` itself is never counted, and
        neither is a request the log shows denied: a request turned down is not a return."""
        start = _quarter_start(today)
        logged = self.by_request()
        reqs = list(requests)
        order_of = {r.request_id: i for i, r in enumerate(reqs)}
        count = 0
        for r in reqs:
            if before is not None and r.request_id == before.request_id:
                continue
            o = orders.get(r.order_id)
            if o is None or o.customer_id != customer_id or not (start <= r.submitted <= today):
                continue
            ahead = before is not None and (
                (r.submitted, order_of[r.request_id]) < (before.submitted, order_of.get(before.request_id, -1)))
            if r.request_id in logged and logged[r.request_id].disposition == Disposition.DENY:
                continue
            if r.request_id in logged or ahead:
                count += 1
        return count

    def reset(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
