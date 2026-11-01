"""Costing a call, or declining to."""

from datetime import date
from pathlib import Path

import pytest

from lakmus.errors import TargetConfigError
from lakmus.targets import pricing

TABLE = """\
[[model]]
adapter = "example"
model = "model-a"
input_per_mtok_usd = 2.0
output_per_mtok_usd = 10.0
as_of = 2026-10-01
source = "https://example.com/pricing"
"""


@pytest.fixture
def table(tmp_path: Path) -> pricing.PricingTable:
    path = tmp_path / "pricing.toml"
    path.write_text(TABLE, encoding="utf-8")
    return pricing.load(path)


def test_the_bundled_table_ships_empty_on_purpose() -> None:
    """A price written from memory is fiction, and a released wheel would carry it for years."""
    assert pricing.load().prices == ()


def test_a_priced_model_costs_what_the_table_says(table: pricing.PricingTable) -> None:
    priced = table.cost_usd("example", "model-a", tokens_in=1_000_000, tokens_out=500_000)

    assert priced is not None
    usd, price = priced
    assert usd == pytest.approx(2.0 + 5.0)
    assert price.as_of == date(2026, 10, 1)
    assert price.source == "https://example.com/pricing"


def test_an_unpriced_model_costs_nothing_rather_than_a_guess(
    table: pricing.PricingTable,
) -> None:
    assert table.cost_usd("example", "model-z", tokens_in=1000, tokens_out=1000) is None
    assert table.cost_usd("other", "model-a", tokens_in=1000, tokens_out=1000) is None


def test_a_price_does_not_spread_to_a_model_that_shares_a_prefix(
    table: pricing.PricingTable,
) -> None:
    """How a cost figure becomes fiction."""
    assert table.find("example", "model-a-turbo") is None


def test_a_missing_table_the_caller_asked_for_is_an_error(tmp_path: Path) -> None:
    """Someone who passed --pricing meant it; falling back to no prices would hide the typo."""
    with pytest.raises(TargetConfigError, match="no price table"):
        pricing.load(tmp_path / "absent.toml")


def test_a_malformed_table_names_what_is_wrong(tmp_path: Path) -> None:
    path = tmp_path / "pricing.toml"
    path.write_text("[[model]\nadapter = ", encoding="utf-8")

    with pytest.raises(TargetConfigError, match="not valid TOML"):
        pricing.load(path)


def test_a_price_without_provenance_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "pricing.toml"
    path.write_text(
        '[[model]]\nadapter = "example"\nmodel = "m"\n'
        "input_per_mtok_usd = 1.0\noutput_per_mtok_usd = 1.0\n",
        encoding="utf-8",
    )

    with pytest.raises(TargetConfigError):
        pricing.load(path)


def test_two_prices_for_one_model_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "pricing.toml"
    path.write_text(TABLE + TABLE, encoding="utf-8")

    with pytest.raises(TargetConfigError, match="more than one price"):
        pricing.load(path)
