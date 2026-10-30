"""The data files load into the contracts and tell the story the sketch tells.

Loads app/data/catalog.json, orders.json and sample_set.json with plain json and the dataclasses
in types.py only, so this test does not depend on loaders other modules are writing.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest

from returns_desk.types import Item, Label, Order, OrderLine, Request, ResponsibleParty

DATA = Path(__file__).resolve().parents[1] / "data"
SAMPLE_TODAY = date(2026, 10, 30)
WINDOW_DAYS = 30
PHOTO_THRESHOLD = {"kitchen_hard": 40.00, "apparel": 100.00}
RETURNABLE = {"kitchen_hard", "apparel"}
CATEGORIES = {"kitchen_hard", "apparel", "perishable", "final_sale"}

# The twelve files in docs/PHOTO-PROMPTS.md and the responsible party each one supports.
PHOTO_PARTY = {
    "photos/kettle-crushed.jpg": "carrier",
    "photos/mixer-crushed.jpg": "carrier",
    "photos/blender-base.jpg": "retailer",
    "photos/jacket-hem.jpg": "customer",
    "photos/shirt-tags.jpg": "customer",
    "photos/wrong-color.jpg": "retailer",
    "photos/headphones-sealed.jpg": "unclear",
    "photos/pan-scratched.jpg": "customer",
    "photos/lamp-missing-part.jpg": "retailer",
    "photos/mug-cracked.jpg": "unclear",
    "photos/boots-sole.jpg": "retailer",
    "photos/toaster-sealed.jpg": "customer",
}


def _load(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def catalog() -> dict[str, Item]:
    items = [Item(**row) for row in _load("catalog.json")]
    return {i.sku: i for i in items}


@pytest.fixture(scope="module")
def orders() -> dict[str, Order]:
    out = {}
    for row in _load("orders.json"):
        order = Order(
            order_id=row["order_id"],
            customer_id=row["customer_id"],
            placed=date.fromisoformat(row["placed"]),
            delivered=date.fromisoformat(row["delivered"]) if row["delivered"] else None,
            lines=tuple(OrderLine(**line) for line in row["lines"]),
        )
        assert order.order_id not in out, f"duplicate order {order.order_id}"
        out[order.order_id] = order
    return out


@pytest.fixture(scope="module")
def requests() -> list[Request]:
    out = []
    for row in _load("sample_set.json"):
        row = dict(row)
        row["submitted"] = date.fromisoformat(row["submitted"])
        if row.get("label") is not None:
            lab = row["label"]
            row["label"] = Label(ResponsibleParty(lab["responsible_party"]), lab["source"])
        out.append(Request(**row))
    return out


@pytest.fixture(scope="module")
def by_id(requests) -> dict[str, Request]:
    return {r.request_id: r for r in requests}


def _quarter(d: date) -> tuple[int, int]:
    return d.year, (d.month - 1) // 3


# ----------------------------------------------------------------------------- shape

def test_catalog_size_and_categories(catalog):
    assert 20 <= len(catalog) <= 30
    assert {i.category for i in catalog.values()} == CATEGORIES
    assert all(i.in_stock >= 0 for i in catalog.values())


def test_request_ids_unique(requests, by_id):
    assert len(by_id) == len(requests)


def test_every_request_order_and_sku_exist(requests, orders, catalog):
    for r in requests:
        assert r.order_id in orders, r.request_id
        assert orders[r.order_id].line(r.sku) is not None, r.request_id
        assert r.sku in catalog, r.request_id


def test_every_order_line_sku_in_catalog(orders, catalog):
    for o in orders.values():
        for line in o.lines:
            assert line.sku in catalog, o.order_id
            assert line.qty >= 1 and line.paid > 0


def test_every_request_has_author_label(requests):
    for r in requests:
        assert r.label is not None, r.request_id
        assert r.label.source == "author", r.request_id


def test_exactly_seven_demo(requests):
    assert sorted(r.request_id for r in requests if r.demo) == [f"RQ-30{n}" for n in range(1, 8)]


def test_forty_with_statement_ten_per_party(requests):
    with_statement = [r for r in requests if r.has_statement]
    assert len(with_statement) == 40
    counts = Counter(r.label.responsible_party for r in with_statement)
    assert counts == {p: 10 for p in ResponsibleParty}


def test_policy_only_extras(requests):
    extras = [r for r in requests if not r.demo and not r.has_statement]
    assert 3 <= len(extras) <= 5
    assert all(r.reason_code for r in extras)


def test_statements_are_not_reused(requests):
    texts = [r.statement.strip().lower() for r in requests if r.statement.strip()]
    assert len(texts) == len(set(texts))


def test_photos_are_prompt_files_matching_label(requests):
    for r in requests:
        if r.photo is None:
            continue
        assert r.photo in PHOTO_PARTY, r.request_id
        assert PHOTO_PARTY[r.photo] == r.label.responsible_party, r.request_id


# ----------------------------------------------------------------------------- dates

def test_dates_consistent(requests, orders):
    for o in orders.values():
        assert o.placed.year == 2026 and o.delivered is not None and o.delivered.year == 2026
        assert o.placed < o.delivered, o.order_id
    for r in requests:
        o = orders[r.order_id]
        assert o.delivered < r.submitted <= SAMPLE_TODAY, r.request_id


def test_statement_requests_reach_the_engines(requests, orders, catalog):
    """Apart from the perishable demo, every statement request passes the rote checks, both
    counted from the submitted date and from the sample set's today."""
    for r in requests:
        if not r.has_statement or r.request_id == "RQ-303":
            continue
        o, item = orders[r.order_id], catalog[r.sku]
        assert item.category in RETURNABLE, r.request_id
        assert (r.submitted - o.delivered).days <= WINDOW_DAYS, r.request_id
        assert (SAMPLE_TODAY - o.delivered).days <= WINDOW_DAYS, r.request_id
        if o.line(r.sku).paid > PHOTO_THRESHOLD[item.category]:
            assert r.photo is not None, r.request_id


