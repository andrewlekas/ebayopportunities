"""Text normalization shared by the matchers.

Why this exists: eBay titles are full of accented characters, and the
tokenizers all split on [a-z]. "Pokémon" therefore became the two tokens
"pok" and "mon" rather than one word - which fed junk into the subject
guard, the per-listing subject injection and the duplicate collapse.
7% of the titles in the history DB (277 of 4,075) contain non-ASCII.

fold() strips accents but leaves Japanese alone, so Buyee/Yahoo titles and
the JP-native query markers keep working.
"""
from __future__ import annotations

import unicodedata


def fold(text: str) -> str:
    """'Pokémon Café' -> 'Pokemon Cafe'. Non-Latin scripts pass through.

    Only accents on LATIN letters are stripped. Japanese uses combining
    marks too - the dakuten in トップサン (Topsun) and リザードン (Charizard)
    decompose exactly like an acute accent, and blindly dropping them
    turns プ into フ, i.e. a different word. Those marks are kept and
    recomposed.
    """
    if not text:
        return ""
    out: list[str] = []
    for ch in unicodedata.normalize("NFKD", text):
        if unicodedata.combining(ch):
            prev = out[-1] if out else ""
            if prev.isascii() and prev.isalpha():
                continue            # a Latin accent - drop it
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out))
