"""Turning token counts into a cost, or declining to.

A price is only meaningful with the date it was true on: providers change prices under an unchanged
model name, so a figure without a date is not reproducible. Matching is exact on `(adapter,
model)`. There is no prefix matching and no inheritance, because a price that silently applies to a
model it was not written for is how a cost figure becomes fiction.

A model that is not in the table costs `None`. Never an estimate.
"""

import tomllib
from datetime import date
from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from lakmus.errors import TargetConfigError

BUNDLED_PATH = Path(__file__).parent / "pricing.toml"

_PER_MILLION = 1_000_000


class ModelPrice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    adapter: str
    model: str
    input_per_mtok_usd: Annotated[float, Field(ge=0.0)]
    output_per_mtok_usd: Annotated[float, Field(ge=0.0)]
    as_of: date
    source: str
    """Where the figure came from. A price with no citable source is not usable."""


class PricingTable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    prices: tuple[ModelPrice, ...] = ()

    @model_validator(mode="after")
    def _one_price_per_model(self) -> Self:
        seen: set[tuple[str, str]] = set()
        duplicates: list[str] = []
        for price in self.prices:
            key = (price.adapter, price.model)
            if key in seen:
                duplicates.append(f"{price.adapter}:{price.model}")
            seen.add(key)
        if duplicates:
            raise ValueError(f"more than one price for {', '.join(sorted(duplicates))}")
        return self

    def find(self, adapter: str, model: str) -> ModelPrice | None:
        """Exact match only."""
        for price in self.prices:
            if price.adapter == adapter and price.model == model:
                return price
        return None

    def cost_usd(
        self, adapter: str, model: str, *, tokens_in: int, tokens_out: int
    ) -> tuple[float, ModelPrice] | None:
        """The cost of one call and the price that produced it, or `None` when unpriced."""
        price = self.find(adapter, model)
        if price is None:
            return None
        usd = (
            tokens_in * price.input_per_mtok_usd + tokens_out * price.output_per_mtok_usd
        ) / _PER_MILLION
        return usd, price


def load(path: Path | None = None) -> PricingTable:
    """Read a price table, defaulting to the one bundled with the package.

    A malformed table is a configuration error naming what is wrong, not a silent fallback to no
    prices: someone who passed `--pricing` meant it.
    """
    resolved = path or BUNDLED_PATH
    if not resolved.is_file():
        if path is None:
            return PricingTable()
        raise TargetConfigError(f"no price table at {resolved}")
    try:
        document = tomllib.loads(resolved.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise TargetConfigError(f"{resolved}: not valid TOML: {exc}") from exc
    try:
        return PricingTable(prices=tuple(document.get("model", ())))
    except ValidationError as exc:
        raise TargetConfigError(f"{resolved}: {exc.error_count()} problem(s): {exc}") from exc
