"""Evaluation: both engines over labeled requests. Canned engines only; nothing calls Azure."""
from __future__ import annotations

import pytest

from conftest import CANNED_READINGS, CANNED_SCORES, CannedGenerativeEngine, CannedProbabilisticEngine, DATA
from returns_desk import evaluation
from returns_desk.log import ResolutionLog
from returns_desk.orders import load_requests
from returns_desk.types import (
    Disposition as D, EngineReport, EvaluationResult, Label, Request, Resolution, Resolver,
    ResponsibleParty as P, Scores,
)

RUN_AT = "2026-10-30T09:00:00+00:00"


def _sample() -> list[Request]:
    return load_requests(DATA / "sample_set.json")


def _demo_statements() -> list[Request]:
    return [r for r in _sample() if r.demo and r.has_statement]


def _clerk(request_id: str, party: P, disposition: D = D.REFUND, resolver: Resolver = Resolver.CLERK) -> Resolution:
    return Resolution(request_id, resolver, party, disposition, 0.0, 0.0, "none", note="n")


class TokenReading(CannedGenerativeEngine):
    name = "gpt-6-luna"

    def read(self, request, context):
        out = super().read(request, context)
        self.usage.add(1000, 200)
        return out


class TokenScoring(CannedProbabilisticEngine):
    name = "gpt-6-luna (decisions)"

    def score(self, request, context):
        out = super().score(request, context)
        self.usage.add(1000, 50)
        return out


class BrokenScoring(CannedProbabilisticEngine):
    def score(self, request, context):
        raise RuntimeError("endpoint unreachable")


def _labeled(log=None, tmp_path=None):
    return evaluation.labeled_requests(_sample(), log or ResolutionLog(tmp_path / "l.jsonl"))


# ------------------------------------------------------------------ labeled requests

def test_labeled_requests_takes_sample_requests_with_a_statement_and_an_author_label(tmp_path):
    labeled = _labeled(tmp_path=tmp_path)
    ids = [r.request_id for r, _ in labeled]
    expected = [r.request_id for r in _sample() if r.has_statement and r.label]
    assert ids == expected
    assert "RQ-302" not in ids and "RQ-304" not in ids        # reason code only, nothing to read
    assert all(label.source == "author" for _, label in labeled)
    assert dict((r.request_id, l.responsible_party) for r, l in labeled)["RQ-301"] == P.CARRIER


def test_requests_without_a_label_are_left_out(tmp_path):
    unlabeled = Request("RQ-X", "R-2041", "KET-200", _sample()[0].submitted, statement="broken")
    out = evaluation.labeled_requests([unlabeled], ResolutionLog(tmp_path / "l.jsonl"))
    assert out == []


def test_clerk_label_in_the_log_overrides_the_author_label(tmp_path):
    log = ResolutionLog(tmp_path / "l.jsonl")
    log.append(_clerk("RQ-301", P.RETAILER))
    labeled = dict((r.request_id, l) for r, l in _labeled(log))
    assert labeled["RQ-301"] == Label(P.RETAILER, "clerk")
    assert labeled["RQ-307"].source == "author"
    assert len(_labeled(log)) == len(_labeled(tmp_path=tmp_path))      # deduped by request_id


def test_only_clerk_resolutions_in_the_log_are_labels(tmp_path):
    log = ResolutionLog(tmp_path / "l.jsonl")
    log.append(_clerk("RQ-301", P.RETAILER, resolver=Resolver.ENGINES))
    log.append(_clerk("RQ-303", P.CUSTOMER, D.DENY, resolver=Resolver.POLICY))
    labeled = dict((r.request_id, l) for r, l in _labeled(log))
    assert labeled["RQ-301"].source == "author" and labeled["RQ-303"].source == "author"


def test_clerk_escalation_is_a_label_and_the_latest_clerk_resolution_wins(tmp_path):
    log = ResolutionLog(tmp_path / "l.jsonl")
    log.append(_clerk("RQ-306", P.RETAILER))
    log.append(_clerk("RQ-306", P.UNCLEAR, D.ESCALATE))
    labeled = dict((r.request_id, l) for r, l in _labeled(log))
    assert labeled["RQ-306"] == Label(P.UNCLEAR, "clerk")


def test_log_resolution_for_a_request_not_in_the_sample_set_is_ignored(tmp_path):
    log = ResolutionLog(tmp_path / "l.jsonl")
    log.append(_clerk("RQ-999", P.RETAILER))
    assert [r.request_id for r, _ in _labeled(log)] == [r.request_id for r, _ in _labeled(tmp_path=tmp_path)]


# ------------------------------------------------------------------ run

def _engines(scoring_overrides=None, reading=None, scoring=None):
    answers = dict(CANNED_SCORES)
    answers.update(scoring_overrides or {})
    return (reading or CannedGenerativeEngine(), scoring or CannedProbabilisticEngine(answers))


