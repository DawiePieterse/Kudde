"""Camp names: how a typed location becomes a row in the Camp table.

A camp used to be nothing but the text typed on a movement event, so
"Noordkamp" and "noordkamp" were two camps and nothing could be picked from
a list. Camps are now set up in Settings and picked from chips - but an
event's location stays plain text, so every event already recorded, and
every one replayed out of a phone's outbox by an older build, keeps
working unchanged. The rules below are what keeps the two in step:

- names are compared ignoring case, in Python rather than SQL, because
  SQLite's case folding mishandles Afrikaans diacritics (Boord Notes
  learnt this first - see its names.py);
- a typed location snaps to the spelling Settings holds;
- a location nobody set up is added to Settings as it is used (the "Other"
  chip in the apps), because a worker at a gate must never be blocked by
  a camp the office has not got round to - the admin tidies it later.
"""
from typing import Optional, Tuple

from sqlmodel import Session, select

from models import Camp


def normalise_camp_name(value: Optional[str]) -> str:
    return " ".join((value or "").split())


def fold(name: str) -> str:
    """The comparison form of a camp name."""
    return normalise_camp_name(name).casefold()


def find_camp(session: Session, name: str) -> Optional[Camp]:
    """The camp called `name`, whatever its capitals, archived or not."""
    wanted = fold(name)
    if not wanted:
        return None
    for camp in session.exec(select(Camp)).all():
        if fold(camp.name) == wanted:
            return camp
    return None


def next_sort_order(session: Session) -> int:
    orders = [c.sort_order for c in session.exec(select(Camp)).all()]
    return (max(orders) + 1) if orders else 0


def ensure_camp(session: Session, name: str) -> Camp:
    """The camp a typed location refers to, created if it is new and
    restored if it had been archived - an animal was just put there, so
    it is plainly in use again. Added to the session, not committed: the
    caller's event and the camp land together or not at all."""
    camp = find_camp(session, name)
    if camp is None:
        camp = Camp(name=normalise_camp_name(name), sort_order=next_sort_order(session))
        session.add(camp)
        session.flush()
    elif camp.archived:
        camp.archived = False
        session.add(camp)
    return camp
