from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import field_validator
from sqlmodel import Session, SQLModel, select

from campnames import ensure_camp
from db import get_session
from farmweather import stamp_weather, weather_for
from models import Animal, AnimalSex, AnimalStatus, Event, EventKind
# Re-exported: these lived here first and other modules import them by this
# path. They moved to tags.py so routers/events.py no longer has to import
# this router to get at them, which let this router import from events in
# turn without the two chasing each other in a circle.
from tags import normalise_tag, tag_sort_key  # noqa: F401

router = APIRouter(prefix="/api/animals", tags=["animals"])


# Plain (non-table) input schemas, deliberately not `Animal` itself.
# SQLModel table=True classes don't get full pydantic type coercion on
# their fields - a birth_date sent as "2024-03-15" arrives at SQLAlchemy
# still a str instead of a date, which SQLite's Date type then rejects at
# insert time. A plain SQLModel (table=False) coerces it properly, so the
# request is validated through one of these and then copied into the real
# table model.
class AnimalCreate(SQLModel):
    tag: str
    name: str = ""
    breed: str = ""
    sex: AnimalSex
    birth_date: Optional[date] = None
    sire_tag: Optional[str] = None
    dam_tag: Optional[str] = None
    # Where the animal is when it is entered. Optional, and not a column on
    # the animal: a camp is only ever derived from the event history (see
    # events._current_locations), so this becomes the animal's first
    # movement event. Without it a new animal has no camp at all, is
    # invisible to the Move camp picker, and a herd keyed in from Admin
    # needed one hand-typed movement per head before Move camp worked.
    camp: Optional[str] = None

    @field_validator("tag")
    @classmethod
    def _tag_is_a_tag(cls, value: str) -> str:
        tag = normalise_tag(value)
        if tag is None:
            raise ValueError("a tag number is required")
        return tag

    # Parentage is stored as free text rather than a foreign key (see
    # models.py), so nothing downstream would ever catch a stray space here.
    @field_validator("sire_tag", "dam_tag")
    @classmethod
    def _tidy_parent_tag(cls, value: Optional[str]) -> Optional[str]:
        return normalise_tag(value)


class AnimalUpdate(SQLModel):
    name: Optional[str] = None
    breed: Optional[str] = None
    sex: Optional[AnimalSex] = None
    birth_date: Optional[date] = None
    sire_tag: Optional[str] = None
    dam_tag: Optional[str] = None
    status: Optional[AnimalStatus] = None

    @field_validator("sire_tag", "dam_tag")
    @classmethod
    def _tidy_parent_tag(cls, value: Optional[str]) -> Optional[str]:
        return normalise_tag(value)


def _last_weights(session: Session) -> dict:
    """{animal_id: (value, event_date)} of each animal's most recent
    weighing. One query for the herd, newest first, keeping the first row
    seen per animal - not a query per animal."""
    rows = session.exec(
        select(Event.animal_id, Event.value, Event.event_date)
        .where(Event.kind == EventKind.weight)
        .order_by(Event.event_date.desc(), Event.id.desc())
    ).all()
    latest: dict = {}
    for animal_id, value, on in rows:
        latest.setdefault(animal_id, (value, on))
    return latest


def _with_context(animal: Animal, last_weights: dict, locations: dict, withdrawal: dict) -> dict:
    """An animal as the apps list it: the record, plus where it is and when
    it was last weighed.

    Carried on the list rather than fetched per animal because the Field
    app caches this list for offline use and that is the only copy it has
    out at the crush: the last weight is what lets a 412 typed as 41 be
    noticed before it is saved, and the camp is what the farmer wants to
    see while walking, more than the breed that never changes.
    """
    value, on = last_weights.get(animal.id, (None, None))
    return {**animal.model_dump(), "camp": locations.get(animal.id) or None,
            "last_weight": value, "last_weight_date": on,
            # The last day a treatment's withdrawal still runs, or None: what
            # the list flags and what a Sale is warned about.
            "withdrawal_until": withdrawal.get(animal.id)}


@router.get("")
def list_animals(q: Optional[str] = None, status: Optional[AnimalStatus] = None,
                  session: Session = Depends(get_session)):
    """q matches the start of a tag or anywhere in a name - the two things a
    farmer actually has in hand when they're looking for an animal."""
    # Imported here, not at the top: events imports this module's neighbours
    # and the locations derivation lives with the events it reads.
    from routers.events import _current_locations, withdrawal_until

    statement = select(Animal)
    if status is not None:
        statement = statement.where(Animal.status == status)
    # Sorted here rather than ORDER BY: see tag_sort_key.
    animals = sorted(session.exec(statement).all(), key=lambda a: tag_sort_key(a.tag))
    if q:
        needle = q.strip().lower()
        animals = [a for a in animals if a.tag.lower().startswith(needle) or needle in a.name.lower()]
    last_weights = _last_weights(session)
    locations = _current_locations(session)
    withdrawal = withdrawal_until(session)
    return [_with_context(a, last_weights, locations, withdrawal) for a in animals]


@router.get("/{tag}")
def get_animal(tag: str, session: Session = Depends(get_session)):
    animal = session.exec(select(Animal).where(Animal.tag == tag)).first()
    if animal is None:
        raise HTTPException(404, f"No animal with tag {tag}")
    return animal


@router.post("")
def create_animal(payload: AnimalCreate, session: Session = Depends(get_session)):
    if session.exec(select(Animal).where(Animal.tag == payload.tag)).first() is not None:
        raise HTTPException(409, f"Tag {payload.tag} is already in use")
    animal = Animal(**payload.model_dump(exclude={"camp"}))
    session.add(animal)
    camp_name = (payload.camp or "").strip()
    if camp_name:
        # Dated today, not at the birth date: this records where the animal
        # is as it is entered, not a claim about where it was born. Through
        # ensure_camp so a camp typed on a phone snaps to the spelling in
        # Settings, or is added there if it is new.
        session.flush()  # animal.id, for the event's foreign key
        placed = Event(animal_id=animal.id, kind=EventKind.movement, event_date=date.today(),
                       location=ensure_camp(session, camp_name).name,
                       note="Entered into Kudde")
        stamp_weather(placed, weather_for(session, placed.event_date))
        session.add(placed)
    session.commit()
    session.refresh(animal)
    return animal


@router.patch("/{tag}")
def update_animal(tag: str, payload: AnimalUpdate, session: Session = Depends(get_session)):
    """Partial update - only the fields the client sends are touched, so a
    field-app edit ("mark as sold") can't clobber data an admin edit made
    around the same time."""
    animal = session.exec(select(Animal).where(Animal.tag == tag)).first()
    if animal is None:
        raise HTTPException(404, f"No animal with tag {tag}")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(animal, key, value)
    animal.updated_at = datetime.utcnow()
    session.add(animal)
    session.commit()
    session.refresh(animal)
    return animal
