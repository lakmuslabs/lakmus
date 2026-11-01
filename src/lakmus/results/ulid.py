"""ULID identifiers for a run.

A run id sorts chronologically, which is what makes a directory of runs readable without reading
any of them. ULID is 48 bits of millisecond timestamp followed by 80 bits of randomness, in
Crockford base32 — twenty lines against a dependency for a string.

The clock and the random source are arguments, never globals, so that a test can pin an exact id.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from random import Random

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
"""Crockford base32: no I, L, O or U, so that an id cannot be misread aloud or retyped wrong."""

LENGTH = 26
_TIMESTAMP_BITS = 48
_RANDOM_BITS = 80
_MAX_TIMESTAMP_MS = (1 << _TIMESTAMP_BITS) - 1
_MAX_RANDOM = (1 << _RANDOM_BITS) - 1

Clock = Callable[[], datetime]


def encode(timestamp_ms: int, randomness: int) -> str:
    """Encode a timestamp and 80 bits of randomness as a 26-character ULID."""
    if not 0 <= timestamp_ms <= _MAX_TIMESTAMP_MS:
        raise ValueError(f"timestamp out of range for a ULID: {timestamp_ms}")
    if not 0 <= randomness <= _MAX_RANDOM:
        raise ValueError(f"randomness out of range for a ULID: {randomness}")

    value = (timestamp_ms << _RANDOM_BITS) | randomness
    characters = []
    for _ in range(LENGTH):
        characters.append(ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(characters))


def generate(clock: Clock, entropy: Random) -> str:
    """A new ULID from an injected clock and random source."""
    moment = clock()
    if moment.tzinfo is None or moment.utcoffset() is None:
        # A naive datetime is interpreted in the machine's local zone, which would make a run id
        # depend on where it was produced.
        raise ValueError("the clock must return an aware datetime")
    timestamp_ms = int(moment.timestamp() * 1000)
    return encode(timestamp_ms, entropy.getrandbits(_RANDOM_BITS))


def timestamp_of(identifier: str) -> datetime:
    """The instant encoded in a ULID, as a UTC datetime.

    Decoding exists so that the encoding can be checked against itself, and so that a tool can
    order runs without a side file.
    """
    if len(identifier) != LENGTH:
        raise ValueError(f"not a ULID: {identifier!r}")
    value = 0
    for character in identifier:
        index = ALPHABET.find(character.upper())
        if index < 0:
            raise ValueError(f"not a ULID: {identifier!r}")
        value = (value << 5) | index
    return datetime.fromtimestamp((value >> _RANDOM_BITS) / 1000, tz=UTC)
