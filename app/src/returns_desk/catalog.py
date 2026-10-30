"""Load the catalog: items with category and stock."""
from __future__ import annotations

import json
from pathlib import Path

from .types import Item


def load_catalog(path: str | Path) -> dict[str, Item]:
    rows = json.loads(Path(path).read_text())
    if isinstance(rows, dict):
        rows = rows.get("items", [])
    return {r["sku"]: Item(r["sku"], r["name"], r["category"], int(r["in_stock"])) for r in rows}
