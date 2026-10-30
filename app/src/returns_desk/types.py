"""Contracts shared by every module. The language is CONTEXT.md; the design is docs/SKETCH.md.

This file is the seam between modules built in parallel. Change it only by agreement; every
other module imports from here and nothing here imports from any other module.

Vocabulary rule: names in code follow the glossary. No "fault", "agent", "decision model",
"LLM", "tier", "stand-in", "outcome", "ground truth".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field


# ----------------------------------------------------------------------------- the judgment

class ResponsibleParty(StrEnum):
    """The party that bears the cost of a return. Who pays, not who is to blame."""
    RETAILER = "retailer"
    CARRIER = "carrier"
    CUSTOMER = "customer"
    UNCLEAR = "unclear"


class Disposition(StrEnum):
    """The kind of resolution. Refund, partial and replace come from the responsibility table.
    Deny and escalate are always open to the clerk and need a note. Partial is clerk-only."""
    REFUND = "refund"
    PARTIAL = "partial"
    REPLACE = "replace"
    DENY = "deny"
    ESCALATE = "escalate"


MONEY_DISPOSITIONS = frozenset({Disposition.REFUND, Disposition.PARTIAL, Disposition.REPLACE})
CLERK_EXITS = frozenset({Disposition.DENY, Disposition.ESCALATE})


class Resolver(StrEnum):
    """Who resolved a request."""
    POLICY = "policy"
    ENGINES = "engines"
    CLERK = "clerk"


Flag = Literal["damage_in_transit", "defect", "remorse", "wrong_item", "used", "possible_abuse"]
FLAGS: tuple[str, ...] = ("damage_in_transit", "defect", "remorse", "wrong_item", "used", "possible_abuse")


# ----------------------------------------------------------------------------- the request

@dataclass(frozen=True)
class OrderLine:
    sku: str
    qty: int
    paid: float          # total paid for the line, USD


@dataclass(frozen=True)
class Order:
    order_id: str
    customer_id: str
    placed: date
    delivered: date | None     # the delivery date: the carrier's reported date, trusted by policy
    lines: tuple[OrderLine, ...]

    def line(self, sku: str) -> OrderLine | None:
        return next((l for l in self.lines if l.sku == sku), None)


@dataclass(frozen=True)
class Item:
    sku: str
    name: str
    category: str
    in_stock: int


@dataclass(frozen=True)
class Label:
    """What makes a request a labeled request."""
    responsible_party: ResponsibleParty
    source: Literal["author", "clerk"]


@dataclass(frozen=True)
class Request:
    """A return request. `reason_code` is from the retailer's list or None. `statement` is the
    customer's own words, possibly empty; `photo` is a path relative to app/data, or None.
    A request has a statement, in the glossary sense, when statement is non-empty or photo
    is set; only then are the engines asked."""
    request_id: str
    order_id: str
    sku: str
    submitted: date
    reason_code: str | None = None
    statement: str = ""
    photo: str | None = None
    label: Label | None = None
    demo: bool = False

    @property
    def has_statement(self) -> bool:
        return bool(self.statement.strip()) or self.photo is not None


# ----------------------------------------------------------------------------- the engines

class Reading(BaseModel):
    """What the reading engine returns. Structured output; the schema is enforced by the API
    and re-validated here."""
    responsible_party: ResponsibleParty = Field(description="Who bears the cost of this return.")
    reason: str = Field(description="One sentence the clerk reads.")
    reply_draft: str = Field(description="At most three sentences to the customer, in the retailer's voice.")
    flags: list[Flag] = Field(default_factory=list)


@dataclass
class Scores:
    """What the scoring engine returns. One probability per responsible party, summing to ~1."""
    probabilities: dict[ResponsibleParty, float]
    confidence: float

    @property
    def top(self) -> ResponsibleParty:
        return max(self.probabilities, key=self.probabilities.__getitem__)


@dataclass
class Usage:
    """Token accounting for one engine."""
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    def add(self, input_tokens: int, output_tokens: int, cached: int = 0) -> None:
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cached_input_tokens += cached


class EngineContext(Protocol):
    """What an engine is told about the request beyond the request itself."""
    item: Item
    order_line: OrderLine
    delivered: date | None
    photo_path: Path | None      # absolute path, resolved by the caller, or None


class ReadingEngine(Protocol):
    name: str
    auth: str                    # "entra" or "api-key", for the board badge
    usage: Usage

    def read(self, request: Request, context: EngineContext) -> Reading: ...


class ScoringEngine(Protocol):
    name: str
    auth: str
    usage: Usage

    def score(self, request: Request, context: EngineContext) -> Scores: ...


@dataclass
class Engines:
    reading: ReadingEngine
    scoring: ScoringEngine


# ----------------------------------------------------------------------------- the policy

@dataclass(frozen=True)
class ResponsibilityRow:
    """One cell of the responsibility table: category x responsible party."""
    dispositions: frozenset[Disposition]      # money dispositions only
    fee: float = 0.0                          # fraction of paid, 0.10 == ten percent
    return_postage: Literal["retailer", "customer", "none"] = "none"


@dataclass(frozen=True)
class AutoApproval:
    require_agreement: bool
    min_probability: float
    max_amount: float
    block_on_flags: frozenset[str]


@dataclass
class Assessment:
    """What policy says about a request before any engine is asked."""
    request: Request
    order: Order | None
    item: Item | None
    line: OrderLine | None
    passes: bool
    denial_rule: str | None = None            # e.g. "window", "category", "photo_required", "cap", "order"
    denial_reply: str | None = None           # templated reply citing the rule
    mapped_party: ResponsibleParty | None = None   # set when a reason code resolved it with no statement
    returns_this_quarter: int = 0
    clerk_rule: str | None = None             # set when a rule sends it to the clerk, e.g. "no_delivery_date"


class Policy(Protocol):
    """The retailer's rules. JsonPolicy reads app/data/policy.json. Open Policy Agent could sit here."""

    def assess(self, request: Request, order: Order | None, item: Item | None,
               returns_this_quarter: int, today: date) -> Assessment: ...

    def responsibility_row(self, category: str, party: ResponsibleParty) -> ResponsibilityRow: ...

    def auto_approval(self) -> AutoApproval: ...

    def reason_code_party(self, reason_code: str) -> ResponsibleParty | None: ...


