"""The returns board over a Desk built from the canned engines. No Azure."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from conftest import DATA
from returns_desk.web import build_app

BOARD = {"X-Requested-With": "returns-desk"}


@pytest.fixture
def board(desk, tmp_path):
    """Returns a factory: board(ids) restricts the queue to those requests and returns
    (client, desk). Evaluation, policy and photos point into tmp_path."""
    def _board(*ids):
        if ids:
            desk.requests = [r for r in desk.requests if r.request_id in ids]
        (tmp_path / "photos").mkdir(exist_ok=True)
        app = build_app(desk, evaluation_path=tmp_path / "evaluation.json",
                        policy_path=DATA / "policy.json", photos_dir=tmp_path / "photos")
        return TestClient(app, headers=BOARD), desk
    return _board


def _calls(desk):
    return desk.engines.reading.usage.calls, desk.engines.scoring.usage.calls


def test_page_and_healthz(board):
    client, _ = board("RQ-301")
    page = client.get("/")
    assert page.status_code == 200 and "Returns Desk" in page.text
    assert client.get("/healthz").json()["ok"] is True


def test_state_shape(board):
    client, desk = board("RQ-301", "RQ-303")
    s = client.get("/api/state").json()
    assert set(s["engines"]) == {"reading", "scoring"}
    for e in s["engines"].values():
        assert {"name", "auth", "calls", "input_tokens", "output_tokens", "cost_usd"} <= set(e)
    assert s["engines"]["reading"]["auth"] == "canned"
    assert s["counts"] == {"policy": 0, "engines": 0, "clerk": 0}
    assert s["queue_length"] == 2
    assert s["policy"]["window_days"] == 30
    assert s["current"] is None and s["log"] == []


def test_next_rq303_policy_denial_no_engine_calls(board):
    client, desk = board("RQ-303")
    body = client.post("/api/next").json()
    res = body["resolution"]
    assert res["resolver"] == "policy" and res["disposition"] == "deny"
    assert _calls(desk) == (0, 0)
    s = client.get("/api/state").json()
    assert s["counts"]["policy"] == 1 and s["queue_length"] == 0
    assert s["log"][0]["resolver"] == "policy"


def test_next_rq301_engines_resolution(board):
    client, desk = board("RQ-301")
    res = client.post("/api/next").json()["resolution"]
    assert res["resolver"] == "engines" and res["disposition"] == "refund"
    assert res["responsible_party"] == "carrier" and res["amount"] == 64.0
    assert _calls(desk) == (1, 1)


def test_next_rq305_pending_for_the_clerk(board):
    client, _ = board("RQ-305")
    p = client.post("/api/next").json()["pending"]
    # The canned scores put retailer on top (0.48 against 0.44), so by the glossary the engines
    # agree on RQ-305; the sketch's "agreement false" for this row conflicts and is reported.
    # The board passes through what the Desk says.
    assert p["agreement"] is True
    assert p["reading"]["responsible_party"] == "retailer"
    assert p["scores"]["probabilities"]["customer"] == 0.44 and p["scores"]["top"] == "retailer"
    assert "refund" in p["allowed"] and "deny" not in p["allowed"]
    assert set(p["allowed_by_party"]) == {"retailer", "carrier", "customer", "unclear"}
    s = client.get("/api/state").json()
    assert s["current"]["request"]["request_id"] == "RQ-305"
    # a second pull while the clerk has one open is refused
    assert client.post("/api/next").status_code == 409


def test_pending_with_split_engines_reports_agreement_false(desk, tmp_path):
    from conftest import CANNED_SCORES, CannedGenerativeEngine, CannedProbabilisticEngine, _scores
    from returns_desk.types import Engines
    split = dict(CANNED_SCORES)
    split["RQ-305"] = _scores(0.40, 0.05, 0.50, 0.05, 0.5)       # probabilistic engine tops customer
    desk.engines = Engines(CannedGenerativeEngine(), CannedProbabilisticEngine(split))
    desk.requests = [r for r in desk.requests if r.request_id == "RQ-305"]
    client = TestClient(build_app(desk, evaluation_path=tmp_path / "e.json"), headers=BOARD)
    p = client.post("/api/next").json()["pending"]
    assert p["agreement"] is False and p["scores"]["top"] == "customer"


def test_pending_says_when_a_rule_sent_it_to_the_clerk(board):
    from dataclasses import replace
    client, desk = board("RQ-305")
    assert client.post("/api/next").json()["pending"]["clerk_rule"] is None
    client.post("/api/reset")
    desk.orders["R-2045"] = replace(desk.orders["R-2045"], delivered=None)
    assert client.post("/api/next").json()["pending"]["clerk_rule"] == "no_delivery_date"


def test_next_when_queue_empty_is_404(board):
    client, _ = board("RQ-303")
    client.post("/api/next")
    assert client.post("/api/next").status_code == 404


def test_resolve_by_clerk(board):
    client, _ = board("RQ-305")
    client.post("/api/next")
    r = client.post("/api/resolve", json={"request_id": "RQ-305", "responsible_party": "retailer",
                                          "disposition": "refund", "note": "motor defect"})
    assert r.status_code == 200
    assert r.json()["resolver"] == "clerk" and r.json()["disposition"] == "refund"
    s = client.get("/api/state").json()
    assert s["counts"]["clerk"] == 1 and s["current"] is None


def test_resolve_rejects_amount_above_paid_with_rule_name(board):
    client, desk = board("RQ-305")
    client.post("/api/next")
    r = client.post("/api/resolve", json={"request_id": "RQ-305", "responsible_party": "customer",
                                          "disposition": "partial", "note": "keep them",
                                          "partial_amount": 9999})
    assert r.status_code == 400
    assert r.json()["rule"] == "partial_range"
    assert desk.log.all() == []
    # still open for the clerk to try again
    assert client.get("/api/state").json()["current"] is not None


def test_resolve_without_pending_is_409(board):
    client, _ = board("RQ-305")
    r = client.post("/api/resolve", json={"request_id": "RQ-305", "responsible_party": "retailer",
                                          "disposition": "refund", "note": ""})
    assert r.status_code == 409


def test_resolve_deny_needs_a_note(board):
    client, _ = board("RQ-305")
    client.post("/api/next")
    r = client.post("/api/resolve", json={"request_id": "RQ-305", "responsible_party": "retailer",
                                          "disposition": "deny", "note": " "})
    assert r.status_code == 400 and r.json()["rule"] == "note"


def test_reset_empties_log_and_restores_queue(board):
    client, desk = board("RQ-301", "RQ-303")
    client.post("/api/next"); client.post("/api/next")
    assert len(desk.log.all()) == 2
    assert client.post("/api/reset").json()["ok"] is True
    assert desk.log.all() == []
    s = client.get("/api/state").json()
    assert s["log"] == [] and s["queue_length"] == 2
    assert s["engines"]["reading"]["calls"] == 0


def test_reset_discards_a_pending_request(board):
    client, _ = board("RQ-305")
    client.post("/api/next")
    client.post("/api/reset")
    assert client.get("/api/state").json()["current"] is None
    assert client.post("/api/next").json()["pending"]["request"]["request_id"] == "RQ-305"


def test_reset_reloads_policy_from_file(desk, tmp_path):
    """Demo step: raise max_amount in the file, press reset, RQ-307 auto-approves."""
    policy = json.loads((DATA / "policy.json").read_text())
    policy["auto_approval"]["max_amount"] = 150.0
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy))
    desk.requests = [r for r in desk.requests if r.request_id == "RQ-307"]
    client = TestClient(build_app(desk, evaluation_path=tmp_path / "e.json", policy_path=path), headers=BOARD)
    assert client.get("/api/state").json()["policy"]["auto_approval"]["max_amount"] == 150.0
    client.post("/api/reset")
    res = client.post("/api/next").json()["resolution"]
    assert res["resolver"] == "engines" and res["amount"] == 140.0


def test_evaluation_404_then_200(board, tmp_path):
    client, _ = board("RQ-301")
    assert client.get("/api/evaluation").status_code == 404
    saved = {"run_at": "2026-10-30T09:00:00+00:00", "model": "x", "labeled_requests": 40,
             "reports": [{"engine": "reading", "requests": 40, "correct": 36}]}
    (tmp_path / "evaluation.json").write_text(json.dumps(saved))
    r = client.get("/api/evaluation")
    assert r.status_code == 200 and r.json() == saved


def test_photos_served_and_confined(board, tmp_path):
    client, _ = board("RQ-301")
    (tmp_path / "photos" / "rq-301.jpg").write_bytes(b"\xff\xd8jpeg")
    (tmp_path / "secret.txt").write_text("no")
    assert client.get("/photos/rq-301.jpg").content == b"\xff\xd8jpeg"
    assert client.get("/photos/missing.jpg").status_code == 404
    assert client.get("/photos/..%2Fsecret.txt").status_code == 404
    assert client.get("/photos/../secret.txt").status_code in (404, 400)


def test_pending_carries_photo_url(board):
    client, desk = board("RQ-307")
    p = client.post("/api/next").json()["pending"]
    photo = next(r.photo for r in desk.requests if r.request_id == "RQ-307")
    assert photo and p["request"]["photo_url"] == "/" + photo


def test_page_uses_glossary_terms_only(board):
    client, _ = board("RQ-301")
    import re
    text = client.get("/").text.lower()
    for banned in ("fault", "llm", "decision model", "decision engine", "stand-in", "mock",
                   "ground truth", "gold set", "outcome", "tier", "agent", "ticket"):
        assert not re.search(rf"\b{re.escape(banned)}\b", text), banned


def test_next_pulls_a_named_request(board):
    client, desk = board()
    assert desk.queue()[0].request_id != "RQ-305"
    p = client.post("/api/next", json={"request_id": "RQ-305"}).json()["pending"]
    assert p["request"]["request_id"] == "RQ-305"


def test_next_named_request_not_in_queue_is_404(board):
    client, _ = board("RQ-303", "RQ-301")
    assert client.post("/api/next", json={"request_id": "RQ-999"}).status_code == 404
    client.post("/api/next", json={"request_id": "RQ-303"})          # resolved by policy
    assert client.post("/api/next", json={"request_id": "RQ-303"}).status_code == 404


def test_rq307_can_be_pulled_again_after_reset(board):
    """Demo step 5: resolve RQ-307, press the demo reset, pull RQ-307 again, though it is not
    at the head of the queue."""
    client, desk = board()
    client.post("/api/next", json={"request_id": "RQ-307"})
    r = client.post("/api/resolve", json={"request_id": "RQ-307", "responsible_party": "carrier",
                                          "disposition": "refund", "note": "carrier damage"})
    assert r.status_code == 200
    client.post("/api/reset")
    assert desk.queue()[0].request_id != "RQ-307"
    again = client.post("/api/next", json={"request_id": "RQ-307"}).json()
    assert again["pending"]["request"]["request_id"] == "RQ-307"


def test_next_without_a_body_still_pulls_the_head_of_the_queue(board):
    client, desk = board()
    head = desk.queue()[0].request_id
    body = client.post("/api/next").json()
    view = body["resolution"] if "resolution" in body else body["pending"]
    assert view["request"]["request_id"] == head


def test_page_has_no_borrowed_or_test_engine_wording(board):
    client, _ = board("RQ-301")
    text = client.get("/").text
    assert "<span>Bench</span>" not in text
    assert "Canned answers" not in text and "canned" not in text.lower()


def test_posts_without_the_board_header_are_refused(board):
    """A cross-site form cannot set a custom header, so the board's POSTs require one."""
    client, desk = board("RQ-305")
    client.headers.pop("X-Requested-With")
    for path in ("/api/next", "/api/reset"):
        assert client.post(path).status_code == 403
    assert _calls(desk) == (0, 0)


def test_engine_failure_does_not_leak_the_error_to_the_browser(board):
    client, desk = board("RQ-305")
    def boom(request, context):
        raise RuntimeError("https://secret-endpoint.example/openai refused the call")
    desk.engines.reading.read = boom
    r = client.post("/api/next")
    assert r.status_code == 502
    assert "secret-endpoint" not in r.text and "RuntimeError" not in r.text
    assert client.get("/api/state").json()["queue_length"] == 1


def test_clerk_note_is_bounded(board):
    client, _ = board("RQ-305")
    client.post("/api/next")
    r = client.post("/api/resolve", json={"request_id": "RQ-305", "responsible_party": "retailer",
                                          "disposition": "deny", "note": "x" * 2001})
    assert r.status_code == 422
