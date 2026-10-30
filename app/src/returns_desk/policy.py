"""JsonPolicy: the retailer's rules, read from a data file. Never imports an engine."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .types import (
    Assessment, AutoApproval, Disposition, Item, Order, Request,
    ResponsibilityRow, ResponsibleParty,
)

_EMPTY_ROW = ResponsibilityRow(frozenset())


class JsonPolicy:
    def __init__(self, data: dict):
        self._d = data

    @classmethod
    def from_file(cls, path: str | Path) -> "JsonPolicy":
        return cls(json.loads(Path(path).read_text()))

    def assess(self, request: Request, order: Order | None, item: Item | None,
               returns_this_quarter: int, today: date) -> Assessment:
        line = order.line(request.sku) if order else None
        a = Assessment(request, order, item, line, True, returns_this_quarter=returns_this_quarter)

        def deny(rule: str, reply: str) -> Assessment:
            a.passes, a.denial_rule, a.denial_reply = False, rule, reply
            return a

        if order is None or line is None or item is None:
            return deny("order", f"Thanks for writing. Under our order rule we could not match "
                                 f"{request.sku} to order {request.order_id}, so we cannot take this return.")
        cat = self._d["categories"].get(item.category)
        if not cat or not cat.get("returnable", False):
            return deny("category", f"Thanks for writing. Under our category rule, {item.name} "
                                    f"is not returnable, so we cannot accept it back.")
        # No delivery date: the window cannot be counted and a lost parcel cannot be photographed,
        # so the clerk decides. The cap still applies.
        if order.delivered is None:
            a.clerk_rule = "no_delivery_date"
        days = self._d["window_days"]
        if order.delivered is not None and (request.submitted - order.delivered).days > days:
            return deny("window", f"Thanks for writing. Under our window rule, returns must be "
                                  f"requested within {days} days of delivery, and this one falls outside it.")
        # A photo is evidence for putting the cost on the retailer or the carrier; a reason code that
        # puts it on the customer leaves nothing for a photo to prove.
        customer_code = (not request.has_statement and request.reason_code is not None
                         and self.reason_code_party(request.reason_code) == ResponsibleParty.CUSTOMER)
        if (line.paid > cat["photo_required_above"] and request.photo is None
                and a.clerk_rule is None and not customer_code):
            return deny("photo_required", f"Thanks for writing. Under our photo_required rule, items over "
                                          f"${cat['photo_required_above']:.2f} need a photo with the request. "
                                          f"Please send one and we will take another look.")
        cap = self._d["returns_per_quarter"]
        if returns_this_quarter >= cap:
            return deny("cap", f"Thanks for writing. Under our cap rule, we accept {cap} returns per "
                               f"quarter, and this account has reached that limit.")
        if not request.has_statement and request.reason_code and a.clerk_rule is None:
            a.mapped_party = self.reason_code_party(request.reason_code)
        return a

    def responsibility_row(self, category: str, party: ResponsibleParty) -> ResponsibilityRow:
        cat = self._d["categories"].get(category) or {}
        row = (cat.get("responsibility") or {}).get(ResponsibleParty(party).value)
        if not row:
            return _EMPTY_ROW
        return ResponsibilityRow(frozenset(Disposition(d) for d in row.get("dispositions", [])),
                                 float(row.get("fee", 0.0)), row.get("return_postage", "none"))

    def auto_approval(self) -> AutoApproval:
        a = self._d["auto_approval"]
        return AutoApproval(bool(a["require_agreement"]), float(a["min_probability"]),
                            float(a["max_amount"]), frozenset(a["block_on_flags"]))

    def reason_code_party(self, reason_code: str) -> ResponsibleParty | None:
        p = self._d["reason_codes"].get(reason_code)
        return ResponsibleParty(p) if p else None
