"""Engine boundary: message building, scoring input, cost table, constructors.

No network. Constructors take a fake client through the optional `client` parameter; the
Foundry calls go to fake objects that return canned API-shaped responses.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from returns_desk import model
from returns_desk.types import (
    Engines, Item, OrderLine, Reading, Request, ResponsibleParty, Scores, Usage,
)


# ----------------------------------------------------------------------------- fixtures

@dataclass
class Ctx:
    item: Item
    order_line: OrderLine
    delivered: date | None
    photo_path: Path | None


KETTLE = Item(sku="KET-200", name="Stovetop kettle, 2 L", category="kitchen_hard", in_stock=7)
LINE = OrderLine(sku="KET-200", qty=1, paid=64.00)
STATEMENT = "Box arrived crushed, the handle was snapped clean off. I have not used it."
# A tiny but valid JPEG header is not needed; any bytes do, the engine never decodes them.
PHOTO_BYTES = b"\xff\xd8\xff\xe0fake-jpeg-bytes\xff\xd9"


def make_request(**kw) -> Request:
    base = dict(request_id="RQ-301", order_id="R-2041", sku="KET-200",
                submitted=date(2026, 10, 14), statement=STATEMENT)
    base.update(kw)
    return Request(**base)


def ctx(photo_path: Path | None = None, delivered: date | None = date(2026, 10, 5)) -> Ctx:
    return Ctx(item=KETTLE, order_line=LINE, delivered=delivered, photo_path=photo_path)


@pytest.fixture
def photo(tmp_path: Path) -> Path:
    p = tmp_path / "rq-301.jpg"
    p.write_bytes(PHOTO_BYTES)
    return p


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("ENDPOINT", "DEPLOYMENT", "MODEL", "API_KEY", "DECISION_DEPLOYMENT", "DECISION_API_KEY"):
        monkeypatch.delenv(f"RETURNS_DESK_{k}", raising=False)


def user_parts(messages: list[dict]) -> list[dict]:
    user = [m for m in messages if m["role"] == "user"]
    assert len(user) == 1
    content = user[0]["content"]
    assert isinstance(content, list)
    return content


# ----------------------------------------------------------------------------- reading messages

def test_reading_messages_have_system_then_user():
    msgs = model.build_reading_messages(make_request(), ctx())
    assert [m["role"] for m in msgs] == ["system", "user"]


def test_reading_system_prompt_names_the_four_parties_and_flags():
    system = model.build_reading_messages(make_request(), ctx())[0]["content"]
    for party in ("retailer", "carrier", "customer", "unclear"):
        assert party in system
    for flag in ("damage_in_transit", "defect", "remorse", "wrong_item", "used", "possible_abuse"):
        assert flag in system
    assert "three sentences" in system
    assert "not" in system and "blame" in system   # who pays, not who is to blame


def test_reading_user_text_carries_the_request_facts():
    req = make_request(reason_code="wrong_item")
    parts = user_parts(model.build_reading_messages(req, ctx()))
    text = " ".join(p["text"] for p in parts if p["type"] == "text")
    assert STATEMENT in text
    assert "Stovetop kettle, 2 L" in text
    assert "kitchen_hard" in text
    assert "64.00" in text
    assert "2026-10-05" in text      # delivery date
    assert "2026-10-14" in text      # submitted date
    assert "wrong_item" in text


def test_reading_user_text_handles_missing_delivery_and_reason_code():
    parts = user_parts(model.build_reading_messages(make_request(), ctx(delivered=None)))
    text = " ".join(p["text"] for p in parts if p["type"] == "text")
    assert STATEMENT in text
    assert "None" not in text


def test_reading_no_image_part_without_photo():
    parts = user_parts(model.build_reading_messages(make_request(), ctx()))
    assert [p["type"] for p in parts] == ["text"]


def test_reading_image_part_is_jpeg_data_url_when_photo_set(photo):
    parts = user_parts(model.build_reading_messages(make_request(photo="photos/rq-301.jpg"), ctx(photo)))
    assert [p["type"] for p in parts] == ["text", "image_url"]
    url = parts[1]["image_url"]["url"]
    prefix = "data:image/jpeg;base64,"
    assert url.startswith(prefix)
    assert base64.b64decode(url[len(prefix):]) == PHOTO_BYTES


@pytest.mark.parametrize("suffix,media", [(".png", "image/png"), (".webp", "image/webp"),
                                          (".gif", "image/gif"), (".jpeg", "image/jpeg")])
def test_media_type_follows_suffix(tmp_path, suffix, media):
    p = tmp_path / f"x{suffix}"
    p.write_bytes(b"abc")
    parts = user_parts(model.build_reading_messages(make_request(), ctx(p)))
    assert parts[1]["image_url"]["url"].startswith(f"data:{media};base64,")


# ----------------------------------------------------------------------------- scoring input

def test_scoring_input_parts_without_photo():
    inp = model.build_scoring_input(make_request(), ctx())
    assert [p["type"] for p in inp] == ["input_text"]
    assert STATEMENT in inp[0]["text"]


def test_scoring_input_image_part_when_photo_set(photo):
    inp = model.build_scoring_input(make_request(photo="photos/rq-301.jpg"), ctx(photo))
    assert [p["type"] for p in inp] == ["input_text", "input_image"]
    url = inp[1]["image_url"]
    assert url.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PHOTO_BYTES


def test_scoring_question_has_four_choices_in_order():
    q = model.build_scoring_question()
    assert q["type"] == "choice"
    assert q["instructions"] == "Who is the responsible party for this return?"
    assert [c["value"] for c in q["choices"]] == ["retailer", "carrier", "customer", "unclear"]
    assert all(c["description"].strip() for c in q["choices"])


# ----------------------------------------------------------------------------- prices

def test_estimate_cost_matches_price_table():
    u = Usage(calls=1, input_tokens=2_000_000, output_tokens=1_000_000)
    inp, out = model.PRICES_PER_M["gpt-6-luna"]
    assert model.estimate_cost_usd("gpt-6-luna", u) == pytest.approx(2 * inp + out)


def test_estimate_cost_decisions_bills_input_only():
    u = Usage(calls=1, input_tokens=1_000_000, output_tokens=500_000)
    inp, out = model.PRICES_PER_M["gpt-6-luna (decisions)"]
    assert out == 0.0
    assert model.estimate_cost_usd("gpt-6-luna (decisions)", u) == pytest.approx(inp)


def test_estimate_cost_unknown_model_is_none():
    assert model.estimate_cost_usd("nope", Usage()) is None


# ----------------------------------------------------------------------------- fake clients

class FakeChat:
    def __init__(self, parsed: Reading | None):
        self.parsed = parsed
        self.calls: list[dict] = []
        self.completions = self

    def parse(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=120, completion_tokens=40,
                                  prompt_tokens_details=SimpleNamespace(cached_tokens=10)),
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=self.parsed))],
        )


class FakeReadingClient:
    def __init__(self, parsed: Reading | None):
        self.chat = FakeChat(parsed)


class FakeDecisions:
    def __init__(self, answer):
        self.answer = answer
        self.calls: list[dict] = []

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=200, output_tokens=0, input_tokens_details=None),
            answers=[self.answer],
        )


class FakeScoringClient:
    def __init__(self, answer):
        self.decisions = FakeDecisions(answer)


CARRIER_READING = Reading(responsible_party=ResponsibleParty.CARRIER, reason="Crushed in transit.",
                          reply_draft="Sorry about that. We will refund you.", flags=["damage_in_transit"])


def choice_answer(probs: dict[str, float], choice: str, confidence: float):
    return SimpleNamespace(type="choice", choice=choice, confidence=confidence,
                           probabilities=[SimpleNamespace(value=k, probability=v) for k, v in probs.items()])


# ----------------------------------------------------------------------------- generative engine

def test_generative_engine_with_fake_client_returns_reading_and_records_usage(photo):
    client = FakeReadingClient(CARRIER_READING)
    eng = model.FoundryGenerativeEngine(endpoint="https://x.example/", deployment="dep",
                                     model_name="gpt-6-luna", client=client)
    assert eng.name == "gpt-6-luna"
    out = eng.read(make_request(photo="photos/rq-301.jpg"), ctx(photo))
    assert out == CARRIER_READING
    call = client.chat.calls[0]
    assert call["model"] == "dep"
    assert call["response_format"] is Reading
    assert call["messages"] == model.build_reading_messages(make_request(photo="photos/rq-301.jpg"), ctx(photo))
    assert (eng.usage.calls, eng.usage.input_tokens, eng.usage.output_tokens,
            eng.usage.cached_input_tokens) == (1, 120, 40, 10)


def test_generative_engine_no_parsed_answer_is_unclear():
    eng = model.FoundryGenerativeEngine(endpoint="https://x", deployment="dep",
                                     client=FakeReadingClient(None))
    out = eng.read(make_request(), ctx())
    assert out.responsible_party is ResponsibleParty.UNCLEAR


def test_generative_engine_auth_from_env(monkeypatch):
    monkeypatch.setenv("RETURNS_DESK_API_KEY", "k")
    eng = model.FoundryGenerativeEngine(endpoint="https://x", deployment="d", client=FakeReadingClient(None))
    assert eng.auth == "api-key"


def test_generative_engine_auth_entra_by_default():
    eng = model.FoundryGenerativeEngine(endpoint="https://x", deployment="d", client=FakeReadingClient(None))
    assert eng.auth == "entra"


def test_generative_engine_logs_no_photo_bytes(photo, caplog):
    caplog.set_level("DEBUG")
    eng = model.FoundryGenerativeEngine(endpoint="https://x", deployment="d",
                                     client=FakeReadingClient(CARRIER_READING))
    eng.read(make_request(photo="photos/rq-301.jpg"), ctx(photo))
    b64 = base64.b64encode(PHOTO_BYTES).decode()
    assert b64 not in caplog.text
    assert "base64" not in caplog.text


# ----------------------------------------------------------------------------- probabilistic engine

def test_probabilistic_engine_maps_probabilities_to_scores(photo):
    ans = choice_answer({"retailer": 0.05, "carrier": 0.85, "customer": 0.05, "unclear": 0.05}, "carrier", 0.9)
    client = FakeScoringClient(ans)
    eng = model.FoundryProbabilisticEngine(endpoint="https://x", deployment="dep",
                                     model_name="gpt-6-luna", client=client)
    assert eng.name == "gpt-6-luna (decisions)"
    s = eng.score(make_request(photo="photos/rq-301.jpg"), ctx(photo))
    assert isinstance(s, Scores)
    assert s.probabilities[ResponsibleParty.CARRIER] == pytest.approx(0.85)
    assert set(s.probabilities) == set(ResponsibleParty)
    assert s.top is ResponsibleParty.CARRIER
    assert s.confidence == pytest.approx(0.9)
    call = client.decisions.calls[0]
    assert call["model"] == "dep"
    # Decisions takes input items, not bare parts: the parts ride in one user message.
    parts = model.build_scoring_input(make_request(photo="photos/rq-301.jpg"), ctx(photo))
    assert call["input"] == [{"type": "message", "role": "user", "content": parts}]
    assert call["questions"] == [model.build_scoring_question()]
    assert (eng.usage.calls, eng.usage.input_tokens, eng.usage.output_tokens) == (1, 200, 0)


def test_probabilistic_engine_fills_missing_parties_with_zero():
    ans = choice_answer({"retailer": 0.6, "customer": 0.4}, "retailer", 0.5)
    eng = model.FoundryProbabilisticEngine(endpoint="https://x", deployment="d", client=FakeScoringClient(ans))
    s = eng.score(make_request(), ctx())
    assert s.probabilities[ResponsibleParty.CARRIER] == 0.0
    assert s.probabilities[ResponsibleParty.UNCLEAR] == 0.0


def test_probabilistic_engine_refusal_maps_to_unclear():
    ans = SimpleNamespace(type="refusal", refusal="no")
    eng = model.FoundryProbabilisticEngine(endpoint="https://x", deployment="d", client=FakeScoringClient(ans))
    s = eng.score(make_request(), ctx())
    assert s.probabilities[ResponsibleParty.UNCLEAR] == 1.0
    assert s.confidence == 0.0
    assert s.top is ResponsibleParty.UNCLEAR


def test_probabilistic_engine_decision_env_vars(monkeypatch):
    monkeypatch.setenv("RETURNS_DESK_ENDPOINT", "https://x")
    monkeypatch.setenv("RETURNS_DESK_DEPLOYMENT", "chat-dep")
    monkeypatch.setenv("RETURNS_DESK_DECISION_DEPLOYMENT", "dec-dep")
    monkeypatch.setenv("RETURNS_DESK_DECISION_API_KEY", "k")
    eng = model.FoundryProbabilisticEngine(client=FakeScoringClient(None))
    assert eng.deployment == "dec-dep"
    assert eng.auth == "api-key"
    assert eng.name == "dec-dep (decisions)"


# ----------------------------------------------------------------------------- from_env

def test_from_env_builds_both_engines(monkeypatch):
    monkeypatch.setenv("RETURNS_DESK_ENDPOINT", "https://x.example/")
    monkeypatch.setenv("RETURNS_DESK_DEPLOYMENT", "dep")
    monkeypatch.setenv("RETURNS_DESK_MODEL", "gpt-6-luna")
    monkeypatch.setenv("RETURNS_DESK_API_KEY", "k")   # api key: no Entra credential is built
    engines = model.from_env()
    assert isinstance(engines, Engines)
    assert isinstance(engines.reading, model.FoundryGenerativeEngine)
    assert isinstance(engines.scoring, model.FoundryProbabilisticEngine)
    assert engines.reading.endpoint == "https://x.example"
    assert engines.reading.name == "gpt-6-luna"
    assert engines.scoring.name == "gpt-6-luna (decisions)"
    assert engines.scoring.deployment == "dep"


def test_from_env_requires_endpoint():
    with pytest.raises(KeyError):
        model.from_env()