def test_only_rq306_customer_reaches_the_cap(requests, orders, by_id):
    per_quarter = Counter((orders[r.order_id].customer_id, _quarter(r.submitted)) for r in requests)
    target = orders[by_id["RQ-306"].order_id].customer_id
    q = _quarter(by_id["RQ-306"].submitted)
    assert per_quarter[(target, q)] == 3
    earlier = [r for r in requests
               if orders[r.order_id].customer_id == target and r.request_id != "RQ-306"]
    assert len(earlier) == 2
    assert all(_quarter(r.submitted) == q and r.submitted < by_id["RQ-306"].submitted for r in earlier)
    assert all(n <= 2 for (cust, _), n in per_quarter.items() if cust != target)


# ----------------------------------------------------------------------------- the demo queue

def _ctx(rid, by_id, orders, catalog):
    r = by_id[rid]
    o = orders[r.order_id]
    return r, o, catalog[r.sku], o.line(r.sku)


def test_rq301_kettle(by_id, orders, catalog):
    r, o, item, line = _ctx("RQ-301", by_id, orders, catalog)
    assert "kettle" in item.name.lower() and item.category == "kitchen_hard"
    assert line.paid == 64.00 and r.photo == "photos/kettle-crushed.jpg"
    assert "crushed" in r.statement.lower() and r.reason_code is None
    assert r.label.responsible_party == "carrier"


def test_rq302_tshirt_changed_mind(by_id, orders, catalog):
    r, o, item, line = _ctx("RQ-302", by_id, orders, catalog)
    assert "t-shirt" in item.name.lower() and item.category == "apparel"
    assert r.reason_code == "changed_mind" and not r.has_statement
    assert r.label.responsible_party == "customer"
    assert (r.submitted - o.delivered).days <= WINDOW_DAYS


def test_rq303_cheese_perishable(by_id, orders, catalog):
    r, o, item, line = _ctx("RQ-303", by_id, orders, catalog)
    assert "cheese" in item.name.lower() and item.category == "perishable"
    assert (r.submitted - o.delivered).days <= WINDOW_DAYS


def test_rq304_jacket_47_days(by_id, orders, catalog):
    r, o, item, line = _ctx("RQ-304", by_id, orders, catalog)
    assert "jacket" in item.name.lower() and item.category == "apparel"
    assert (r.submitted - o.delivered).days == 47
    assert not r.has_statement


def test_rq305_blender(by_id, orders, catalog):
    r, o, item, line = _ctx("RQ-305", by_id, orders, catalog)
    assert "blender" in item.name.lower() and item.category == "kitchen_hard"
    assert "grinding" in r.statement.lower() and r.photo == "photos/blender-base.jpg"
    assert r.label.responsible_party == "retailer"
    assert line.paid > 75.00


def test_rq306_headphones(by_id, orders, catalog):
    r, o, item, line = _ctx("RQ-306", by_id, orders, catalog)
    assert "headphones" in item.name.lower()
    assert "never arrived" in r.statement.lower() and o.delivered is not None
    assert r.photo == "photos/headphones-sealed.jpg"
    assert r.label.responsible_party == "unclear"


def test_rq307_mixer(by_id, orders, catalog):
    r, o, item, line = _ctx("RQ-307", by_id, orders, catalog)
    assert "mixer" in item.name.lower() and item.category == "kitchen_hard"
    assert line.paid == 140.00 and r.photo == "photos/mixer-crushed.jpg"
    assert r.label.responsible_party == "carrier"


def test_refund_items_in_stock_for_replace(by_id, catalog):
    """The demo's money dispositions on kettle and mixer can pick replace."""
    assert catalog[by_id["RQ-301"].sku].in_stock > 0
    assert catalog[by_id["RQ-307"].sku].in_stock > 0


def test_demo_rows_are_in_demo_script_order(requests):
    """Sketch section 9: RQ-303, RQ-304, RQ-302, RQ-301, RQ-307, RQ-305, RQ-306."""
    assert [r.request_id for r in requests if r.demo] == [
        "RQ-303", "RQ-304", "RQ-302", "RQ-301", "RQ-307", "RQ-305", "RQ-306"]


def test_every_referenced_photo_exists(requests):
    data = Path(__file__).resolve().parents[1] / "data"
    missing = sorted({r.photo for r in requests if r.photo and not (data / r.photo).is_file()})
    assert missing == []