def _run(reading, scoring, labeled):
    from returns_desk.types import Engines
    return evaluation.run(Engines(reading, scoring), labeled, lambda r: None, "gpt-6-luna", RUN_AT)


def _demo_labeled():
    return [(r, r.label) for r in _demo_statements()]


def test_run_over_the_demo_requests_gives_the_grid_and_accuracy():
    # the engines are right on all five demo requests that carry a statement, except that the
    # probabilistic engine is told to put RQ-305 (known: retailer) on customer
    reading, scoring = _engines({"RQ-305": Scores({P.RETAILER: 0.30, P.CARRIER: 0.05, P.CUSTOMER: 0.60, P.UNCLEAR: 0.05}, 0.6)})
    result = _run(reading, scoring, _demo_labeled())

    assert isinstance(result, EvaluationResult)
    assert result.run_at == RUN_AT and result.model == "gpt-6-luna"
    assert result.labeled_requests == 5
    assert result.from_sample_set == 5 and result.from_resolution_log == 0
    r_reading, r_scoring = result.reports
    assert isinstance(r_reading, EngineReport)
    assert (r_reading.engine, r_scoring.engine) == (reading.name, scoring.name)

    assert (r_reading.requests, r_reading.correct, r_reading.accuracy) == (5, 5, 1.0)
    assert r_reading.grid["retailer"] == {"retailer": 2, "carrier": 0, "customer": 0, "unclear": 0}
    assert r_reading.grid["carrier"]["carrier"] == 2
    assert r_reading.grid["unclear"]["unclear"] == 1
    assert r_reading.grid["customer"] == {"retailer": 0, "carrier": 0, "customer": 0, "unclear": 0}

    assert (r_scoring.requests, r_scoring.correct, r_scoring.accuracy) == (5, 4, 0.8)
    assert r_scoring.grid["retailer"] == {"retailer": 1, "carrier": 0, "customer": 1, "unclear": 0}
    assert list(r_scoring.grid) == ["retailer", "carrier", "customer", "unclear"]
    assert all(list(row) == ["retailer", "carrier", "customer", "unclear"] for row in r_scoring.grid.values())
    assert result.agreement_rate == pytest.approx(0.8)


def test_run_calls_each_engine_once_per_labeled_request():
    reading, scoring = _engines()
    _run(reading, scoring, _demo_labeled())
    ids = [r.request_id for r in _demo_statements()]
    assert reading.asked == ids and scoring.asked == ids


def test_run_counts_labels_by_source():
    labeled = _demo_labeled()
    i = next(i for i, (r, _) in enumerate(labeled) if r.request_id == "RQ-301")
    labeled[i] = (labeled[i][0], Label(P.RETAILER, "clerk"))
    result = _run(*_engines(), labeled)
    assert (result.from_sample_set, result.from_resolution_log) == (4, 1)
    assert result.reports[0].grid["retailer"]["carrier"] == 1       # clerk said retailer, reading named carrier


def test_run_with_no_requests_reports_zeroes():
    result = _run(*_engines(), [])
    assert result.labeled_requests == 0 and result.agreement_rate == 0.0
    assert [rep.requests for rep in result.reports] == [0, 0]


def test_cost_comes_from_the_price_table_on_the_usage_delta():
    reading, scoring = TokenReading(), TokenScoring()
    reading.usage.add(5_000_000, 5_000_000)                 # earlier spend must not be billed to this run
    result = _run(reading, scoring, _demo_labeled())
    rr, rs = result.reports
    assert (rr.input_tokens, rr.output_tokens) == (5000, 1000)
    assert rr.cost_usd == pytest.approx((5000 * 0.10 + 1000 * 0.50) / 1e6)
    assert rr.cost_per_request_usd == pytest.approx(rr.cost_usd / 5)
    assert (rs.input_tokens, rs.output_tokens) == (5000, 250)
    assert rs.cost_usd == pytest.approx(5000 * 0.10 / 1e6)          # decisions bill input only


def test_an_engine_error_aborts_the_run():
    with pytest.raises(RuntimeError, match="unreachable"):
        _run(CannedGenerativeEngine(), BrokenScoring(), _demo_labeled())


# ------------------------------------------------------------------ save and load

def test_save_then_load_round_trips(tmp_path):
    result = _run(TokenReading(), TokenScoring(), _demo_labeled())
    path = tmp_path / "out" / "evaluation.json"
    evaluation.save(result, path)
    assert evaluation.load(path) == result


def test_to_dict_carries_what_the_board_renders():
    result = _run(TokenReading(), TokenScoring(), _demo_labeled())
    d = evaluation.to_dict(result)
    assert d["labeled_requests"] == 5 and d["agreement_rate"] == 1.0
    rep = d["reports"][0]
    assert rep["engine"] == "gpt-6-luna" and rep["accuracy"] == 1.0
    assert rep["cost_per_request_usd"] == pytest.approx(result.reports[0].cost_per_request_usd)
    assert rep["grid"]["carrier"]["carrier"] == 2
