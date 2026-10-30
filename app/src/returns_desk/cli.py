"""`returns-desk run` walks the queue at the terminal; `serve` opens the returns board;
`evaluate` measures both engines; `reset` empties the resolution log."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from . import evaluation
from .desk import DATA_DIR, Desk, Pending, load_desk
from .catalog import load_catalog
from .log import DEFAULT_PATH, ResolutionLog
from .model import estimate_cost_usd
from .orders import load_orders, load_requests
from .types import (
    CLERK_EXITS, Disposition, Engines, Resolution, Resolver, ResponsibleParty, ValidationError,
)

PARTIES = [p.value for p in ResponsibleParty]


def setup_logging(verbose: bool, serving: bool = False) -> None:
    # The board logs each request it moves at INFO; the terminal commands stay quiet.
    level = logging.DEBUG if verbose else logging.INFO if serving else logging.WARNING
    logging.basicConfig(level=level, stream=sys.stderr,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")


def make_engines() -> Engines:
    """Both engines on Foundry, from RETURNS_DESK_* env vars. No offline engine."""
    from .model import from_env
    try:
        return from_env()
    except KeyError as e:
        print(f"missing env var {e}; set it from `tofu output app_env`", file=sys.stderr)
        raise SystemExit(2)


# ----------------------------------------------------------------------------- the walk-through

def _usd(x: float) -> str:
    return f"${x:,.2f}"


def print_resolution(r: Resolution) -> None:
    if r.resolver == Resolver.POLICY and r.disposition == Disposition.DENY:
        print(f"[DENY] {r.request_id:<7} policy: {r.note}")
    elif r.resolver == Resolver.ENGINES:
        p = r.scores.probabilities.get(r.responsible_party, 0.0) if r.scores else 0.0
        print(f"[AUTO] {r.request_id:<7} auto-approval: {r.disposition.value} {_usd(r.amount)}, "
              f"{r.responsible_party.value} (engines agree, p={p:.2f})")
    else:
        print(f"[POL ] {r.request_id:<7} policy: {r.disposition.value} {_usd(r.amount)}, "
              f"{r.responsible_party.value} ({r.note})")


def print_cards(pending: Pending) -> None:
    req, a = pending.request, pending.assessment
    item = a.item.name if a.item else req.sku
    paid = f", paid {_usd(a.line.paid)}" if a.line else ""
    print(f"\n--- {req.request_id} needs the clerk: {item}{paid}")
    if req.reason_code:
        print(f"    reason code: {req.reason_code}")
    if req.statement:
        print(f"    statement:   {req.statement}")
    if req.photo:
        print(f"    photo:       {req.photo}")
    if a.clerk_rule == "no_delivery_date":
        print("    no delivery date held: policy sends this to the clerk")
    if a.returns_this_quarter:
        print(f"    returns this quarter before this one: {a.returns_this_quarter}")
    rd, sc = pending.reading, pending.scores
    if rd is None or sc is None:
        print("    no engine answer: nothing to read")
        return
    print(f"  generative engine:  {rd.responsible_party.value}")
    print(f"    reason: {rd.reason}")
    print(f"    flags:  {', '.join(rd.flags) or 'none'}")
    print(f"    draft:  {rd.reply_draft}")
    ranked = sorted(sc.probabilities.items(), key=lambda kv: -kv[1])
    print(f"  probabilistic engine:  {sc.top.value}  (confidence {sc.confidence:.2f}; "
          + ", ".join(f"{k.value} {v:.0%}" for k, v in ranked) + ")")
    print(f"  engines {'agree' if pending.agreement else 'DISAGREE'}")


def _ask(prompt: str, valid: list[str], default: str | None = None) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        answer = input(f"  {prompt} ({'/'.join(valid)}){suffix} > ").strip().lower() or (default or "")
        if answer in valid:
            return answer
        print(f"  not one of: {', '.join(valid)}")


def _ask_amount() -> float:
    while True:
        try:
            amount = float(input("  partial amount in USD > ").strip().lstrip("$"))
            if amount > 0:
                return amount
        except ValueError:
            pass
        print("  type a positive number")


def ask_clerk(desk: Desk, pending: Pending) -> Resolution:
    """Prompt until the validator accepts the clerk's resolution."""
    print_cards(pending)
    default = pending.reading.responsible_party.value if pending.reading else None
    while True:
        party = ResponsibleParty(_ask("responsible party", PARTIES, default))
        money = sorted(d.value for d in pending.allowed_by_party.get(party, frozenset()))
        exits = sorted(d.value for d in CLERK_EXITS)
        disposition = Disposition(_ask("disposition", money + exits))
        note = input("  note > ").strip()
        amount = _ask_amount() if disposition == Disposition.PARTIAL else None
        try:
            return desk.resolve_as_clerk(pending.request.request_id, party, disposition, note, amount)
        except ValidationError as e:
            print(f"  rejected: {e}")


def print_usage(engines: Engines) -> None:
    for eng in (engines.reading, engines.scoring):
        u = eng.usage
        cost = estimate_cost_usd(eng.name, u)
        print(f"{eng.name:<28} calls: {u.calls}  in: {u.input_tokens}  out: {u.output_tokens}"
              + (f"  est ${cost:.6f}" if cost is not None else ""))


