"""Unit tests need no running database."""

import pytest

from app.utils.base62 import ALPHABET, encode_base62


@pytest.mark.parametrize("number, expected", [
    (0, "7"), (1, "l"), (61, "1"), (62, "l7"), (64, "lI"),
])
def test_known_encodings(number, expected):
    assert encode_base62(number) == expected


def test_codes_are_unique_and_round_trip():
    numbers = [*range(200), 62**2 - 1, 62**2, 2**63 - 1]
    codes = [encode_base62(number) for number in numbers]
    assert len(set(codes)) == len(numbers)
    for number, code in zip(numbers, codes):
        decoded = 0
        for character in code:
            decoded = decoded * 62 + ALPHABET.index(character)
        assert decoded == number
        assert len(code) <= 11


@pytest.mark.parametrize("value", [True, 1.5, "12", None])
def test_non_integer_rejected(value):
    with pytest.raises(TypeError):
        encode_base62(value)


def test_negative_integer_rejected():
    with pytest.raises(ValueError):
        encode_base62(-1)
