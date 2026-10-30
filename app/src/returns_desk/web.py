"""The returns board: a small web UI over the same Desk the command line uses.

One in-memory session per server: the Desk, the request the clerk has open, and the engines'
usage. Every resolution still goes through Desk.process or Desk.resolve_as_clerk, so the
browser cannot make one the validator would refuse.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .desk import Desk, Pending
from .log import to_dict
from .model import estimate_cost_usd
from .policy import JsonPolicy
from .types import Disposition, Request, Resolution, ResponsibleParty, ValidationError

STATIC = Path(__file__).resolve().parent / "static"
log = logging.getLogger("returns_desk.board")


class ResolveBody(BaseModel):
    request_id: str
    responsible_party: ResponsibleParty
    disposition: Disposition
    note: str = Field("", max_length=2000)
    partial_amount: float | None = None


class NextBody(BaseModel):
    request_id: str | None = None            # a named request from the queue; None pulls the head


class Session:
    def __init__(self, desk: Desk, evaluation_path: Path, policy_path: Path, photos_dir: Path) -> None:
        self.desk = desk
        self.evaluation_path, self.policy_path, self.photos_dir = evaluation_path, policy_path, photos_dir
        self.current: Pending | None = None

    # ---- reset: demo only ----------------------------------------------
    def reset(self) -> None:
        """Empty the resolution log and the open request; the queue is whatever the log has not
        resolved, so it fills again. Reload policy.json so an edited file takes effect."""
        d = self.desk
        d.log.reset()
        d._pending.clear()
        self.current = None
        for e in (d.engines.reading, d.engines.scoring):
            e.usage.__init__()
        if isinstance(d.policy, JsonPolicy) and self.policy_path.exists():
            d.policy = JsonPolicy.from_file(self.policy_path)

    # ---- serialization -------------------------------------------------
    @staticmethod
    def engine(e) -> dict:
        u = e.usage
        return {"name": e.name, "auth": getattr(e, "auth", "unknown"), "calls": u.calls,
                "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                "cost_usd": estimate_cost_usd(e.name, u)}

    def request_view(self, r: Request) -> dict:
        d = self.desk
        order, item = d.orders.get(r.order_id), d.catalog.get(r.sku)
        line = order.line(r.sku) if order else None
        return {"request_id": r.request_id, "order_id": r.order_id, "sku": r.sku,
                "item": item.name if item else r.sku, "category": item.category if item else None,
                "paid": line.paid if line else None,
                "delivered": order.delivered.isoformat() if order and order.delivered else None,
                "submitted": r.submitted.isoformat(), "reason_code": r.reason_code,
                "statement": r.statement, "demo": r.demo,
                "photo_url": f"/{r.photo}" if r.photo else None}

    def resolution_view(self, r: Resolution) -> dict:
        return to_dict(r) | {"request": self.request_view(self.desk.request(r.request_id))}

    def pending_view(self, p: Pending) -> dict:
        d = self.desk
        a = p.assessment
        rows = {}
        for party in ResponsibleParty:
            row = d.policy.responsibility_row(a.item.category, party) if a.item else None
            rows[party.value] = {"dispositions": sorted(x.value for x in row.dispositions) if row else [],
                                 "fee": row.fee if row else 0.0,
                                 "return_postage": row.return_postage if row else "none"}
        return {
            "request": self.request_view(p.request),
            "reading": p.reading.model_dump(mode="json") if p.reading else None,
            "scores": ({"probabilities": {k.value: v for k, v in p.scores.probabilities.items()},
                        "confidence": p.scores.confidence, "top": p.scores.top.value}
                       if p.scores else None),
            "agreement": p.agreement,
            "allowed": sorted(x.value for x in p.allowed),
            "allowed_by_party": {k: v["dispositions"] for k, v in rows.items()},
            "responsibility": rows,
            "returns_this_quarter": a.returns_this_quarter,
            "clerk_rule": a.clerk_rule,
        }

    def policy_contents(self) -> dict | None:
        try:
            return json.loads(self.policy_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def state(self) -> dict:
        d = self.desk
        done = d.log.all()
        counts = {r: sum(1 for x in done if x.resolver.value == r) for r in ("policy", "engines", "clerk")}
        queue = d.queue()
        return {
            "engines": {"reading": self.engine(d.engines.reading), "scoring": self.engine(d.engines.scoring)},
            "counts": counts,
            "policy": self.policy_contents(),
            "today": d.today.isoformat(),
            "queue_length": len(queue),
            "queue": [self.request_view(r) for r in queue],
            "current": self.pending_view(self.current) if self.current else None,
            "log": [self.resolution_view(r) for r in done],
        }

    # ---- actions -------------------------------------------------------
    def next(self, request_id: str | None = None) -> dict:
        """Pull the head of the queue, or the named request when the clerk picks one."""
        if self.current is not None:
            raise HTTPException(409, "a request is waiting for the clerk")
        queue = self.desk.queue()
        if not queue:
            raise HTTPException(404, "no requests left")
        if request_id is None:
            request = queue[0]
        else:
            request = next((r for r in queue if r.request_id == request_id), None)
            if request is None:
                raise HTTPException(404, f"{request_id} is not in the queue")
        try:
            result = self.desk.process(request)
        except ValidationError as e:
            raise HTTPException(500, f"the validator refused the desk's own resolution: {e}") from None
        except Exception:  # engine failure: keep the request in the queue; details stay in the server log
            log.exception("processing %s failed", request.request_id)
            raise HTTPException(502, "the engines could not answer; the server log has the details") from None
        if isinstance(result, Pending):
            self.current = result
            log.info("%s: waiting for the clerk", request.request_id)
            return {"pending": self.pending_view(result)}
        log.info("%s: %s by %s", request.request_id, result.disposition.value, result.resolver.value)
        return {"resolution": self.resolution_view(result)}

    def resolve(self, body: ResolveBody) -> JSONResponse:
        if self.current is None or self.current.request.request_id != body.request_id:
            raise HTTPException(409, "that request is not waiting for the clerk")
        try:
            r = self.desk.resolve_as_clerk(body.request_id, body.responsible_party, body.disposition,
                                           body.note, body.partial_amount)
        except ValidationError as e:
            return JSONResponse({"detail": str(e), "rule": e.rule}, status_code=400)
        self.current = None
        log.info("%s: %s by the clerk", body.request_id, r.disposition.value)
        return JSONResponse(self.resolution_view(r))


def build_app(desk: Desk, *, evaluation_path: Path | str | None = None,
              policy_path: Path | str | None = None, photos_dir: Path | str | None = None) -> FastAPI:
    data = Path(desk.data_dir)
    session = Session(desk, Path(evaluation_path or data / "evaluation.json"),
                      Path(policy_path or data / "policy.json"), Path(photos_dir or data / "photos"))
    app = FastAPI(title="Returns Desk board")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"ok": True, "reading": desk.engines.reading.name,
                             "scoring": desk.engines.scoring.name})

    @app.get("/api/state")
    def state() -> JSONResponse:
        return JSONResponse(session.state())

    @app.post("/api/next", dependencies=[Depends(board_header)])
    def next_request(body: NextBody | None = None) -> JSONResponse:
        return JSONResponse(session.next(body.request_id if body else None))

    @app.post("/api/resolve", dependencies=[Depends(board_header)])
    def resolve(body: ResolveBody) -> JSONResponse:
        return session.resolve(body)

    @app.post("/api/reset", dependencies=[Depends(board_header)])
    def reset() -> JSONResponse:
        """Demo only: empties the resolution log and the open request."""
        session.reset()
        return JSONResponse({"ok": True})

    @app.get("/api/evaluation")
    def evaluation() -> JSONResponse:
        try:
            return JSONResponse(json.loads(session.evaluation_path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            raise HTTPException(404, "no saved evaluation") from None

    @app.get("/photos/{name:path}")
    def photo(name: str) -> FileResponse:
        root = session.photos_dir.resolve()
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, "no such photo")
        return FileResponse(path)

    return app


def board_header(x_requested_with: str | None = Header(None)) -> None:
    """The board's own page sends this header; a cross-site form post cannot, so a signed-in
    clerk's cookie cannot be used from another site to pull requests or reset the demo."""
    if x_requested_with != "returns-desk":
        raise HTTPException(403, "missing the board's X-Requested-With header")
