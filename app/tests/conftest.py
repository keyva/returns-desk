"""Canned engines and desk fixtures. Tests never call Azure.

The canned engines answer from a table keyed by request id, with answers for the seven demo
requests as the sketch's demo queue table expects. They count calls through `usage`, so a test
can assert an engine was not asked. Imports of desk and log are deferred into the fixtures so
this file never breaks a test run for a module that does not use them.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from returns_desk.types import Reading, Request, ResponsibleParty as P, Scores, Usage

APP = Path(__file__).resolve().parents[1]
DATA = APP / "data"
SAMPLE_TODAY = date(2026, 10, 30)


def _scores(retailer: float, carrier: float, customer: float, unclear: float, confidence: float = 0.8) -> Scores:
    return Scores({P.RETAILER: retailer, P.CARRIER: carrier, P.CUSTOMER: customer, P.UNCLEAR: unclear}, confidence)


CANNED_READINGS: dict[str, Reading] = {
    "RQ-301": Reading(responsible_party=P.CARRIER,
                      reason="The box was crushed in transit and the handle snapped before use.",
                      reply_draft="Sorry your kettle arrived broken. We will refund you in full."),
    "RQ-302": Reading(responsible_party=P.CUSTOMER, reason="The customer changed their mind.",
                      reply_draft="Thanks, we will process your return.", flags=["remorse"]),
    "RQ-303": Reading(responsible_party=P.RETAILER, reason="The cheese tasted off on arrival.",
                      reply_draft="Sorry about the cheese.", flags=["defect"]),
    "RQ-304": Reading(responsible_party=P.CUSTOMER, reason="The jacket was the wrong size.",
                      reply_draft="Thanks for writing."),
    "RQ-305": Reading(responsible_party=P.RETAILER,
                      reason="A grinding noise after normal use points to a motor defect.",
                      reply_draft="Sorry your blender has started grinding. We will sort it out.",
                      flags=["defect"]),
    "RQ-306": Reading(responsible_party=P.UNCLEAR,
                      reason="Tracking says delivered and the customer says it never arrived.",
                      reply_draft="Sorry to hear your headphones did not reach you. We are looking into it.",
                      flags=["possible_abuse"]),
    "RQ-307": Reading(responsible_party=P.CARRIER,
                      reason="The shipping box split and the mixer box is crushed: damage in transit.",
                      reply_draft="Sorry your mixer arrived damaged. We will make it right.",
                      flags=["damage_in_transit"]),
}

CANNED_SCORES: dict[str, Scores] = {
    "RQ-301": _scores(0.06, 0.85, 0.05, 0.04, 0.85),
    "RQ-302": _scores(0.05, 0.02, 0.90, 0.03),
    "RQ-303": _scores(0.70, 0.10, 0.10, 0.10),
    "RQ-304": _scores(0.05, 0.02, 0.90, 0.03),
    "RQ-305": _scores(0.48, 0.05, 0.44, 0.03, 0.5),
    "RQ-306": _scores(0.10, 0.25, 0.20, 0.45, 0.4),
    "RQ-307": _scores(0.05, 0.90, 0.03, 0.02, 0.9),
}


class CannedGenerativeEngine:
    name = "canned-reading"
    auth = "canned"

    def __init__(self, answers: dict[str, Reading] | None = None):
        self.answers = dict(CANNED_READINGS if answers is None else answers)
        self.usage = Usage()
        self.asked: list[str] = []

    def read(self, request: Request, context) -> Reading:
        self.usage.add(0, 0)
        self.asked.append(request.request_id)
        return self.answers[request.request_id]


class CannedProbabilisticEngine:
    name = "canned-scoring"
    auth = "canned"

    def __init__(self, answers: dict[str, Scores] | None = None):
        self.answers = dict(CANNED_SCORES if answers is None else answers)
        self.usage = Usage()
        self.asked: list[str] = []

    def score(self, request: Request, context) -> Scores:
        self.usage.add(0, 0)
        self.asked.append(request.request_id)
        return self.answers[request.request_id]


class FixedClock:
    def __init__(self, at: datetime = datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc)):
        self.at = at

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture
def canned_engines():
    from returns_desk.types import Engines
    return Engines(CannedGenerativeEngine(), CannedProbabilisticEngine())


@pytest.fixture
def resolution_log(tmp_path):
    from returns_desk.log import ResolutionLog
    return ResolutionLog(tmp_path / "resolutions.jsonl", clock=FixedClock())


@pytest.fixture
def make_desk(canned_engines, resolution_log):
    """Build a Desk over the real data files, a temp log and today pinned to 2026-10-30.
    Pass `policy=` to swap the policy, e.g. a copy with a different max_amount."""
    from returns_desk.desk import load_desk

    def _make(policy=None, engines=None, log=None):
        return load_desk(DATA, engines=engines or canned_engines, log=log or resolution_log,
                         today=SAMPLE_TODAY, policy=policy)
    return _make


@pytest.fixture
def desk(make_desk):
    return make_desk()
