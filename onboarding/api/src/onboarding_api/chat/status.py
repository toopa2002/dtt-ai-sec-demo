"""System-written status of an agent reply while it waits or works (FR-006h, research R21). No model call: the API
knows the queue and the turn's progress line. English only in v1; every string is in STRINGS so a language can be
added without touching the logic (research R12)."""

STRINGS = {
    "en": {
        "received_now": "Received. Starting on it now.",
        "received_next": "Received. I'm finishing {first} first; yours is next.",
        "received_ahead": "Received. {n} messages are ahead of yours.",
        "your_earlier": "your earlier message",
        "their_question": "{name}'s question",
        "working": "Working on it: {progress}",
        "working_plain": "Working on it…",
        "passed_on": "Passed on to {name}.",
    },
}


def _t(key: str, lang: str = "en") -> str:
    return (STRINGS.get(lang) or STRINGS["en"])[key]


def received_text(ahead: int, first_name: str | None, same_writer: bool, lang: str = "en") -> str:
    """`ahead` messages are before this one; `first_name` wrote the one being answered now (None if unknown)."""
    if ahead <= 0:
        return _t("received_now", lang)
    if ahead == 1:
        first = _t("your_earlier", lang) if same_writer or not first_name else \
            _t("their_question", lang).format(name=first_name)
        return _t("received_next", lang).format(first=first)
    return _t("received_ahead", lang).format(n=ahead)


def working_text(progress: str | None, lang: str = "en") -> str:
    if progress:
        return _t("working", lang).format(progress=progress.rstrip("…. ") + "…")
    return _t("working_plain", lang)


def passed_on_text(name: str, lang: str = "en") -> str:
    return _t("passed_on", lang).format(name=name)
