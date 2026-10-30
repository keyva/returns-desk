"""Tests for compute() and validate()."""
import pytest

from returns_desk.resolution import compute, validate
from returns_desk.types import (
    Disposition, Item, OrderLine, Resolution, Resolver, ResponsibilityRow,
    ResponsibleParty, ValidationError,
)

D = Disposition
ROW = ResponsibilityRow(frozenset({D.REFUND, D.PARTIAL}), fee=0.10, return_postage="customer")
ROW_FREE = ResponsibilityRow(frozenset({D.REFUND, D.REPLACE}), fee=0.0, return_postage="none")
EMPTY = ResponsibilityRow(frozenset())
LINE = OrderLine("KET-200", 1, 64.00)
STOCKED = Item("KET-200", "Kettle", "kitchen_hard", 7)
SOLD_OUT = Item("KET-200", "Kettle", "kitchen_hard", 0)


def res(disposition, amount, fee=0.0, resolver=Resolver.CLERK, note="n", party=ResponsibleParty.CARRIER,
        postage="none"):
    return Resolution("RQ-1", resolver, party, disposition, amount, fee, postage, note)


def rule_of(resolution, row=ROW_FREE, line=LINE, item=STOCKED):
    with pytest.raises(ValidationError) as e:
        validate(resolution, row, line, item)
    return e.value.rule


# ------------------------------------------------------------------ compute

def test_compute_refund_no_fee():
    assert compute(ROW_FREE, LINE, D.REFUND) == (64.00, 0.0)


def test_compute_refund_with_fee():
    assert compute(ROW, LINE, D.REFUND) == (57.60, 6.40)


def test_compute_rounds_to_cents():
    row = ResponsibilityRow(frozenset({D.REFUND}), fee=0.10)
    amount, fee = compute(row, OrderLine("X", 1, 33.33), D.REFUND)
    assert fee == 3.33 and amount == 30.00


def test_compute_replace_deny_escalate_zero():
    for d in (D.REPLACE, D.DENY, D.ESCALATE):
        assert compute(ROW, LINE, d) == (0.0, 0.0)


def test_compute_partial_given_amount():
    assert compute(ROW, LINE, D.PARTIAL, 30.0) == (30.0, 0.0)


# ------------------------------------------------------------------ validate: accepts

def test_valid_refund():
    validate(res(D.REFUND, 64.00), ROW_FREE, LINE, STOCKED)


def test_valid_refund_with_fee():
    validate(res(D.REFUND, 57.60, 6.40, postage="customer"), ROW, LINE, STOCKED)


def test_valid_replace_in_stock():
    validate(res(D.REPLACE, 0.0), ROW_FREE, LINE, STOCKED)


def test_valid_partial_by_clerk():
    validate(res(D.PARTIAL, 30.0, postage="customer"), ROW, LINE, STOCKED)


def test_deny_and_escalate_allowed_with_empty_row():
    validate(res(D.DENY, 0.0, note="No."), EMPTY, LINE, STOCKED)
    validate(res(D.ESCALATE, 0.0, note="Up."), EMPTY, LINE, STOCKED)


# ------------------------------------------------------------------ validate: rejects

def test_rule_disposition_not_allowed():
    assert rule_of(res(D.REPLACE, 0.0), row=ROW) == "disposition"


def test_rule_refund_on_empty_row():
    assert rule_of(res(D.REFUND, 64.0), row=EMPTY) == "disposition"


def test_rule_refund_amount_mismatch():
    assert rule_of(res(D.REFUND, 60.0)) == "refund_amount"


def test_rule_refund_amount_must_match_compute_whatever_the_fee():
    assert rule_of(res(D.REFUND, 64.00, postage="customer"), row=ROW) == "refund_amount"


@pytest.mark.parametrize("amount", [0.0, -5.0])
def test_rule_partial_not_positive(amount):
    assert rule_of(res(D.PARTIAL, amount), row=ROW) == "partial_range"


