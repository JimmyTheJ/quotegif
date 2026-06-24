from __future__ import annotations


_SHOW_HINT_INSTRUCTION = """\
The user has confirmed this quote is from {kind} "{title}".
Identify which episode contains it (season and episode number for TV) and the exact verbatim quote.
Do not suggest a different show or movie."""


def build_identify_input(
    quote: str,
    *,
    show_hint: str | None = None,
    movie: bool = False,
) -> str:
    """User message / prompt body for quote identification."""
    lines = [f'Quote: "{quote}"']
    if show_hint:
        kind = "the movie" if movie else "the TV show"
        lines.append(_SHOW_HINT_INSTRUCTION.format(kind=kind, title=show_hint.strip()))
    return "\n\n".join(lines)
