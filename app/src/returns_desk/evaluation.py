"""Evaluation: both engines over labeled requests, from the sample set and the resolution log.

Run on demand from `returns-desk evaluate`, saved to data/evaluation.json; the board reads the
file and never runs an evaluation. An engine error aborts the run: a half-measured engine is
worse than no number.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, replace
from pathlib import Path

from .desk import EngineInput
from .log import ResolutionLog
from .model import estimate_cost_usd
from .types import (
    Engines, EngineContext, EngineReport, EvaluationResult, Item, Label, Order, Request,
    Resolver, ResponsibleParty, Usage,
)

PARTIES = [p.value for p in ResponsibleParty]


def labeled_requests(sample_set: Sequence[Request], log: ResolutionLog) -> list[tuple[Request, Label]]:
    """Every sample request with a statement and a label, plus every clerk resolution in the
    log as a clerk-sourced label. One entry per request id, the clerk label winning, in sample
    set order. A clerk resolution for a request the sample set does not hold, or one with no
    statement for the engines to read, cannot be evaluated and is left out."""
    clerk: dict[str, Label] = {}
    for r in log.all():                                  # later resolutions replace earlier ones
        if r.resolver == Resolver.CLERK:
            clerk[r.request_id] = Label(r.responsible_party, "clerk")
    out: list[tuple[Request, Label]] = []
    seen: set[str] = set()
    for request in sample_set:
        if request.request_id in seen or not request.has_statement:
            continue
        label = clerk.get(request.request_id) or request.label
        if label is None:
            continue
        seen.add(request.request_id)
        out.append((request, label))
    return out


def context_builder(orders: dict[str, Order], catalog: dict[str, Item],
                    data_dir: str | Path) -> Callable[[Request], EngineContext]:
    """What the engines are told about a request, as the desk tells them."""
    base = Path(data_dir)

    def build(request: Request) -> EngineContext:
        order = orders[request.order_id]
        return EngineInput(catalog[request.sku], order.line(request.sku), order.delivered,
                           (base / request.photo) if request.photo else None)
    return build


def _empty_grid() -> dict[str, dict[str, int]]:
    return {known: {named: 0 for named in PARTIES} for known in PARTIES}


def _snapshot(u: Usage) -> Usage:
    return replace(u)


def _report(engine, before: Usage, grid, requests: int, correct: int) -> EngineReport:
    delta = Usage(engine.usage.calls - before.calls,
                  engine.usage.input_tokens - before.input_tokens,
                  engine.usage.output_tokens - before.output_tokens,
                  engine.usage.cached_input_tokens - before.cached_input_tokens)
    cost = estimate_cost_usd(engine.name, delta)
    return EngineReport(engine.name, requests, correct, grid, delta.input_tokens,
                        delta.output_tokens, cost if cost is not None else 0.0)


def run(engines: Engines, labeled: Sequence[tuple[Request, Label]],
        context_builder: Callable[[Request], EngineContext], model_name: str,
        run_at: str) -> EvaluationResult:
    """Ask both engines once per labeled request. Tokens and cost are each engine's usage
    delta over the run."""
    reading_before, scoring_before = _snapshot(engines.reading.usage), _snapshot(engines.scoring.usage)
    grids = _empty_grid(), _empty_grid()
    correct = [0, 0]
    agreed = 0
    for request, label in labeled:
        context = context_builder(request)
        reading = engines.reading.read(request, context)
        scores = engines.scoring.score(request, context)
        known = label.responsible_party.value
        for i, named in enumerate((reading.responsible_party, scores.top)):
            grids[i][known][named.value] += 1
            correct[i] += named == label.responsible_party
        agreed += reading.responsible_party == scores.top
    n = len(labeled)
    from_log = sum(1 for _, label in labeled if label.source == "clerk")
    return EvaluationResult(
        run_at=run_at, model=model_name, labeled_requests=n,
        from_sample_set=n - from_log, from_resolution_log=from_log,
        agreement_rate=agreed / n if n else 0.0,
        reports=[_report(engines.reading, reading_before, grids[0], n, correct[0]),
                 _report(engines.scoring, scoring_before, grids[1], n, correct[1])])


# ----------------------------------------------------------------------------- save and load

def to_dict(result: EvaluationResult) -> dict:
    """What the board renders: the result plus each report's accuracy and cost per request."""
    d = asdict(result)
    for rep, src in zip(d["reports"], result.reports):
        rep["accuracy"] = src.accuracy
        rep["cost_per_request_usd"] = src.cost_per_request_usd
    return d


def from_dict(d: dict) -> EvaluationResult:
    fields = ("engine", "requests", "correct", "grid", "input_tokens", "output_tokens", "cost_usd")
    return EvaluationResult(
        run_at=d["run_at"], model=d["model"], labeled_requests=d["labeled_requests"],
        from_sample_set=d["from_sample_set"], from_resolution_log=d["from_resolution_log"],
        agreement_rate=d["agreement_rate"],
        reports=[EngineReport(**{k: r[k] for k in fields}) for r in d["reports"]])


def save(result: EvaluationResult, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(to_dict(result), indent=2) + "\n", encoding="utf-8")


def load(path: str | Path) -> EvaluationResult:
    return from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
