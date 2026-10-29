import enum
import unicodedata
import re


def normalize_search_query(text: str) -> str:
    """
    Deterministically normalizes a search query or taxonomy term for indexing/comparison:
    1. Unicode NFKC normalization.
    2. Lowercase transformation.
    3. Trimming and collapsing consecutive whitespace.
    4. Preserving meaningful technical characters (C, C++, C#, .NET, ASP.NET, Node.js, R, Go).
    """
    if not text:
        return ""

    # 1. Unicode NFKC normalization
    normalized = unicodedata.normalize("NFKC", text)

    # 2. Lowercase
    normalized = normalized.lower()

    # 3. Collapse multiple whitespace characters into single space
    normalized = re.sub(r"\s+", " ", normalized).strip()

    return normalized


def is_short_ambiguous_symbol(term: str) -> bool:
    """
    Identifies short technical symbols where exact match semantics are essential.
    """
    norm = normalize_search_query(term)
    return norm in {"c", "r", "go", "js", "ts", "c++", "c#", ".net"}
