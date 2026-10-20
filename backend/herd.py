"""What the herd looks like right now, derived from the records.

Kudde stores an animal's identity and its events, nothing else: where an
animal is, what it last weighed, whether it is still in withdrawal and
whether a cow has calved are all read off the event history every time.
This module is the one place those derivations live, so the animal list,
the dashboard, the CSV export, the camp pickers and the camps settings all
read the same answer from the same code rather than each router keeping
its own copy (which is how the dashboard came to have a second "last
weighed" query that could disagree on a tie).

Everything here takes a Session and returns plain dicts keyed by
animal.id; herd_context() bundles the lot for a request that needs them
all, loading the Animal table once for the three derivations that read it.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Set, Tuple

from fastapi import HTTPException
from sqlmodel import Session, select

from campnames import fold
from classes import CALF_UNTIL_DAYS, animal_class, calved_animal_ids  # noqa: F401 - CALF_UNTIL_DAYS re-exported for callers
from models import Animal, AnimalStatus, Camp, Event, EventKind
from tags import normalise_tag, tag_sort_key

# Only movement and birth events place an animal somewhere the herd grazes;
# a weight or treatment event's location, if ever set, is incidental to
# where it happened, not a place the animal is considered to live.
LOCATION_EVENT_KINDS = [EventKind.movement, EventKind.birth]


def animal_by_tag(session: Session, tag: str) -> Animal:
    """The animal with this ear tag, or a 404 the apps show as it is."""
    animal = session.exec(select(Animal).where(Animal.tag == normalise_tag(tag))).first()
    if animal is None:
        raise HTTPException(404, f"No animal with tag {tag}")
    return animal


def animals_by_tags(session: Session, tags: List[str]) -> List[Animal]:
    """The animals for a batch of tags, in the order given, all or nothing:
    one query, and a 404 naming the first tag that does not exist, so a
    camp move or a group treatment never half-lands."""
    wanted = [normalise_tag(t) for t in tags]
    found = {a.tag: a for a in session.exec(select(Animal).where(Animal.tag.in_(wanted))).all()}
    for tag in wanted:
        if tag not in found:
            raise HTTPException(404, f"No animal with tag {tag}")
    return [found[tag] for tag in wanted]


def current_locations(session: Session) -> Dict[int, str]:
    """Latest known camp per animal_id, from the movement and birth events
    that recorded one. There is no stored current-camp field; this is
    always derived from the history."""
    rows = session.exec(
        select(Event.animal_id, Event.location)
        .where(Event.kind.in_(LOCATION_EVENT_KINDS))
        .where(Event.location != "")
        .order_by(Event.animal_id, Event.event_date.desc(), Event.id.desc())
    ).all()
    latest: Dict[int, str] = {}
    for animal_id, location in rows:
        latest.setdefault(animal_id, location)  # first row per animal is the latest, by the order_by
    return latest


def last_weights(session: Session) -> Dict[int, Tuple[float, date]]:
    """{animal_id: (kg, date)} of each animal's most recent weighing. One
    query for the herd, newest first, keeping the first row per animal."""
    rows = session.exec(
        select(Event.animal_id, Event.value, Event.event_date)
        .where(Event.kind == EventKind.weight)
        .order_by(Event.event_date.desc(), Event.id.desc())
    ).all()
    latest: Dict[int, Tuple[float, date]] = {}
    for animal_id, value, on in rows:
        latest.setdefault(animal_id, (value, on))
    return latest


def withdrawal_until(session: Session) -> Dict[int, date]:
    """{animal_id: date} of the last day each animal is still inside a
    treatment's withdrawal period, for those that are. The longest of any
    overlapping treatments wins: an animal dosed twice is clear when the
    later-ending one is."""
    today = date.today()
    rows = session.exec(
        select(Event.animal_id, Event.event_date, Event.withdrawal_days)
        .where(Event.kind == EventKind.treatment)
        .where(Event.withdrawal_days.is_not(None))
    ).all()
    until: Dict[int, date] = {}
    for animal_id, on, days in rows:
        end = on + timedelta(days=days)
        if end >= today and end > until.get(animal_id, date.min):
            until[animal_id] = end
    return until


@dataclass
class HerdContext:
    animals: List[Animal]            # every animal, whatever its status, in tag order
    locations: Dict[int, str]
    weights: Dict[int, Tuple[float, date]]
    withdrawal: Dict[int, date]
    calved: Set[int]

    @property
    def alive(self) -> List[Animal]:
        return [a for a in self.animals if a.status is AnimalStatus.alive]

    def describe(self, animal: Animal) -> dict:
        """An animal as the apps list it: the record, plus where it is, when
        it was last weighed, its withdrawal and its class.

        Carried on the list rather than fetched per animal because the Field
        app caches this list for offline use and that is the only copy it
        has out at the crush: the last weight is what lets a 412 typed as
        41 be noticed before it is saved, and the camp is what the farmer
        wants to see while walking, more than the breed that never changes.
        """
        value, on = self.weights.get(animal.id, (None, None))
        return {**animal.model_dump(), "camp": self.locations.get(animal.id) or None,
                "last_weight": value, "last_weight_date": on,
                "withdrawal_until": self.withdrawal.get(animal.id),
                "class": animal_class(animal, animal.id in self.calved)}


def herd_context(session: Session) -> HerdContext:
    animals = sorted(session.exec(select(Animal)).all(), key=lambda a: tag_sort_key(a.tag))
    return HerdContext(
        animals=animals,
        locations=current_locations(session),
        weights=last_weights(session),
        withdrawal=withdrawal_until(session),
        calved=calved_animal_ids(session, animals),
    )


def camp_groups(session: Session, alive: List[Animal], locations: Dict[int, str]) -> List[Tuple[Optional[str], List[Animal]]]:
    """The alive herd camp by camp, as both the Move picker and the
    dashboard show it: every active camp in Settings order (empty ones
    included, so the farmer sees the whole farm), then any camp only the
    history knows (older records, or a replay from before the list
    existed), then None for the animals with no camp recorded at all, so
    they can be moved INTO one. Members are in tag order."""
    camps = session.exec(select(Camp).order_by(Camp.sort_order, Camp.id)).all()
    names: Dict[str, Optional[str]] = {fold(c.name): c.name for c in camps if not c.archived}
    members: Dict[str, List[Animal]] = {key: [] for key in names}
    unplaced: List[Animal] = []
    for animal in alive:
        location = locations.get(animal.id)
        if not location:
            unplaced.append(animal)
            continue
        key = fold(location)
        names.setdefault(key, location.strip())
        members.setdefault(key, []).append(animal)
    managed = [k for k in members if k in {fold(c.name) for c in camps if not c.archived}]
    stray = sorted(k for k in members if k not in managed)
    by_tag = lambda a: tag_sort_key(a.tag)  # noqa: E731
    groups = [(names[k], sorted(members[k], key=by_tag)) for k in managed + stray]
    if unplaced:
        groups.append((None, sorted(unplaced, key=by_tag)))
    return groups
