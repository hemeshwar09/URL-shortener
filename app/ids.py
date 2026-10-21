"""Base62 encoding: turns a unique DB row id into a short, collision-free code."""
ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
OFFSET = 100_000  # keeps early codes at a reasonable minimum length


def encode(n: int) -> str:
    n += OFFSET
    out = []
    while n:
        n, r = divmod(n, 62)
        out.append(ALPHABET[r])
    return "".join(reversed(out))


def decode(code: str) -> int:
    n = 0
    for ch in code:
        n = n * 62 + ALPHABET.index(ch)
    return n - OFFSET
