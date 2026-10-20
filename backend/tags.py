"""Ear-tag helpers shared by every router.

A module of their own rather than a corner of routers/animals.py, so the
routers can all import them without importing one another.
"""
import re
from typing import List, Optional, Tuple, Union


def normalise_tag(value: Optional[str]) -> Optional[str]:
    """An ear tag as it should be stored: trimmed, or None if there is
    nothing left.

    The tag is this app's identity key - it is what the unique index is on,
    what every event is recorded against, and what a farmer reads off the
    animal. Both front ends already .trim() before sending, so nothing
    normal reaches here untrimmed; what does is the offline outbox replaying
    an entry captured by an older build, and any other client. Without this
    " A1 " lands beside "A1" as a second animal that looks identical on
    every screen and cannot be told apart, and "" lands as an animal with no
    tag at all - both confirmed against the API before this was added.
    """
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


_TAG_RUNS = re.compile(r"(\d+)")


def tag_sort_key(tag: Optional[str]) -> List[Tuple[int, Union[int, str]]]:
    """Sort key that reads a tag the way a farmer does: B2 before B10, A20
    before A100.

    SQLite's ORDER BY compares text character by character, so B10 sorted
    between B1 and B2 and any herd numbered past nine never read in order -
    on the Field list, in the Move camp picker and on the dashboard alike.
    Each run of digits becomes a number and each run of letters is compared
    ignoring case; a numeric run sorts before a text run at the same position
    so "12" and "A" can't be compared int-to-str. frontend/shared/api.js
    compareTags() applies the same rule client-side, and the two must agree.
    """
    key: List[Tuple[int, Union[int, str]]] = []
    for part in _TAG_RUNS.split(tag or ""):
        if not part:
            continue
        key.append((0, int(part)) if part.isdigit() else (1, part.lower()))
    return key
