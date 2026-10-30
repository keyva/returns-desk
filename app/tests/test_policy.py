"""Tests for the rote layer: loaders and JsonPolicy. Fixtures are built here."""
import json
from datetime import date
from pathlib import Path

import pytest

from returns_desk.catalog import load_catalog
from returns_desk.orders import load_orders, load_requests
from returns_desk.policy import JsonPolicy
from returns_desk.types import (
    Disposition, Item, Order, OrderLine, Request, ResponsibleParty,
)

POLICY_PATH = Path(__file__).resolve().parent.parent / "data" / "policy.json"
TODAY = date(2026, 10, 30)
DELIVERED = date(2026, 10, 5)


@pytest.fixture
def policy():
    return JsonPolicy.from_file(POLICY_PATH)


def mk(paid=64.0, sku="KET-200", category="kitchen_hard", delivered=DELIVERED,
       submitted=date(2026, 10, 14), photo="photos/x.jpg", reason_code=None, statement=""):
    order = Order("R-1", "c_1", date(2026, 10, 2), delivered, (OrderLine(sku, 1, paid),))
    item = Item(sku, "Thing", category, 5)
    req = Request("RQ-1", "R-1", sku, submitted, reason_code=reason_code, statement=statement, photo=photo)
    return req, order, item


def assess(policy, req, order, item, n=0):
    return policy.assess(req, order, item, n, TODAY)


# ------------------------------------------------------------------ loaders

def test_load_catalog(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps([{"sku": "A", "name": "Ay", "category": "apparel", "in_stock": 3}]))
    cat = load_catalog(p)
    assert cat == {"A": Item("A", "Ay", "apparel", 3)}


def test_load_orders_parses_dates(tmp_path):
    p = tmp_path / "o.json"
    p.write_text(json.dumps([
        {"order_id": "R-1", "placed": "2026-10-02", "delivered": "2026-10-05", "customer_id": "c_9",
         "lines": [{"sku": "A", "qty": 2, "paid": 64.0}]},
        {"order_id": "R-2", "placed": "2026-10-03", "delivered": None, "customer_id": "c_9", "lines": []},
    ]))
    orders = load_orders(p)
    o = orders["R-1"]
    assert o.placed == date(2026, 10, 2) and o.delivered == date(2026, 10, 5)
    assert o.customer_id == "c_9"
    assert o.line("A") == OrderLine("A", 2, 64.0)
    assert orders["R-2"].delivered is None


def test_load_orders_accepts_the_sketch_customer_key(tmp_path):
    p = tmp_path / "o.json"
    p.write_text(json.dumps([{"order_id": "R-1", "placed": "2026-10-02", "delivered": None,
                              "customer": "c_9", "lines": []}]))
    assert load_orders(p)["R-1"].customer_id == "c_9"


def test_load_orders_reads_the_shipped_orders_file():
    from conftest import DATA
    orders = load_orders(DATA / "orders.json")
    assert orders["R-2041"].customer_id == "c_118"


def test_there_is_one_orders_loader():
    import returns_desk.desk as desk
    from returns_desk import orders
    assert desk.load_orders is orders.load_orders


def test_load_requests_label_and_demo(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps([
        {"request_id": "RQ-301", "order_id": "R-1", "sku": "A", "submitted": "2026-10-14",
         "reason_code": None, "statement": "Crushed.", "photo": "photos/rq-301.jpg",
         "label": {"responsible_party": "carrier", "source": "author"}, "demo": True},
        {"request_id": "RQ-302", "order_id": "R-1", "sku": "A", "submitted": "2026-10-15",
         "reason_code": "changed_mind"},
    ]))
    reqs = load_requests(p)
    a, b = reqs
    assert a.submitted == date(2026, 10, 14) and a.demo is True
    assert a.label.responsible_party is ResponsibleParty.CARRIER and a.label.source == "author"
    assert a.has_statement
    assert b.label is None and b.demo is False and b.statement == "" and b.photo is None
    assert not b.has_statement


# ------------------------------------------------------------------ assess

def test_passing_request(policy):
    a = assess(policy, *mk())
    assert a.passes and a.denial_rule is None and a.denial_reply is None
    assert a.line.paid == 64.0 and a.item.sku == "KET-200"


def test_denies_unknown_order(policy):
    req, _, item = mk()
    a = assess(policy, req, None, item)
    assert not a.passes and a.denial_rule == "order"


