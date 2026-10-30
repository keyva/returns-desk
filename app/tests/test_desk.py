"""The desk flow. The seven demo queue rows of the sketch are the acceptance tests."""
from __future__ import annotations

import copy
import json
from datetime import date

import pytest

from conftest import DATA, SAMPLE_TODAY
from returns_desk.types import (
    Disposition as D, Reading, Resolution, Resolver, ResponsibleParty as P, Scores, ValidationError,
)


def _req(desk, request_id):
    return next(r for r in desk.requests if r.request_id == request_id)


def _calls(desk):
    return desk.engines.reading.usage.calls, desk.engines.scoring.usage.calls


# ------------------------------------------------------------------ demo queue, one test per row

def test_rq303_cheese_denied_by_policy_no_engine(desk):
    r = desk.process(_req(desk, "RQ-303"))
    assert isinstance(r, Resolution)
    assert r.resolver == Resolver.POLICY
    assert r.disposition == D.DENY
    assert r.responsible_party == P.UNCLEAR
    assert r.amount == 0.0 and r.fee == 0.0
    assert "category" in r.note and r.note == r.reply_draft
    assert _calls(desk) == (0, 0)
    assert desk.log.by_request()["RQ-303"].disposition == D.DENY


def test_rq304_jacket_denied_by_window_no_engine(desk):
    r = desk.process(_req(desk, "RQ-304"))
    assert r.resolver == Resolver.POLICY
    assert r.disposition == D.DENY
    assert r.responsible_party == P.CUSTOMER          # from the reason code
    assert r.amount == 0.0
    assert "window" in r.note
    assert _calls(desk) == (0, 0)


def test_rq302_tshirt_reason_code_refunded_by_policy(desk):
    r = desk.process(_req(desk, "RQ-302"))
    assert r.resolver == Resolver.POLICY
    assert r.responsible_party == P.CUSTOMER
    assert r.disposition == D.REFUND
    assert r.amount == 18.0 and r.fee == 0.0
    assert r.return_postage == "customer"
    assert _calls(desk) == (0, 0)


def test_rq301_kettle_auto_approved_by_engines(desk):
    r = desk.process(_req(desk, "RQ-301"))
    assert isinstance(r, Resolution)
    assert r.resolver == Resolver.ENGINES
    assert r.responsible_party == P.CARRIER
    assert r.disposition == D.REFUND
    assert r.amount == 64.0 and r.fee == 0.0
    assert r.reading is not None and r.scores is not None
    assert _calls(desk) == (1, 1)
    assert desk.log.by_request()["RQ-301"].resolver == Resolver.ENGINES


def test_rq307_mixer_over_limit_goes_to_clerk_who_refunds(desk):
    from returns_desk.desk import Pending
    p = desk.process(_req(desk, "RQ-307"))
    assert isinstance(p, Pending)
    assert p.agreement is True
    assert p.reading.responsible_party == P.CARRIER
    assert D.REFUND in p.allowed
    assert _calls(desk) == (1, 1)
    assert "RQ-307" not in desk.log.by_request()

    r = desk.resolve_as_clerk("RQ-307", P.CARRIER, D.REFUND, "Confirmed carrier damage.")
    assert r.resolver == Resolver.CLERK
    assert r.responsible_party == P.CARRIER
    assert r.disposition == D.REFUND
    assert r.amount == 140.0
    assert r.reading is not None                       # the clerk's resolution keeps what the engines said
    assert _calls(desk) == (1, 1)                      # resolving does not ask the engines again
    assert desk.log.by_request()["RQ-307"].resolver == Resolver.CLERK


def test_rq305_blender_engines_split_clerk_decides(desk):
    from returns_desk.desk import Pending
    p = desk.process(_req(desk, "RQ-305"))
    assert isinstance(p, Pending)
    # Glossary: agreement is both engines naming the same party. The probabilistic engine's top party
    # at retailer 0.48 / customer 0.44 is retailer, so they agree; the sketch's "amber" for this row
    # conflicts with that and is reported. It reaches the clerk because 0.48 is under the floor.
    assert p.agreement is True
    assert p.scores.probabilities[P.RETAILER] < desk.policy.auto_approval().min_probability
    assert p.reading.responsible_party == P.RETAILER
    assert p.scores.probabilities[P.RETAILER] == 0.48
    assert p.scores.probabilities[P.CUSTOMER] == 0.44
    assert _calls(desk) == (1, 1)

    r = desk.resolve_as_clerk("RQ-305", P.RETAILER, D.REFUND, "Motor defect.")
    assert r.resolver == Resolver.CLERK
    assert r.responsible_party == P.RETAILER
    assert r.disposition == D.REFUND
    assert r.amount == 89.0 and r.fee == 0.0


