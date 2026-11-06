"""Price sheet loading and deterministic pricing.

All money math lives here, in plain Python. The LLM never computes or chooses a price.

Pricing rules (derived from the sheet columns):
- `price_sar` is the price of one deliverable and includes `usage_days_included` days of usage.
- Every additional started 30-day block adds `extra_30_days_pct` % of the base price.
- A discount is allowed up to `max_discount_pct` for that package; above it a human must approve.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from src.config import config


@dataclass(frozen=True)
class Package:
    code: str
    platform: str
    format: str
    creator_tier: str
    price_sar: float
    usage_days_included: int
    extra_30_days_pct: float
    max_discount_pct: float

    @property
    def tier(self) -> str:
        """Tier letter, e.g. 'A' from 'Tier A (10M+ followers)'."""
        return self.creator_tier.split()[1] if self.creator_tier.lower().startswith("tier") else self.creator_tier


@dataclass(frozen=True)
class PriceBreakdown:
    package: Package
    duration_days: int
    base_price: float
    extra_blocks: int
    extension_amount: float
    list_price: float
    discount_pct: float
    discount_amount: float
    final_price: float

    def as_dict(self) -> dict:
        p = self.package
        return {
            "code": p.code,
            "platform": p.platform,
            "format": p.format,
            "creator_tier": p.creator_tier,
            "duration_days": self.duration_days,
            "usage_days_included": p.usage_days_included,
            "base_price": self.base_price,
            "extra_30_day_blocks": self.extra_blocks,
            "extra_30_days_pct": p.extra_30_days_pct,
            "extension_amount": self.extension_amount,
            "list_price": self.list_price,
            "discount_pct": self.discount_pct,
            "discount_amount": self.discount_amount,
            "final_price": self.final_price,
            "max_discount_pct": p.max_discount_pct,
            "currency": config.CURRENCY,
        }


@lru_cache
def load_sheet(path: Path | None = None) -> tuple[Package, ...]:
    path = path or config.PRICE_SHEET_PATH
    with open(path, encoding="utf-8-sig", newline="") as f:  # utf-8-sig strips the BOM
        return tuple(
            Package(
                code=r["code"].strip(),
                platform=r["platform"].strip(),
                format=r["format"].strip(),
                creator_tier=r["creator_tier"].strip(),
                price_sar=float(r["price_sar"]),
                usage_days_included=int(r["usage_days_included"]),
                extra_30_days_pct=float(r["extra_30_days_pct"]),
                max_discount_pct=float(r["max_discount_pct"]),
            )
            for r in csv.DictReader(f)
        )


def platforms() -> list[str]:
    return sorted({p.platform for p in load_sheet()})


def formats(platform: str | None = None) -> list[str]:
    return sorted({p.format for p in load_sheet() if platform is None or p.platform == platform})


def candidates(platform: str, fmt: str | None = None, tier: str | None = None) -> list[Package]:
    return [
        p
        for p in load_sheet()
        if p.platform == platform and (fmt is None or p.format == fmt) and (tier is None or p.tier == tier)
    ]


def price(package: Package, duration_days: int, discount_pct: float = 0.0) -> PriceBreakdown:
    extra_days = max(0, duration_days - package.usage_days_included)
    extra_blocks = math.ceil(extra_days / 30)
    extension = round(package.price_sar * package.extra_30_days_pct / 100 * extra_blocks, 2)
    list_price = round(package.price_sar + extension, 2)
    discount_amount = round(list_price * discount_pct / 100, 2)
    return PriceBreakdown(
        package=package,
        duration_days=duration_days,
        base_price=package.price_sar,
        extra_blocks=extra_blocks,
        extension_amount=extension,
        list_price=list_price,
        discount_pct=discount_pct,
        discount_amount=discount_amount,
        final_price=round(list_price - discount_amount, 2),
    )


def select_package(
    platform: str, duration_days: int, budget: float, fmt: str | None = None, tier: str | None = None
) -> tuple[Package | None, bool]:
    """Pick the package to quote.

    Returns (package, fits_budget). Among matching packages, choose the most valuable one whose
    list price fits the budget; if none fits, return the cheapest so the customer sees the gap.
    """
    matches = candidates(platform, fmt, tier)
    if not matches:
        return None, False
    priced = sorted(((price(p, duration_days).list_price, p) for p in matches), key=lambda x: x[0])
    fitting = [p for lp, p in priced if lp <= budget]
    if fitting:
        return fitting[-1], True
    return priced[0][1], False
