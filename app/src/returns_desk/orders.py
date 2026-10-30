"""Load orders and requests."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .types import Label, Order, OrderLine, Request, ResponsibleParty


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def load_orders(path: str | Path) -> dict[str, Order]:
    rows = json.loads(Path(path).read_text())
    if isinstance(rows, dict):
        rows = rows.get("orders", [])
    out: dict[str, Order] = {}
    for r in rows:
        lines = tuple(OrderLine(l["sku"], int(l["qty"]), float(l["paid"])) for l in r.get("lines", []))
        customer = r.get("customer_id") or r["customer"]      # orders.json; the sketch shows `customer`
        out[r["order_id"]] = Order(r["order_id"], customer, date.fromisoformat(r["placed"]),
                                   _date(r.get("delivered")), lines)
    return out


def load_requests(path: str | Path) -> list[Request]:
    rows = json.loads(Path(path).read_text())
    if isinstance(rows, dict):
        rows = rows.get("requests", [])
    out: list[Request] = []
    for r in rows:
        lab = r.get("label")
        label = Label(ResponsibleParty(lab["responsible_party"]), lab["source"]) if lab else None
        out.append(Request(
            request_id=r["request_id"], order_id=r["order_id"], sku=r["sku"],
            submitted=date.fromisoformat(r["submitted"]), reason_code=r.get("reason_code"),
            statement=r.get("statement") or "", photo=r.get("photo"), label=label,
            demo=bool(r.get("demo", False)),
        ))
    return out