def test_rq306_headphones_flagged_unclear_clerk_escalates(desk):
    from returns_desk.desk import Pending
    req = _req(desk, "RQ-306")
    assert desk.assess(req).returns_this_quarter == 2       # third request this quarter
    p = desk.process(req)
    assert isinstance(p, Pending)
    assert "possible_abuse" in p.reading.flags
    assert p.reading.responsible_party == P.UNCLEAR
    assert p.allowed == frozenset()                          # unclear row is empty
    assert _calls(desk) == (1, 1)

    r = desk.resolve_as_clerk("RQ-306", P.UNCLEAR, D.ESCALATE, "Delivery disputed, third this quarter.")
    assert r.resolver == Resolver.CLERK
    assert r.responsible_party == P.UNCLEAR
    assert r.disposition == D.ESCALATE
    assert r.amount == 0.0


# ------------------------------------------------------------------ the RQ-307 replay

def test_rq307_replay_with_raised_limit_auto_approves(desk, make_desk, canned_engines):
    from returns_desk.policy import JsonPolicy
    desk.process(_req(desk, "RQ-307"))
    desk.resolve_as_clerk("RQ-307", P.CARRIER, D.REFUND, "Confirmed carrier damage.")
    assert "RQ-307" not in [r.request_id for r in desk.queue()]

    data = copy.deepcopy(json.loads((DATA / "policy.json").read_text()))
    data["auto_approval"]["max_amount"] = 150.0
    desk.log.reset()
    assert desk.log.all() == []

    raised = make_desk(policy=JsonPolicy(data))
    assert "RQ-307" in [r.request_id for r in raised.queue()]
    r = raised.process(_req(raised, "RQ-307"))
    assert isinstance(r, Resolution)
    assert r.resolver == Resolver.ENGINES
    assert r.responsible_party == P.CARRIER
    assert r.disposition == D.REFUND
    assert r.amount == 140.0
    assert canned_engines.reading.usage.calls == 2      # once per pull, nobody touched the engine


# ------------------------------------------------------------------ clerk bounds

def test_clerk_partial_within_bounds(desk):
    desk.process(_req(desk, "RQ-305"))
    r = desk.resolve_as_clerk("RQ-305", P.CUSTOMER, D.PARTIAL, "Keep the customer.", partial_amount=40.0)
    assert r.disposition == D.PARTIAL
    assert r.amount == 40.0
    assert r.resolver == Resolver.CLERK
    assert desk.log.by_request()["RQ-305"].amount == 40.0


def test_clerk_amount_above_paid_rejected(desk):
    desk.process(_req(desk, "RQ-305"))
    with pytest.raises(ValidationError):
        desk.resolve_as_clerk("RQ-305", P.CUSTOMER, D.PARTIAL, "Too generous.", partial_amount=200.0)
    assert "RQ-305" not in desk.log.by_request()


def test_clerk_money_disposition_outside_table_rejected(desk):
    desk.process(_req(desk, "RQ-306"))
    with pytest.raises(ValidationError):
        desk.resolve_as_clerk("RQ-306", P.UNCLEAR, D.REFUND, "Just refund it.")
    assert desk.log.all() == []


def test_clerk_deny_without_note_rejected(desk):
    desk.process(_req(desk, "RQ-305"))
    with pytest.raises(ValidationError) as e:
        desk.resolve_as_clerk("RQ-305", P.CUSTOMER, D.DENY, "   ")
    assert e.value.rule == "note"
    assert desk.log.all() == []


# ------------------------------------------------------------------ queue and auto-approval conditions

SCRIPT_ORDER = ["RQ-303", "RQ-304", "RQ-302", "RQ-301", "RQ-307", "RQ-305", "RQ-306"]


def test_queue_demo_first_in_demo_script_order_and_drops_resolved(desk):
    """Sketch section 9 pulls the demo queue in this order; queue[0] follows it."""
    q = [r.request_id for r in desk.queue()]
    assert q[:7] == SCRIPT_ORDER
    assert len(q) == len(desk.requests)
    desk.process(_req(desk, "RQ-303"))
    assert "RQ-303" not in [r.request_id for r in desk.queue()]


