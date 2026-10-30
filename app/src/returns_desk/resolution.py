"""The validator: code, no model, runs on every resolution."""
from __future__ import annotations

from .types import (
    MONEY_DISPOSITIONS, Disposition, Item, OrderLine, Resolution, Resolver,
    ResponsibilityRow, ValidationError,
)


def compute(row: ResponsibilityRow, line: OrderLine, disposition: Disposition,
            partial_amount: float | None = None) -> tuple[float, float]:
    """Return (amount, fee) in USD."""
    if disposition == Disposition.REFUND:
        fee = round(line.paid * row.fee, 2)
        return round(line.paid - fee, 2), fee
    if disposition == Disposition.PARTIAL:
        return float(partial_amount or 0.0), 0.0
    return 0.0, 0.0


def validate(resolution: Resolution, row: ResponsibilityRow, line: OrderLine, item: Item) -> None:
    d = resolution.disposition
    if d in MONEY_DISPOSITIONS and d not in row.dispositions:
        raise ValidationError("disposition", f"{d.value} is not allowed for this category and party")
    if d == Disposition.REFUND:
        expected, fee = compute(row, line, d)
        if round(resolution.amount, 2) != expected:
            raise ValidationError("refund_amount", f"refund must be {expected:.2f}, got {resolution.amount:.2f}")
        if round(resolution.fee, 2) != fee:
            raise ValidationError("fee", f"fee must be {fee:.2f}, got {resolution.fee:.2f}")
    if d == Disposition.PARTIAL:
        if not 0 < resolution.amount < line.paid:
            raise ValidationError("partial_range", f"partial must be above 0 and below {line.paid:.2f}")
        if resolution.resolver != Resolver.CLERK:
            raise ValidationError("partial_clerk_only", "only the clerk may choose a partial")
    if d == Disposition.REPLACE and item.in_stock == 0:
        raise ValidationError("in_stock", f"{item.sku} is out of stock")
    if d in (Disposition.DENY, Disposition.ESCALATE) and not resolution.note.strip():
        raise ValidationError("note", f"{d.value} needs a note")
    if resolution.amount > line.paid:
        raise ValidationError("exceeds_paid", f"amount {resolution.amount:.2f} exceeds paid {line.paid:.2f}")
    if d in (Disposition.REPLACE, Disposition.DENY, Disposition.ESCALATE) and resolution.amount != 0:
        raise ValidationError("amount_zero", f"{d.value} carries no refund, got {resolution.amount:.2f}")
    if d != Disposition.REFUND and resolution.fee != 0:
        raise ValidationError("fee", f"only a refund withholds a fee, got {resolution.fee:.2f} on {d.value}")
    postage = row.return_postage if d in MONEY_DISPOSITIONS else "none"
    if resolution.return_postage != postage:
        raise ValidationError("return_postage",
                              f"return postage for {d.value} must be {postage}, got {resolution.return_postage}")