def test_denies_item_not_on_order(policy):
    req, order, item = mk()
    other = Item("ZZZ", "Other", "kitchen_hard", 1)
    req2 = Request("RQ-2", "R-1", "ZZZ", req.submitted)
    a = assess(policy, req2, order, other)
    assert a.denial_rule == "order"


def test_denies_unknown_item(policy):
    req, order, _ = mk()
    assert assess(policy, req, order, None).denial_rule == "order"


def test_denies_non_returnable_category(policy):
    for cat in ("perishable", "final_sale"):
        a = assess(policy, *mk(category=cat))
        assert a.denial_rule == "category" and not a.passes


def test_denies_unknown_category(policy):
    assert assess(policy, *mk(category="mystery")).denial_rule == "category"


def test_window_boundary_30_days_inside(policy):
    a = assess(policy, *mk(submitted=date(2026, 11, 4)))  # 30 days after Oct 5
    assert a.passes


def test_window_31_days_denied(policy):
    a = assess(policy, *mk(submitted=date(2026, 11, 5)))
    assert a.denial_rule == "window"


def test_window_47_days(policy):
    a = assess(policy, *mk(submitted=date(2026, 11, 21)))
    assert a.denial_rule == "window"


def test_missing_delivered_goes_to_the_clerk(policy):
    """No delivery date: the window cannot be counted and a lost parcel cannot be photographed,
    so policy neither denies nor maps it; it sends it to the clerk."""
    a = assess(policy, *mk(delivered=None, photo=None, statement="It never arrived."))
    assert a.passes and a.clerk_rule == "no_delivery_date"


def test_missing_delivered_is_not_mapped_from_a_reason_code(policy):
    a = assess(policy, *mk(delivered=None, photo=None, reason_code="changed_mind"))
    assert a.passes and a.clerk_rule == "no_delivery_date" and a.mapped_party is None


def test_missing_delivered_still_checks_the_cap(policy):
    assert assess(policy, *mk(delivered=None), n=3).denial_rule == "cap"


def test_delivered_request_has_no_clerk_rule(policy):
    assert assess(policy, *mk()).clerk_rule is None


def test_photo_at_threshold_not_required(policy):
    a = assess(policy, *mk(paid=40.0, photo=None))
    assert a.passes


def test_photo_above_threshold_required(policy):
    a = assess(policy, *mk(paid=40.01, photo=None))
    assert a.denial_rule == "photo_required"


def test_photo_present_passes(policy):
    assert assess(policy, *mk(paid=64.0, photo="photos/a.jpg")).passes


def test_photo_not_required_when_the_reason_code_puts_the_cost_on_the_customer(policy):
    """A photo is evidence for putting the cost on the retailer or the carrier. Changed mind and
    wrong size put it on the customer, so there is nothing for a photo to prove."""
    for code in ("changed_mind", "wrong_size"):
        a = assess(policy, *mk(paid=64.0, photo=None, reason_code=code))
        assert a.passes and a.mapped_party == ResponsibleParty.CUSTOMER


def test_photo_still_required_when_the_reason_code_puts_the_cost_on_the_shop(policy):
    assert assess(policy, *mk(paid=64.0, photo=None, reason_code="wrong_item")).denial_rule == "photo_required"


def test_photo_still_required_with_a_statement_and_a_customer_reason_code(policy):
    req = mk(paid=64.0, photo=None, reason_code="changed_mind", statement="It broke on day one.")
    assert assess(policy, *req).denial_rule == "photo_required"


def test_photo_threshold_is_per_category(policy):
    assert assess(policy, *mk(paid=100.0, category="apparel", photo=None)).passes
    assert assess(policy, *mk(paid=100.01, category="apparel", photo=None)).denial_rule == "photo_required"


def test_cap_below_passes(policy):
    assert assess(policy, *mk(), n=2).passes


def test_cap_reached_denied(policy):
    a = assess(policy, *mk(), n=3)
    assert a.denial_rule == "cap" and a.returns_this_quarter == 3


def test_returns_this_quarter_recorded(policy):
    assert assess(policy, *mk(), n=1).returns_this_quarter == 1


def test_order_checked_before_category(policy):
    req, order, item = mk(category="perishable")
    assert assess(policy, req, None, item).denial_rule == "order"


def test_category_checked_before_window(policy):
    a = assess(policy, *mk(category="perishable", submitted=date(2026, 12, 31)))
    assert a.denial_rule == "category"


def test_window_checked_before_photo(policy):
    a = assess(policy, *mk(paid=64.0, photo=None, submitted=date(2026, 12, 31)))
    assert a.denial_rule == "window"


