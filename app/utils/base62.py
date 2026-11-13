"""Encode IDs with the project's fixed, shuffled Base62 alphabet."""

# Keep this ordering stable: changing it changes the code assigned to an ID.
# This is obfuscation, not encryption or protection against enumeration.
ALPHABET = "7lIfxpnyDtXUVEhQ2ZWbwdGmOAvogHK6rucCTSY4NP5zR03qB9isjkJLeF8Ma1"

if len(ALPHABET) != 62 or len(set(ALPHABET)) != 62:
    raise ValueError("Base62 alphabet must contain 62 distinct characters")


def encode_base62(number: int) -> str:
    """Encode a non-negative integer without truncation or collisions."""
    if isinstance(number, bool) or not isinstance(number, int):
        raise TypeError("number must be an integer")
    if number < 0:
        raise ValueError("number must be non-negative")
    if number == 0:
        return ALPHABET[0]

    characters = []
    while number > 0:
        number, remainder = divmod(number, len(ALPHABET))
        characters.append(ALPHABET[remainder])
    return "".join(reversed(characters))