def test_low_probability_blocks_auto_approval(make_desk):
    from conftest import CANNED_SCORES, CannedGenerativeEngine, CannedProbabilisticEngine
    from returns_desk.desk import Pending
    from returns_desk.types import Engines
    scores = dict(CANNED_SCORES)
    scores["RQ-301"] = Scores({P.RETAILER: 0.1, P.CARRIER: 0.79, P.CUSTOMER: 0.06, P.UNCLEAR: 0.05}, 0.7)
    d = make_desk(engines=Engines(CannedGenerativeEngine(), CannedProbabilisticEngine(scores)))
    assert isinstance(d.process(_req(d, "RQ-301")), Pending)


def test_blocking_flag_blocks_auto_approval(make_desk):
    from conftest import CANNED_READINGS, CannedGenerativeEngine, CannedProbabilisticEngine
    from returns_desk.desk import Pending
    from returns_desk.types import Engines
    readings = dict(CANNED_READINGS)
    readings["RQ-301"] = readings["RQ-301"].model_copy(update={"flags": ["used"]})
    d = make_desk(engines=Engines(CannedGenerativeEngine(readings), CannedProbabilisticEngine()))
    assert isinstance(d.process(_req(d, "RQ-301")), Pending)


# ------------------------------------------------------------------ the resolution log

def test_log_round_trips_reading_and_scores(desk, resolution_log):
    desk.process(_req(desk, "RQ-301"))
    line = json.loads(resolution_log.path.read_text().splitlines()[0])
    assert isinstance(line["reading"], dict) and line["reading"]["responsible_party"] == "carrier"
    assert line["scores"]["probabilities"]["carrier"] == 0.85
    assert line["resolved_at"].startswith("2026-10-30T09:00")
    back = resolution_log.all()[0]
    assert isinstance(back.reading, Reading)
    assert back.scores.probabilities[P.CARRIER] == 0.85
    assert back.resolver == Resolver.ENGINES and back.disposition == D.REFUND


def test_log_reset_truncates(desk, resolution_log):
    desk.process(_req(desk, "RQ-303"))
    assert len(resolution_log.all()) == 1
    resolution_log.reset()
    assert resolution_log.all() == []
    assert resolution_log.path.exists()


def test_returns_this_quarter_counts_logged_requests(desk, resolution_log):
    req = _req(desk, "RQ-301")
    assert resolution_log.returns_this_quarter("c_118", SAMPLE_TODAY, desk.orders, desk.requests, before=req) == 0
    other = _req(desk, "RQ-302")    # customer c_131; logging it does not count for c_118
    desk.process(other)
    assert resolution_log.returns_this_quarter("c_118", SAMPLE_TODAY, desk.orders, desk.requests, before=req) == 0
    assert resolution_log.returns_this_quarter("c_131", SAMPLE_TODAY, desk.orders, desk.requests) == 1


def test_returns_this_quarter_does_not_count_a_denied_request(desk, resolution_log):
    """A request policy or the clerk turned down is not a return, so it uses none of the cap."""
    from returns_desk.types import Resolution
    req = _req(desk, "RQ-306")                 # c_207; RQ-313 and RQ-328 are ahead of it
    assert desk.assess(req).returns_this_quarter == 2
    resolution_log.append(Resolution("RQ-313", Resolver.CLERK, P.CARRIER, D.DENY, 0.0, 0.0, "none",
                                     note="No damage visible."))
    after = resolution_log.returns_this_quarter("c_207", SAMPLE_TODAY, desk.orders, desk.requests, before=req)
    assert after == 1


def _desk_without_delivery_date(canned_engines, resolution_log, **request):
    """A desk over one order the carrier never reported delivered."""
    from returns_desk.desk import Desk
    from returns_desk.policy import JsonPolicy
    from returns_desk.types import Item, Order, OrderLine, Request
    order = Order("R-9001", "c_900", date(2026, 10, 10), None, (OrderLine("KET-200", 1, 64.0),))
    req = Request("RQ-901", "R-9001", "KET-200", date(2026, 10, 28), **request)
    d = Desk(JsonPolicy.from_file(DATA / "policy.json"), canned_engines,
             {"KET-200": Item("KET-200", "Stovetop kettle, 2 L", "kitchen_hard", 7)},
             {"R-9001": order}, [req], resolution_log, today=SAMPLE_TODAY, data_dir=DATA)
    return d, req