def test_photo_checked_before_cap(policy):
    assert assess(policy, *mk(paid=64.0, photo=None), n=5).denial_rule == "photo_required"


@pytest.mark.parametrize("kw,rule", [
    ({"category": "perishable"}, "category"),
    ({"submitted": date(2026, 12, 1)}, "window"),
    ({"photo": None, "paid": 90.0}, "photo_required"),
])
def test_denial_reply_cites_rule(policy, kw, rule):
    a = assess(policy, *mk(**kw))
    assert a.denial_rule == rule
    assert a.denial_reply and len(a.denial_reply) < 400
    assert rule.replace("_", " ") in a.denial_reply.lower() or rule in a.denial_reply.lower()


def test_cap_and_order_replies_present(policy):
    req, order, item = mk()
    assert assess(policy, req, None, item).denial_reply
    assert assess(policy, req, order, item, n=3).denial_reply


def test_window_reply_mentions_days(policy):
    a = assess(policy, *mk(submitted=date(2026, 12, 1)))
    assert "30" in a.denial_reply


def test_no_banned_vocabulary_in_replies(policy):
    for kw in ({"category": "perishable"}, {"submitted": date(2026, 12, 1)}):
        text = assess(policy, *mk(**kw)).denial_reply.lower()
        assert "fault" not in text and "outcome" not in text


# ------------------------------------------------------------------ reason code mapping

def test_reason_code_maps_party(policy):
    a = assess(policy, *mk(reason_code="changed_mind", photo=None, paid=20.0))
    assert a.passes and a.mapped_party is ResponsibleParty.CUSTOMER


def test_reason_code_wrong_item_shop(policy):
    a = assess(policy, *mk(reason_code="wrong_item", photo=None, paid=20.0))
    assert a.mapped_party is ResponsibleParty.RETAILER


def test_statement_suppresses_mapping(policy):
    a = assess(policy, *mk(reason_code="changed_mind", statement="It broke."))
    assert a.passes and a.mapped_party is None


def test_photo_alone_is_a_statement(policy):
    a = assess(policy, *mk(reason_code="changed_mind", photo="photos/a.jpg"))
    assert a.mapped_party is None


def test_no_reason_code_no_mapping(policy):
    assert assess(policy, *mk()).mapped_party is None


def test_denied_request_has_no_mapping(policy):
    a = assess(policy, *mk(reason_code="changed_mind", category="perishable"))
    assert a.mapped_party is None


def test_reason_code_party_lookup(policy):
    assert policy.reason_code_party("wrong_size") is ResponsibleParty.CUSTOMER
    assert policy.reason_code_party("nonsense") is None


# ------------------------------------------------------------------ tables

def test_responsibility_row_kitchen_customer(policy):
    row = policy.responsibility_row("kitchen_hard", ResponsibleParty.CUSTOMER)
    assert row.dispositions == frozenset({Disposition.REFUND, Disposition.PARTIAL})
    assert row.fee == 0.10 and row.return_postage == "customer"


def test_responsibility_row_carrier(policy):
    row = policy.responsibility_row("kitchen_hard", ResponsibleParty.CARRIER)
    assert row.dispositions == frozenset({Disposition.REFUND, Disposition.REPLACE})
    assert row.return_postage == "none"


def test_unclear_row_empty(policy):
    row = policy.responsibility_row("apparel", ResponsibleParty.UNCLEAR)
    assert row.dispositions == frozenset()


def test_apparel_customer_refund_only(policy):
    row = policy.responsibility_row("apparel", ResponsibleParty.CUSTOMER)
    assert row.dispositions == frozenset({Disposition.REFUND}) and row.fee == 0.0


def test_non_returnable_category_row_is_empty(policy):
    assert policy.responsibility_row("perishable", ResponsibleParty.RETAILER).dispositions == frozenset()
    assert policy.responsibility_row("mystery", ResponsibleParty.RETAILER).dispositions == frozenset()


def test_auto_approval(policy):
    aa = policy.auto_approval()
    assert aa.require_agreement is True and aa.min_probability == 0.80 and aa.max_amount == 75.0
    assert aa.block_on_flags == frozenset({"possible_abuse", "used"})


def test_policy_does_not_import_an_engine():
    import returns_desk.policy as mod
    src = Path(mod.__file__).read_text()
    assert "returns_desk.model" not in src and "from . import model" not in src and "import model" not in src
