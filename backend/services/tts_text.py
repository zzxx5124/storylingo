"""Provider-neutral text chunking for long TTS requests."""
import re


_PRIMARY_BOUNDARIES = frozenset("。！？!?；;\n")
_SECONDARY_BOUNDARIES = frozenset("，,、：:")
_CLOSING_MARKS = frozenset("」』”\"'）)]〕】》")


def split_text_for_synthesis(text: str, max_chars: int) -> list[str]:
    """Split text at punctuation while preserving the normalized text order.

    The caller supplies the provider/latency budget; this helper does not
    impose a provider-specific limit and never drops source characters.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    normalized = re.sub(r"\r\n?", "\n", text or "").strip()
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]

    boundaries: list[int] = []
    for index, char in enumerate(normalized):
        if char not in _PRIMARY_BOUNDARIES and char not in _SECONDARY_BOUNDARIES:
            continue
        boundary = index + 1
        while boundary < len(normalized) and normalized[boundary] in _CLOSING_MARKS:
            boundary += 1
        boundaries.append(boundary)

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        limit = min(start + max_chars, len(normalized))
        if limit == len(normalized):
            chunks.append(normalized[start:])
            break

        candidates = [boundary for boundary in boundaries if start < boundary <= limit]
        primary = [boundary for boundary in candidates
                   if normalized[boundary - 1] in _PRIMARY_BOUNDARIES
                   or (boundary > 1 and normalized[boundary - 2] in _PRIMARY_BOUNDARIES)]
        boundary = (max(primary or candidates) if (primary or candidates) else limit)
        chunks.append(normalized[start:boundary])
        start = boundary

    return [chunk for chunk in chunks if chunk.strip()]
