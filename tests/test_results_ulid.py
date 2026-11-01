"""ULID encoding.

A run id has to sort chronologically and be reproducible from a fixed clock and seed, because a
test that cannot pin an id cannot check that the clock was injected rather than read.
"""

from datetime import UTC, datetime
from random import Random

import pytest

from lakmus.results import ulid

FIXED_MOMENT = datetime(2026, 11, 1, 12, 34, 56, 789000, tzinfo=UTC)


def test_the_alphabet_excludes_the_letters_people_misread() -> None:
    assert len(ulid.ALPHABET) == 32
    assert set("ILOU").isdisjoint(ulid.ALPHABET)


def test_the_smallest_ulid_is_all_zeroes() -> None:
    assert ulid.encode(0, 0) == "0" * ulid.LENGTH


def test_the_largest_ulid_fills_every_bit_it_has() -> None:
    """26 base32 characters hold 130 bits, so the top two of the first character are unused."""
    largest = ulid.encode((1 << 48) - 1, (1 << 80) - 1)

    assert largest == "7" + "Z" * 25


@pytest.mark.parametrize(
    ("timestamp_ms", "randomness"),
    [
        pytest.param(-1, 0, id="negative-timestamp"),
        pytest.param(1 << 48, 0, id="timestamp-too-large"),
        pytest.param(0, -1, id="negative-randomness"),
        pytest.param(0, 1 << 80, id="randomness-too-large"),
    ],
)
def test_out_of_range_values_are_refused(timestamp_ms: int, randomness: int) -> None:
    with pytest.raises(ValueError):
        ulid.encode(timestamp_ms, randomness)


def test_a_ulid_carries_the_instant_it_was_made_at() -> None:
    identifier = ulid.generate(lambda: FIXED_MOMENT, Random(42))

    assert ulid.timestamp_of(identifier) == FIXED_MOMENT


def test_the_same_clock_and_seed_give_the_same_id() -> None:
    first = ulid.generate(lambda: FIXED_MOMENT, Random(42))
    second = ulid.generate(lambda: FIXED_MOMENT, Random(42))

    assert first == second


def test_a_later_run_sorts_after_an_earlier_one() -> None:
    """What makes a directory of runs readable without opening any of them."""
    earlier = ulid.generate(lambda: FIXED_MOMENT, Random(1))
    later = ulid.generate(lambda: FIXED_MOMENT.replace(second=57), Random(1))

    assert earlier < later


@pytest.mark.parametrize("bad", ["", "short", "0" * 25, "0" * 27, "I" * 26, "U" * 26])
def test_decoding_refuses_what_is_not_a_ulid(bad: str) -> None:
    with pytest.raises(ValueError):
        ulid.timestamp_of(bad)


GOLDEN_ID = "01M6BHP54N0SKHS01HFYHV2YCX"
"""A fixed clock and a fixed seed give a fixed id. Pinned so that a clock or a random source read
from a global, rather than taken as an argument, fails here."""


def test_a_fixed_clock_and_seed_give_the_pinned_id() -> None:
    assert ulid.generate(lambda: FIXED_MOMENT, Random(42)) == GOLDEN_ID


def test_a_naive_clock_is_refused() -> None:
    """Otherwise a run id would depend on the timezone of the machine that produced it."""
    naive = datetime(2026, 11, 1, 12, 34, 56)

    with pytest.raises(ValueError, match="aware datetime"):
        ulid.generate(lambda: naive, Random(42))