def test_rule_partial_equal_to_paid():
    assert rule_of(res(D.PARTIAL, 64.0), row=ROW) == "partial_range"


def test_rule_partial_above_paid_is_range_first():
    assert rule_of(res(D.PARTIAL, 80.0), row=ROW) == "partial_range"


def test_rule_partial_only_clerk():
    for r in (Resolver.POLICY, Resolver.ENGINES):
        assert rule_of(res(D.PARTIAL, 30.0, resolver=r), row=ROW) == "partial_clerk_only"


def test_rule_replace_out_of_stock():
    assert rule_of(res(D.REPLACE, 0.0), item=SOLD_OUT) == "in_stock"


@pytest.mark.parametrize("d", [D.DENY, D.ESCALATE])
@pytest.mark.parametrize("note", ["", "   "])
def test_rule_empty_note(d, note):
    assert rule_of(res(d, 0.0, note=note)) == "note"


def test_rule_exceeds_paid_is_its_own_rule():
    # A deny carrying money passes every other rule, so only the final check catches it.
    assert rule_of(res(D.DENY, 70.0, note="x"), row=EMPTY) == "exceeds_paid"
    assert rule_of(res(D.ESCALATE, 64.01, note="x"), row=EMPTY) == "exceeds_paid"


def test_rule_exceeds_paid_checked_last():
    # refund over paid on a free row: mismatch is reported first
    assert rule_of(res(D.REFUND, 70.0)) == "refund_amount"


def test_validation_error_str_names_rule():
    with pytest.raises(ValidationError) as e:
        validate(res(D.REFUND, 1.0), ROW_FREE, LINE, STOCKED)
    assert str(e.value).startswith("refund_amount")


# ------------------------------------------------------------------ validate: fee, zero amounts, postage

def test_rule_refund_fee_must_match_compute():
    # right amount, wrong fee recorded
    assert rule_of(res(D.REFUND, 57.60, 0.0, postage="customer"), row=ROW) == "fee"
    assert rule_of(res(D.REFUND, 64.00, 6.40)) == "fee"


@pytest.mark.parametrize("d,amount,row", [(D.PARTIAL, 30.0, ROW), (D.REPLACE, 0.0, ROW_FREE)])
def test_rule_fee_zero_for_partial_and_replace(d, amount, row):
    postage = row.return_postage
    assert rule_of(res(d, amount, 5.0, postage=postage), row=row) == "fee"


@pytest.mark.parametrize("d", [D.DENY, D.ESCALATE])
def test_rule_fee_zero_for_clerk_exits(d):
    assert rule_of(res(d, 0.0, 5.0, note="x"), row=EMPTY) == "fee"


@pytest.mark.parametrize("d", [D.REPLACE, D.DENY, D.ESCALATE])
def test_rule_amount_zero_for_replace_deny_escalate(d):
    row = ROW_FREE if d == D.REPLACE else EMPTY
    assert rule_of(res(d, 40.0, note="x"), row=row) == "amount_zero"


@pytest.mark.parametrize("d,amount,fee,row,wrong", [
    (D.REFUND, 57.60, 6.40, ROW, "retailer"),
    (D.PARTIAL, 30.0, 0.0, ROW, "none"),
    (D.REFUND, 64.00, 0.0, ROW_FREE, "customer"),
    (D.REPLACE, 0.0, 0.0, ROW_FREE, "retailer"),
])
def test_rule_return_postage_follows_the_row_for_money(d, amount, fee, row, wrong):
    assert rule_of(res(d, amount, fee, postage=wrong), row=row) == "return_postage"


@pytest.mark.parametrize("d", [D.DENY, D.ESCALATE])
def test_rule_return_postage_none_for_clerk_exits(d):
    assert rule_of(res(d, 0.0, note="x", postage="customer"), row=ROW) == "return_postage"