def test_no_delivery_date_with_a_statement_asks_the_engines_but_never_auto_approves(canned_engines, resolution_log):
    from conftest import _scores
    from returns_desk.desk import Pending
    canned_engines.reading.answers["RQ-901"] = Reading(
        responsible_party=P.CARRIER, reason="Tracking stopped and the parcel never arrived.",
        reply_draft="Sorry your kettle has not arrived.")
    canned_engines.scoring.answers["RQ-901"] = _scores(0.03, 0.92, 0.03, 0.02, 0.9)
    d, req = _desk_without_delivery_date(canned_engines, resolution_log,
                                         statement="Tracking stopped a week ago and it never came.")
    result = d.process(req)
    assert isinstance(result, Pending) and result.reading.responsible_party == P.CARRIER
    assert _calls(d) == (1, 1)


def test_no_delivery_date_with_a_reason_code_goes_to_the_clerk_without_the_engines(canned_engines, resolution_log):
    from returns_desk.desk import Pending
    d, req = _desk_without_delivery_date(canned_engines, resolution_log, reason_code="changed_mind")
    result = d.process(req)
    assert isinstance(result, Pending) and result.reading is None
    assert _calls(d) == (0, 0)


def test_today_from_env(monkeypatch, canned_engines, resolution_log):
    from returns_desk.desk import load_desk
    monkeypatch.setenv("RETURNS_DESK_TODAY", "2027-01-15")
    d = load_desk(DATA, engines=canned_engines, log=resolution_log)
    assert d.today == date(2027, 1, 15)


def test_today_defaults_to_the_sample_sets_date_not_the_real_one(monkeypatch, canned_engines, resolution_log):
    """The sample set is dated October 2026 and the per-quarter count uses today's quarter, so the
    demo only tells its story on a date in that quarter, whatever the calendar says."""
    import returns_desk.desk as desk_module
    from returns_desk.desk import load_desk

    class Later(date):
        @classmethod
        def today(cls):
            return cls(2027, 2, 1)

    monkeypatch.setattr(desk_module, "date", Later)
    monkeypatch.delenv("RETURNS_DESK_TODAY", raising=False)
    d = load_desk(DATA, engines=canned_engines, log=resolution_log)
    assert d.today == date(2026, 10, 30)


def test_reaching_the_per_quarter_cap_blocks_auto_approval(make_desk):
    """Glossary, Auto-Approval: the customer is under the per-quarter cap. c_207's third request
    this quarter has clean, agreeing answers above the floor and under the limit; policy lets it
    through (the cap denies only past it), but it must go to the clerk, not auto-approve."""
    from conftest import CANNED_READINGS, CANNED_SCORES, CannedGenerativeEngine, CannedProbabilisticEngine, _scores
    from returns_desk.desk import Pending
    from returns_desk.types import Engines
    readings = dict(CANNED_READINGS)
    readings["RQ-328"] = Reading(responsible_party=P.CUSTOMER, reason="The color was not as expected.",
                                 reply_draft="Thanks, we will refund you.", flags=["remorse"])
    scores = dict(CANNED_SCORES)
    scores["RQ-328"] = _scores(0.03, 0.02, 0.92, 0.03, 0.9)
    engines = lambda: Engines(CannedGenerativeEngine(readings), CannedProbabilisticEngine(scores))

    # Control: RQ-313 is the only earlier request from c_207, so RQ-328 is the second. Auto-approved.
    fresh = make_desk(engines=engines())
    rq328 = _req(fresh, "RQ-328")
    assert fresh.assess(rq328).returns_this_quarter == 1
    assert fresh.process(rq328).resolver == Resolver.ENGINES

    # With RQ-306 already resolved, RQ-328 is c_207's third this quarter: the cap is reached.
    fresh.log.reset()
    d = make_desk(engines=engines())
    d.process(_req(d, "RQ-306"))
    d.resolve_as_clerk("RQ-306", P.UNCLEAR, D.ESCALATE, "Delivery disputed.")
    a = d.assess(_req(d, "RQ-328"))
    assert a.passes and a.returns_this_quarter == 2
    assert isinstance(d.process(_req(d, "RQ-328")), Pending)
