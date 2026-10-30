"""The desk flow: policy first, engines only for a statement, the clerk for what is left.

Every resolution, whatever the resolver, goes through the validator before it is logged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import resolution as validator
from .catalog import load_catalog
from .log import ResolutionLog
from .orders import load_orders, load_requests
from .policy import JsonPolicy
from .types import (
    MONEY_DISPOSITIONS, Assessment, Disposition, Engines, Item, Order, OrderLine, Policy,
    Reading, Request, Resolution, Resolver, ResponsibilityRow, ResponsibleParty, Scores,
)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
# The sample set is dated October 2026, and the per-quarter count is taken over today's quarter,
# so the demo keeps this date rather than the real one.
DEMO_TODAY = date(2026, 10, 30)


def desk_today() -> date:
    """DEMO_TODAY unless RETURNS_DESK_TODAY (ISO date) is set."""
    v = os.environ.get("RETURNS_DESK_TODAY")
    return date.fromisoformat(v) if v else DEMO_TODAY


@dataclass
class EngineInput:
    """What an engine is told beyond the request (types.EngineContext)."""
    item: Item
    order_line: OrderLine
    delivered: date | None
    photo_path: Path | None


@dataclass
class Pending:
    """A request waiting for the clerk, with everything the clerk sees."""
    assessment: Assessment
    reading: Reading | None
    scores: Scores | None
    agreement: bool
    allowed: frozenset[Disposition]                       # money dispositions for the generative engine's party
    allowed_by_party: dict[ResponsibleParty, frozenset[Disposition]] = field(default_factory=dict)

    @property
    def request(self) -> Request:
        return self.assessment.request


class Desk:
    def __init__(self, policy: Policy, engines: Engines, catalog: dict[str, Item],
                 orders: dict[str, Order], requests: list[Request], log: ResolutionLog,
                 today: date | None = None, data_dir: Path = DATA_DIR):
        self.policy = policy
        self.engines = engines
        self.catalog = catalog
        self.orders = orders
        self.requests = list(requests)
        self.log = log
        self._today = today
        self.data_dir = Path(data_dir)
        self._pending: dict[str, Pending] = {}

    @property
    def today(self) -> date:
        return self._today or desk_today()

    # -------------------------------------------------------------------- the queue

    def queue(self) -> list[Request]:
        resolved = self.log.by_request()
        open_ = [r for r in self.requests if r.request_id not in resolved]
        return sorted(open_, key=lambda r: not r.demo)      # stable: sample order within each group

    def request(self, request_id: str) -> Request:
        for r in self.requests:
            if r.request_id == request_id:
                return r
        raise KeyError(request_id)

    # -------------------------------------------------------------------- policy

    def assess(self, request: Request) -> Assessment:
        order = self.orders.get(request.order_id)
        item = self.catalog.get(request.sku)
        count = 0
        if order is not None:
            count = self.log.returns_this_quarter(order.customer_id, self.today, self.orders,
                                                  self.requests, before=request)
        return self.policy.assess(request, order, item, count, self.today)

    # -------------------------------------------------------------------- the flow

    def process(self, request: Request) -> Resolution | Pending:
        a = self.assess(request)

        if not a.passes:
            party = (self.policy.reason_code_party(request.reason_code) if request.reason_code else None)
            reply = a.denial_reply or a.denial_rule or "denied by policy"
            return self._commit(a, Resolution(
                request.request_id, Resolver.POLICY, party or ResponsibleParty.UNCLEAR, Disposition.DENY,
                0.0, 0.0, "none", note=reply, reply_draft=reply))

        if not request.has_statement:
            if a.mapped_party is not None:
                row = self._row(a, a.mapped_party)
                disposition = self._policy_disposition(row, a.item)
                if disposition is not None:
                    amount, fee = validator.compute(row, a.line, disposition)
                    return self._commit(a, Resolution(
                        request.request_id, Resolver.POLICY, a.mapped_party, disposition, amount, fee,
                        row.return_postage, note=f"reason code {request.reason_code}"))
            # nothing to read and nothing policy can map: straight to the clerk
            return self._hold(a, None, None, False)

        ctx = EngineInput(a.item, a.line, a.order.delivered,
                          (self.data_dir / request.photo) if request.photo else None)
        reading = self.engines.reading.read(request, ctx)
        scores = self.engines.scoring.score(request, ctx)
        agreement = reading.responsible_party == scores.top
        party = reading.responsible_party
        row = self._row(a, party)
        rule = self.policy.auto_approval()
        amount, fee = validator.compute(row, a.line, Disposition.REFUND)
        if (a.clerk_rule is None
                and (agreement or not rule.require_agreement)
                and scores.probabilities.get(party, 0.0) >= rule.min_probability
                and amount <= rule.max_amount
                and not (set(reading.flags) & rule.block_on_flags)
                and Disposition.REFUND in row.dispositions
                and self._under_cap(a)):
            return self._commit(a, Resolution(
                request.request_id, Resolver.ENGINES, party, Disposition.REFUND, amount, fee,
                row.return_postage, note=reading.reason, reply_draft=reading.reply_draft,
                reading=reading, scores=scores))
        return self._hold(a, reading, scores, agreement)

    def resolve_as_clerk(self, request_id: str, responsible_party: ResponsibleParty,
                         disposition: Disposition, note: str,
                         partial_amount: float | None = None) -> Resolution:
        party = ResponsibleParty(responsible_party)
        disposition = Disposition(disposition)
        pending = self._pending.get(request_id)
        a = pending.assessment if pending else self.assess(self.request(request_id))
        row = self._row(a, party)
        if disposition in MONEY_DISPOSITIONS:
            amount, fee = validator.compute(row, a.line, disposition, partial_amount)
        else:
            amount, fee = 0.0, 0.0
        reading = pending.reading if pending else None
        r = Resolution(request_id, Resolver.CLERK, party, disposition, amount, fee,
                       row.return_postage if disposition in MONEY_DISPOSITIONS else "none",
                       note=note, reply_draft=reading.reply_draft if reading else "",
                       reading=reading, scores=pending.scores if pending else None)
        self._commit(a, r)
        self._pending.pop(request_id, None)
        return r

    # -------------------------------------------------------------------- helpers

    def _under_cap(self, a: Assessment) -> bool:
        """Auto-approval's cap condition: the customer is still under the per-quarter cap once
        this request counts. Policy lets the request that reaches the cap through, so ask it
        whether one more would be denied by the cap; if so, this one reaches it."""
        after = self.policy.assess(a.request, a.order, a.item, a.returns_this_quarter + 1, self.today)
        return after.denial_rule != "cap"

    def _row(self, a: Assessment, party: ResponsibleParty) -> ResponsibilityRow:
        if a.item is None:
            return ResponsibilityRow(frozenset())
        return self.policy.responsibility_row(a.item.category, party)

    @staticmethod
    def _policy_disposition(row: ResponsibilityRow, item: Item | None) -> Disposition | None:
        if Disposition.REFUND in row.dispositions:
            return Disposition.REFUND
        if Disposition.REPLACE in row.dispositions and item is not None and item.in_stock > 0:
            return Disposition.REPLACE
        return None

    def _hold(self, a: Assessment, reading: Reading | None, scores: Scores | None,
              agreement: bool) -> Pending:
        by_party = {p: self._row(a, p).dispositions for p in ResponsibleParty}
        allowed = by_party[reading.responsible_party] if reading else frozenset()
        p = Pending(a, reading, scores, agreement, allowed, by_party)
        self._pending[a.request.request_id] = p
        return p

    def _commit(self, a: Assessment, r: Resolution) -> Resolution:
        """Validate, then log. A policy denial may have no order line or item; the validator
        then checks it against a zero line, which a deny with a note always passes."""
        line = a.line or OrderLine(a.request.sku, 0, 0.0)
        item = a.item or Item(a.request.sku, "", "", 0)
        validator.validate(r, self._row(a, r.responsible_party), line, item)
        return self.log.append(r)


def load_desk(data_dir: str | Path = DATA_DIR, *, engines: Engines, log: ResolutionLog | None = None,
              today: date | None = None, policy: Policy | None = None) -> Desk:
    """A desk over the data files in `data_dir`."""
    d = Path(data_dir)
    return Desk(policy or JsonPolicy.from_file(d / "policy.json"), engines,
                load_catalog(d / "catalog.json"), load_orders(d / "orders.json"),
                load_requests(d / "sample_set.json"), log or ResolutionLog(), today, d)