# ----------------------------------------------------------------------------- the result

@dataclass
class Resolution:
    """The full result of a request. Written to the resolution log."""
    request_id: str
    resolver: Resolver
    responsible_party: ResponsibleParty
    disposition: Disposition
    amount: float                              # refund in USD; 0 for replace, deny, escalate
    fee: float                                 # USD withheld
    return_postage: Literal["retailer", "customer", "none"]
    note: str                                  # clerk note, denial rule, or engine reason
    reply_draft: str = ""
    reading: Reading | None = None
    scores: Scores | None = None
    resolved_at: str = ""                      # ISO timestamp, set by the log writer


@dataclass
class ValidationError(Exception):
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.detail}"


# ----------------------------------------------------------------------------- the evaluation

@dataclass
class EngineReport:
    engine: str
    requests: int
    correct: int
    grid: dict[str, dict[str, int]]           # known party -> named party -> count
    input_tokens: int
    output_tokens: int
    cost_usd: float

    @property
    def accuracy(self) -> float:
        return self.correct / self.requests if self.requests else 0.0

    @property
    def cost_per_request_usd(self) -> float:
        return self.cost_usd / self.requests if self.requests else 0.0


@dataclass
class EvaluationResult:
    run_at: str
    model: str
    labeled_requests: int
    from_sample_set: int
    from_resolution_log: int
    agreement_rate: float
    reports: list[EngineReport] = field(default_factory=list)
