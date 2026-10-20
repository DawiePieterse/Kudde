"""What kind of animal this is, in the words a farmer uses.

A farmer does not think in "female" and "male": the herd is cows, heifers,
bulls, steers and calves, and those are what get counted, filtered and
sold. None of that needs a new field to type - it follows from what is
recorded already, plus one flag (castrated), so it is derived here rather
than stored, and can never disagree with the record it comes from.

The rules, deliberately simple:

- under a year old (by birth date) it is a calf: heifer calf or bull calf,
  or a steer calf once castrated;
- a male is a bull, or a steer if castrated;
- a female is a cow once she has calved, and a heifer until then - a
  heifer stays a heifer however old she is, which is what the word means;
- no birth date reads as grown: an animal entered without one is nearly
  always a bought-in adult, not a calf nobody dated.

"Has calved" is read from the records: a calf with her as dam (the calving
screen writes that), or a birth event on her dated well after her own
birth - a birth event dated at her birth date is her own.
"""
from datetime import date, timedelta
from typing import Dict, Set

from sqlmodel import Session, select

from models import Animal, AnimalSex, Event, EventKind

CALF_UNTIL_DAYS = 365
# A birth event this long after her own birth is a calving, not her birth.
# Nothing calves at ten months; a date error of a few months still reads
# right either way.
_OWN_BIRTH_WINDOW = timedelta(days=300)

# Every class the apps can show, in the order a herd summary lists them.
CLASSES = ["cow", "heifer", "bull", "steer", "heifer calf", "bull calf", "steer calf"]


def animal_class(animal: Animal, calved: bool, today: date = None) -> str:
    today = today or date.today()
    is_calf = animal.birth_date is not None and (today - animal.birth_date).days < CALF_UNTIL_DAYS
    if animal.sex is AnimalSex.female:
        if is_calf:
            return "heifer calf"
        return "cow" if calved else "heifer"
    base = "steer" if animal.castrated else "bull"
    return f"{base} calf" if is_calf else base


def calved_animal_ids(session: Session) -> Set[int]:
    """The animals with a calving on record, by either sign of one."""
    animals = session.exec(select(Animal)).all()
    by_tag: Dict[str, Animal] = {a.tag.casefold(): a for a in animals}
    calved: Set[int] = set()
    for a in animals:
        if a.dam_tag and a.dam_tag.casefold() in by_tag:
            calved.add(by_tag[a.dam_tag.casefold()].id)
    births = session.exec(
        select(Event.animal_id, Event.event_date).where(Event.kind == EventKind.birth)).all()
    birth_date = {a.id: a.birth_date for a in animals}
    for animal_id, on in births:
        own = birth_date.get(animal_id)
        if own is not None and on > own + _OWN_BIRTH_WINDOW:
            calved.add(animal_id)
    return calved