def _run(args: argparse.Namespace, engines: Engines | None) -> int:
    engines = engines or make_engines()
    desk = load_desk(args.data, engines=engines, log=ResolutionLog(args.log), today=args.today)
    queue = desk.queue()
    print(f"generative engine: {engines.reading.name} ({engines.reading.auth})   "
          f"probabilistic engine: {engines.scoring.name} ({engines.scoring.auth})   "
          f"today: {desk.today}   queue: {len(queue)}\n")
    try:
        for request in queue:
            result = desk.process(request)
            if isinstance(result, Resolution):
                print_resolution(result)
            else:
                r = ask_clerk(desk, result)
                print(f"[CLRK] {r.request_id:<7} clerk: {r.disposition.value} {_usd(r.amount)}, "
                      f"{r.responsible_party.value}")
    except EOFError:
        print("\ninput ended; the rest of the queue is untouched", file=sys.stderr)
        return 1
    print()
    print_usage(engines)
    return 0


# ----------------------------------------------------------------------------- evaluate

def print_evaluation(result) -> None:
    print(f"evaluation of {result.model} at {result.run_at}: {result.labeled_requests} labeled requests "
          f"({result.from_sample_set} sample set, {result.from_resolution_log} resolution log); "
          f"engines agree on {result.agreement_rate:.0%}\n")
    print(f"{'engine':<28}{'correct':>10}{'accuracy':>10}{'in tokens':>12}{'out tokens':>12}{'$/request':>14}")
    for rep in result.reports:
        print(f"{rep.engine:<28}{f'{rep.correct}/{rep.requests}':>10}{rep.accuracy:>10.0%}"
              f"{rep.input_tokens:>12}{rep.output_tokens:>12}{rep.cost_per_request_usd:>14.6f}")
    for rep in result.reports:
        print(f"\n{rep.engine}: known party (rows) against named party (columns)")
        print(f"  {'':<10}" + "".join(f"{p:>10}" for p in PARTIES))
        for known, row in rep.grid.items():
            print(f"  {known:<10}" + "".join(f"{row[p]:>10}" for p in PARTIES))


def _evaluate(args: argparse.Namespace, engines: Engines | None) -> int:
    engines = engines or make_engines()
    data = Path(args.data)
    sample = load_requests(data / "sample_set.json")
    labeled = evaluation.labeled_requests(sample, ResolutionLog(args.log))
    build = evaluation.context_builder(load_orders(data / "orders.json"),
                                       load_catalog(data / "catalog.json"), data)
    print(f"asking {engines.reading.name} and {engines.scoring.name} about {len(labeled)} requests ...")
    result = evaluation.run(engines, labeled, build, engines.reading.name,
                            datetime.now(timezone.utc).isoformat())
    out = data / "evaluation.json"
    evaluation.save(result, out)
    print_evaluation(result)
    print(f"\nwritten to {out}")
    return 0


# ----------------------------------------------------------------------------- serve

def _serve(args: argparse.Namespace, engines: Engines | None) -> int:
    import uvicorn

    from . import web

    engines = engines or make_engines()
    desk = load_desk(args.data, engines=engines, log=ResolutionLog(args.log), today=args.today)
    app = web.build_app(desk)
    port = int(os.environ.get("PORT", args.port))
    print(f"returns board on http://{args.host}:{port}/  (generative {engines.reading.name}, "
          f"probabilistic {engines.scoring.name}, auth {engines.reading.auth}, resolution log {desk.log.path})",
          file=sys.stderr)
    uvicorn.run(app, host=args.host, port=port, log_level="warning", log_config=None)
    return 0


def _reset(args: argparse.Namespace, engines: Engines | None) -> int:
    ResolutionLog(args.log).reset()
    print(f"resolution log emptied: {args.log}")
    return 0


# ----------------------------------------------------------------------------- entry

def main(argv: list[str] | None = None, *, engines: Engines | None = None) -> int:
    """`engines` is for tests; without it the engines come from model.from_env()."""
    p = argparse.ArgumentParser(prog="returns-desk")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--today", type=date.fromisoformat, default=None,
                        help="the desk's date, ISO; default RETURNS_DESK_TODAY or 2026-10-30, "
                             "the sample set's date")
        sp.add_argument("--data", default=str(DATA_DIR), help="directory holding the data files")
        sp.add_argument("--log", default=str(DEFAULT_PATH),
                        help="resolution log (JSON lines); '' means the default path")
        sp.add_argument("-v", "--verbose", action="store_true", help="debug logging, including HTTP")

    for name, func, text in (("run", _run, "walk the queue at the terminal"),
                             ("evaluate", _evaluate, "measure both engines, write data/evaluation.json"),
                             ("reset", _reset, "empty the resolution log")):
        sp = sub.add_parser(name, help=text)
        common(sp)
        sp.set_defaults(func=func)

    serve = sub.add_parser("serve", help="open the returns board web UI")
    common(serve)
    serve.add_argument("--port", type=int, default=8080, help="overridden by the PORT env var when set")
    serve.add_argument("--host", default="127.0.0.1", help="0.0.0.0 inside a container")
    serve.set_defaults(func=_serve)

    args = p.parse_args(argv)
    setup_logging(args.verbose, serving=args.func is _serve)
    return args.func(args, engines)


if __name__ == "__main__":
    raise SystemExit(main())
